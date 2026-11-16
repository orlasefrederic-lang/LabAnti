@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if "%~1"=="" (
    echo Перетащите на этот файл фото, видео или аудио — я сделаю копию
    echo без приватных данных ^(GPS, дат, серийного номера, модели камеры и автора^)
    echo с пометкой _privacy в имени, но сохраню ориентацию, заголовок и копирайт.
    echo.
    pause
    exit /b 0
)

set "PY=py"
where py >nul 2>nul
if not %errorlevel% equ 0 set "PY=python"

:loop
if "%~1"=="" goto done
set "SRC=%~1"
set "DIR=%~dp1"
set "BASE=%~n1"
set "EXT=%~x1"
echo Очищаю приватные данные: %~nx1
%PY% -m mediameta remove "%SRC%" --privacy --output "%DIR%%BASE%_privacy%EXT%"
shift
goto loop

:done
echo.
pause
