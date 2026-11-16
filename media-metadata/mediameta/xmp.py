"""Read, update and build XMP packets (Lightroom, Photoshop, Bridge, etc.).

In 1.0, XMP was read-only, which meant deleting or changing ``title`` on a
file tagged by Lightroom left the old value inside the XMP packet — and
reading the file back resurrected the deleted value. In 2.0G, existing XMP
packets are kept in sync with canonical edits and ``XMP:...`` raw tags can
be written natively without ExifTool.
"""

from __future__ import annotations

import datetime as _dt
import re
from xml.etree import ElementTree

NAMESPACES = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "xmp": "http://ns.adobe.com/xap/1.0/",
    "photoshop": "http://ns.adobe.com/photoshop/1.0/",
    "tiff": "http://ns.adobe.com/tiff/1.0/",
    "exif": "http://ns.adobe.com/exif/1.0/",
    "exifEX": "http://cipa.jp/exif/1.0/",
    "aux": "http://ns.adobe.com/exif/1.0/aux/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
}

for _prefix, _uri in NAMESPACES.items():
    ElementTree.register_namespace(_prefix, _uri)

_PACKET_RE = re.compile(rb"<x:xmpmeta.*?</x:xmpmeta>", re.DOTALL)

#: XMP property -> canonical field name (priority order).
INTERESTING = {
    ("dc", "title"): "title",
    ("dc", "description"): "description",
    ("dc", "creator"): "artist",
    ("dc", "rights"): "copyright",
    ("dc", "subject"): "keywords",
    ("xmp", "Rating"): "rating",
    ("xmp", "CreateDate"): "datetime_original",
    ("xmp", "CreatorTool"): "software",
    ("photoshop", "DateCreated"): "datetime_original",
    ("tiff", "Make"): "camera_make",
    ("tiff", "Model"): "camera_model",
    ("exifEX", "LensModel"): "lens",
    ("aux", "Lens"): "lens",
    ("exifEX", "LensMake"): "lens_make",
    ("exifEX", "BodySerialNumber"): "serial_number",
    ("aux", "SerialNumber"): "serial_number",
}

#: Canonical field -> XMP keys that represent it. First key is primary for writes.
FIELD_TO_XMP = {
    "title": (("dc", "title"),),
    "description": (("dc", "description"),),
    "artist": (("dc", "creator"),),
    "copyright": (("dc", "rights"),),
    "keywords": (("dc", "subject"),),
    "rating": (("xmp", "Rating"),),
    "datetime_original": (("xmp", "CreateDate"), ("photoshop", "DateCreated")),
    "software": (("xmp", "CreatorTool"),),
    "camera_make": (("tiff", "Make"),),
    "camera_model": (("tiff", "Model"),),
    "lens": (("exifEX", "LensModel"), ("aux", "Lens")),
    "lens_make": (("exifEX", "LensMake"),),
    "serial_number": (("exifEX", "BodySerialNumber"), ("aux", "SerialNumber")),
}

_ALT_PROPERTIES = {("dc", "title"), ("dc", "description"), ("dc", "rights")}
_SEQ_PROPERTIES = {("dc", "creator")}
_BAG_PROPERTIES = {("dc", "subject")}


def extract_packet(data: bytes):
    """Find a raw XMP packet inside arbitrary file bytes."""
    match = _PACKET_RE.search(data)
    return match.group(0) if match else None


def parse(packet) -> dict:
    """Parse an XMP packet into ``{"dc:title": value, ...}``."""
    if packet is None:
        return {}
    if isinstance(packet, str):
        packet = packet.encode("utf-8")

    try:
        root = ElementTree.fromstring(packet)
    except ElementTree.ParseError:
        return {}

    reverse = {uri: prefix for prefix, uri in NAMESPACES.items()}
    found: dict = {}

    for element in root.iter():
        if not element.tag.startswith("{"):
            continue
        uri, _, local = element.tag[1:].partition("}")
        prefix = reverse.get(uri)
        if prefix is None or prefix == "rdf":
            continue
        value = _element_value(element)
        if value not in (None, "", []):
            found[f"{prefix}:{local}"] = value

    # Attributes carry properties too when the file uses compact RDF.
    for element in root.iter():
        for name, value in element.attrib.items():
            if not name.startswith("{"):
                continue
            uri, _, local = name[1:].partition("}")
            prefix = reverse.get(uri)
            if prefix and prefix != "rdf" and value:
                found.setdefault(f"{prefix}:{local}", value)

    return found


def _element_value(element):
    """Flatten ``rdf:Alt`` / ``rdf:Bag`` / ``rdf:Seq`` wrappers into values."""
    rdf = NAMESPACES["rdf"]
    container = None
    for name in ("Alt", "Bag", "Seq"):
        container = element.find(f"{{{rdf}}}{name}")
        if container is not None:
            break

    if container is not None:
        items = [
            (item.text or "").strip()
            for item in container.findall(f"{{{rdf}}}li")
        ]
        items = [item for item in items if item]
        if not items:
            return None
        return items[0] if len(items) == 1 else items

    text = (element.text or "").strip()
    return text or None


