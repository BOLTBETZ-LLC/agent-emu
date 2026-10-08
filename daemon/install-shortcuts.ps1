# Creates "agent-emu" shortcuts (Desktop and Start Menu) that run agent-emu.cmd from this checkout.
$cmd = Join-Path (Split-Path $PSScriptRoot -Parent) "agent-emu.cmd"
$sh = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))) {
  $lnk = $sh.CreateShortcut((Join-Path $dir "agent-emu.lnk"))
  $lnk.TargetPath = $cmd
  $lnk.WorkingDirectory = Split-Path $cmd -Parent
  $lnk.WindowStyle = 7
  $lnk.IconLocation = "$env:SystemRoot\System32\shell32.dll,15"
  $lnk.Description = "agent-emu control panel"
  $lnk.Save()
  "created " + $lnk.FullName
}
