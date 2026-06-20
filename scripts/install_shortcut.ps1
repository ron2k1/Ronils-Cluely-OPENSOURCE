# Creates a console-less Cluely launcher on the Desktop and in the Start Menu.
# Run it once from a clone: powershell -ExecutionPolicy Bypass -File scripts\install_shortcut.ps1
$ErrorActionPreference = 'Stop'
# This script lives in scripts/, so the repo root is its parent. Deriving the
# base at runtime means the shortcut works from whatever path you cloned into.
$base   = Split-Path -Parent $PSScriptRoot
$target = Join-Path $base '.venv\Scripts\pythonw.exe'
$icon   = Join-Path $base 'assets\cluely.ico'
if (-not (Test-Path $target)) { throw "pythonw not found: $target" }
if (-not (Test-Path $icon))   { throw "icon not found: $icon" }

$dests = @(
  (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Cluely.lnk'),
  (Join-Path ([Environment]::GetFolderPath('Programs')) 'Cluely.lnk')
)
$ws = New-Object -ComObject WScript.Shell
foreach ($lnk in $dests) {
  $sc = $ws.CreateShortcut($lnk)
  $sc.TargetPath       = $target
  $sc.Arguments        = 'src\main.py'
  $sc.WorkingDirectory = $base
  $sc.IconLocation     = "$icon,0"
  $sc.Description       = 'Cluely - capture-invisible meeting overlay'
  $sc.WindowStyle       = 1
  $sc.Save()
  Write-Output "created $lnk"
}
