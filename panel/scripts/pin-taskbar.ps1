# Crea accesos directos (Escritorio + Menú Inicio) al panel ya construido y trata de
# anclarlo a la barra de tareas. En Windows 11 el anclaje programático suele estar
# bloqueado por Microsoft; si no se puede, deja el acceso directo y avisa de cómo
# anclarlo con un clic derecho.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot          # ...\panel
$exe  = Join-Path $root "dist\win-unpacked\ECONOSIM Panel.exe"
if (-not (Test-Path $exe)) { throw "No existe el ejecutable: $exe (corre 'npm run dist' primero)" }

$WScript = New-Object -ComObject WScript.Shell
function New-Shortcut($path) {
  $lnk = $WScript.CreateShortcut($path)
  $lnk.TargetPath = $exe
  $lnk.WorkingDirectory = Split-Path -Parent $exe
  $lnk.IconLocation = "$exe,0"
  $lnk.Description = "ECONOSIM Panel"
  $lnk.Save()
}

# 1) Acceso directo en Escritorio y en el Menú Inicio (para que sea buscable/anclable)
$desktop = [Environment]::GetFolderPath("Desktop")
$startmenu = Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs"
$deskLnk = Join-Path $desktop "ECONOSIM Panel.lnk"
$startLnk = Join-Path $startmenu "ECONOSIM Panel.lnk"
New-Shortcut $deskLnk
New-Shortcut $startLnk
Write-Host "OK accesos directos: $deskLnk ; $startLnk"

# 2) Intento de anclaje a la barra de tareas por verbo de shell (localizado)
$pinned = $false
try {
  $shell = New-Object -ComObject Shell.Application
  $folder = $shell.Namespace((Split-Path -Parent $exe))
  $item = $folder.ParseName((Split-Path -Leaf $exe))
  foreach ($v in $item.Verbs()) {
    $n = $v.Name -replace '&',''
    if ($n -match 'barra de tareas' -or $n -match 'taskbar') { $v.DoIt(); $pinned = $true; break }
  }
} catch { }

if ($pinned) {
  Write-Host "PINNED: anclado a la barra de tareas."
} else {
  Write-Host "PIN_MANUAL: Windows no permite anclar por script en esta version."
  Write-Host "  Haz clic derecho en 'ECONOSIM Panel' (Escritorio o Inicio) -> Anclar a la barra de tareas."
}
