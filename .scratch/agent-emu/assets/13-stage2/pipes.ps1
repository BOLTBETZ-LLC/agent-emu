# List named pipes whose names contain the given text.
param([string]$Match = "ae")
[System.IO.Directory]::GetFiles("\\.\pipe\") | Where-Object { $_ -like "*$Match*" }
