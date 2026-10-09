# Image pack for the first-run download: zips dist\agent-emu\images (paths relative to images\) and writes
# manifest.json {version, files:[{name, size, sha256}]} beside it. Upload both to the CloudFront-fronted bucket
# (bucket policy + CloudFront, no IAM role) and set package.json agentEmu.imageManifest to the manifest URL.
param([string]$Version = (Get-Date -Format "yyyy-MM-dd"), [string]$Out = "")
$ErrorActionPreference = "Stop"
$Repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Images = Join-Path $Repo "dist\agent-emu\images"
if (-not $Out) { $Out = Join-Path $Repo "dist\image-pack" }
New-Item -ItemType Directory -Force $Out | Out-Null
$name = "agent-emu-images-$Version.zip"
$zip = Join-Path $Out $name
if (Test-Path $zip) { Remove-Item $zip }
& "$env:SystemRoot\System32\tar.exe" -a -c -f $zip -C $Images .
if ($LASTEXITCODE -ne 0) { throw "tar failed ($LASTEXITCODE)" }
$f = Get-Item $zip
$m = @{ version = $Version; files = @(@{ name = $name; size = $f.Length; sha256 = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower() }) }
$m | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 (Join-Path $Out "manifest.json")
"{0}  {1:N2} GB" -f $zip, ($f.Length / 1GB)
