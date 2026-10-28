@echo off
rem Builds a stand-alone Windows app in dist\AurumLIMS\AurumLIMS.exe
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo Run scripts\run.bat once first to set up the Python environment.
    exit /b 1
)

".venv\Scripts\python.exe" -m PyInstaller ^
    --noconfirm --clean --windowed ^
    --name AurumLIMS ^
    --specpath build ^
    --paths src ^
    --add-data "%CD%\src\aurum_lims\migrations;aurum_lims\migrations" ^
    --add-data "%CD%\src\aurum_lims\ui\theme.qss;aurum_lims\ui" ^
    --hidden-import alembic.op ^
    --hidden-import alembic.context ^
    --hidden-import alembic.ddl.sqlite ^
    --exclude-module pytest ^
    src\aurum_lims\__main__.py || goto :error

echo.
echo Built dist\AurumLIMS\AurumLIMS.exe
exit /b 0

:error
echo Build failed.
exit /b 1
