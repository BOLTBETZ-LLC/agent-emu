# Set the balloon size (bytes) of the Device whose control pipe is \\.\pipe\<name>.
param([string]$Bytes, [string]$Name = "ae-vm")
& "C:\dev\agent-emu-work\crosvm\target\release\crosvm.exe" balloon $Bytes "\\.\pipe\$Name" --wait 2>&1 | Select-Object -Last 3
