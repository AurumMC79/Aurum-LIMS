# Creates an "Aurum LIMS" shortcut on the desktop (and optionally in the Start menu)
# that starts the app from this project folder, without a console window.
#
#   powershell -ExecutionPolicy Bypass -File scripts\create_shortcut.ps1 [-StartMenu]

param([switch]$StartMenu)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$icon = Join-Path $root "src\aurum_lims\ui\aurum.ico"

if (-not (Test-Path $pythonw)) {
    Write-Host "The Python environment is not set up yet. Run scripts\run.bat once first." -ForegroundColor Red
    exit 1
}

$shell = New-Object -ComObject WScript.Shell
$targets = @([Environment]::GetFolderPath("Desktop"))
if ($StartMenu) { $targets += [Environment]::GetFolderPath("Programs") }

foreach ($folder in $targets) {
    $path = Join-Path $folder "Aurum LIMS.lnk"
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = $pythonw
    $link.Arguments = "-m aurum_lims"
    $link.WorkingDirectory = $root
    $link.IconLocation = "$icon,0"
    $link.Description = "Aurum LIMS - Laboratory Information Management System"
    $link.Save()
    Write-Host "Created $path"
}