def to_common(properties: dict) -> dict:
    """Translate parsed XMP properties into canonical fields."""
    common: dict = {}
    for (prefix, local), field_name in INTERESTING.items():
        value = properties.get(f"{prefix}:{local}")
        if value in (None, "", []):
            continue
        if field_name == "keywords":
            if isinstance(value, str):
                value = [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]
            elif isinstance(value, (list, tuple)):
                value = [str(part).strip() for part in value if str(part).strip()]
        elif field_name != "keywords" and isinstance(value, list):
            value = ", ".join(str(part) for part in value)
        common.setdefault(field_name, value)
    return common


def update_packet(
    packet: bytes | str | None,
    changes: dict | None = None,
    raw_xmp: dict | None = None,
    create: bool = False,
) -> bytes | None:
    """Update an existing XMP packet (or build a new one if ``create=True``).

    Returns ``None`` if there was no packet and nothing required creating one,
    or if every property in the packet was deleted.
    """
    props = parse(packet) if packet else {}
    if not props and not create and not raw_xmp:
        return None

    for field_name, value in (changes or {}).items():
        targets = FIELD_TO_XMP.get(field_name)
        if not targets:
            continue
        if value is None or value == "" or value == []:
            for prefix, local in targets:
                props.pop(f"{prefix}:{local}", None)
        else:
            # Update the primary XMP property and drop secondary aliases so they don't conflict
            primary = f"{targets[0][0]}:{targets[0][1]}"
            for prefix, local in targets[1:]:
                props.pop(f"{prefix}:{local}", None)
            props[primary] = _format_xmp_value(field_name, value)

    for key, value in (raw_xmp or {}).items():
        clean_key = key[4:] if key.lower().startswith("xmp:") else key
        if ":" not in clean_key:
            clean_key = f"xmp:{clean_key}"
        prefix, _, local = clean_key.partition(":")
        # Normalize known prefix case
        for known in NAMESPACES:
            if known.lower() == prefix.lower():
                prefix = known
                break
        full_key = f"{prefix}:{local}"
        if value is None or value == "":
            props.pop(full_key, None)
        else:
            props[full_key] = value

    if not props:
        return None
    return build_packet(props)


def _format_xmp_value(field_name: str, value):
    if isinstance(value, _dt.datetime):
        return value.isoformat()
    if field_name == "keywords":
        if isinstance(value, (list, tuple)):
            return [str(item).strip() for item in value if str(item).strip()]
        return [part.strip() for part in str(value).replace(";", ",").split(",") if part.strip()]
    return value


def build_packet(properties: dict) -> bytes:
    """Build a standard ``<x:xmpmeta>`` RDF XML packet from ``{"dc:title": ...}``."""
    rdf_uri = NAMESPACES["rdf"]
    xml_ns = 'xmlns:x="adobe:ns:meta/"'
    used_prefixes = {"rdf"}
    for key in properties:
        prefix, _, _ = key.partition(":")
        if prefix in NAMESPACES:
            used_prefixes.add(prefix)

    ns_decls = " ".join(
        f'xmlns:{prefix}="{NAMESPACES[prefix]}"'
        for prefix in sorted(used_prefixes)
        if prefix != "rdf"
    )
    rdf_decl = f'xmlns:rdf="{rdf_uri}"'

    body_lines = []
    for key, value in properties.items():
        if value in (None, "", []):
            continue
        prefix, _, local = key.partition(":")
        if prefix not in NAMESPACES:
            continue
        pair = (prefix, local)
        tag = f"{prefix}:{local}"
        if pair in _ALT_PROPERTIES:
            items = value if isinstance(value, (list, tuple)) else [value]
            li = "".join(
                f'<rdf:li xml:lang="x-default">{_escape_xml(str(item))}</rdf:li>'
                for item in items
            )
            body_lines.append(f"      <{tag}><rdf:Alt>{li}</rdf:Alt></{tag}>")
        elif pair in _SEQ_PROPERTIES:
            items = value if isinstance(value, (list, tuple)) else [value]
            li = "".join(f"<rdf:li>{_escape_xml(str(item))}</rdf:li>" for item in items)
            body_lines.append(f"      <{tag}><rdf:Seq>{li}</rdf:Seq></{tag}>")
        elif pair in _BAG_PROPERTIES or isinstance(value, (list, tuple)):
            items = value if isinstance(value, (list, tuple)) else [value]
            li = "".join(f"<rdf:li>{_escape_xml(str(item))}</rdf:li>" for item in items)
            body_lines.append(f"      <{tag}><rdf:Bag>{li}</rdf:Bag></{tag}>")
        else:
            body_lines.append(f"      <{tag}>{_escape_xml(str(value))}</{tag}>")

    desc_attrs = f'rdf:about=""' + (f" {ns_decls}" if ns_decls else "")
    inner = "\n".join(body_lines)
    packet = (
        f'<x:xmpmeta {xml_ns}>\n'
        f'  <rdf:RDF {rdf_decl}>\n'
        f'    <rdf:Description {desc_attrs}>\n'
        f'{inner}\n'
        f'    </rdf:Description>\n'
        f'  </rdf:RDF>\n'
        f'</x:xmpmeta>'
    )
    return packet.encode("utf-8")


def _escape_xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
