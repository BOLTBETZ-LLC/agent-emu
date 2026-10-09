# Publishes the first-run download feed: Python (embeddable), PyYAML wheel, pict.exe, Temurin JRE, bundletool and the base image pack
# (pack-images.ps1) to the agent-emu-dist bucket behind CloudFront (infra\dist-cdn.yml), plus manifest.json.
# Third-party files are checked against their publisher's sha256 before upload.
param([string]$Images = "", [string]$Version = (Get-Date -Format "yyyy-MM-dd"), [string]$Profile = "boltbetz",
  [string]$Bucket = "agent-emu-dist-670246014949", [string]$Prefix = "feed/v1")
$ErrorActionPreference = "Stop"
$Feed = Join-Path (Split-Path $PSScriptRoot -Parent) "payload\feed"
New-Item -ItemType Directory -Force $Feed | Out-Null
$deps = @(
  @{ name = "python"; file = "python-3.14.7-embed-amd64.zip"; unpack = "zip"; dest = "python"
     url = "https://www.python.org/ftp/python/3.14.7/python-3.14.7-embed-amd64.zip"
     sha256 = "d297e5ff019966817ad8502465176139f2d3d840fa4ed84b13bed399a6ab1f15" },   # python.org sigstore digest
  @{ name = "pyyaml"; file = "pyyaml-6.0.3-cp314-cp314-win_amd64.whl"; unpack = "zip"; dest = "python/Lib/site-packages"
     url = "https://files.pythonhosted.org/packages/23/20/bb6982b26a40bb43951265ba29d4c246ef0ff59c9fdcdf0ed04e0687de4d/pyyaml-6.0.3-cp314-cp314-win_amd64.whl"
     sha256 = "4a2e8cebe2ff6ab7d1050ecd59c25d4c8bd7e6f400f5f82b96557ac0abafd0ac" },   # PyPI digest
  @{ name = "pict"; file = "pict.exe"; unpack = "file"; dest = "bin/pict.exe"
     url = "https://github.com/microsoft/pict/releases/download/v3.7.4/pict.exe"
     sha256 = "80aba862739cf18b4faa13d408163324d188a1c4efccdd977d9c5ba3f8950bbd" },   # official v3.7.4 release asset
  @{ name = "jre"; file = "OpenJDK21U-jre_x64_windows_hotspot_21.0.12.1_1.zip"; unpack = "zip"; dest = "jre"; strip = 1
     url = "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.12.1%2B1/OpenJDK21U-jre_x64_windows_hotspot_21.0.12.1_1.zip"
     sha256 = "d35f31e712f0fcf6ac5a093edc90204fbff22f720ba3950bd09d331d5e621636" },   # Adoptium API checksum (Temurin 21 JRE)
  @{ name = "bundletool"; file = "bundletool-all-1.18.3.jar"; unpack = "file"; dest = "bin/bundletool.jar"
     url = "https://github.com/google/bundletool/releases/download/1.18.3/bundletool-all-1.18.3.jar"
     sha256 = "a099cfa1543f55593bc2ed16a70a7c67fe54b1747bb7301f37fdfd6d91028e29" }    # GitHub release asset digest
)
if (-not $Images) { $Images = Get-ChildItem $Feed -Filter "agent-emu-images-*.zip" | Sort-Object Name | Select-Object -Last 1 -ExpandProperty FullName }
if (-not $Images) { throw "no image pack: run pack-images.ps1 first" }
$comps = @()
foreach ($d in $deps) {
  $p = Join-Path $Feed $d.file
  if (-not (Test-Path $p)) { Invoke-WebRequest $d.url -OutFile $p }
  if ((Get-FileHash $p -Algorithm SHA256).Hash.ToLower() -ne $d.sha256) { Remove-Item $p; throw "$($d.file): sha256 mismatch" }
  $c = [ordered]@{ name = $d.name; file = $d.file; size = (Get-Item $p).Length; sha256 = $d.sha256; unpack = $d.unpack; dest = $d.dest }
  if ($d.strip) { $c.strip = $d.strip }
  $comps += $c
}
$img = Get-Item $Images
$comps += [ordered]@{ name = "images"; file = $img.Name; size = $img.Length; sha256 = (Get-FileHash $img.FullName -Algorithm SHA256).Hash.ToLower(); unpack = "zip"; dest = "images" }
$json = [ordered]@{ version = $Version; components = $comps } | ConvertTo-Json -Depth 4
[IO.File]::WriteAllText((Join-Path $Feed "manifest.json"), $json, (New-Object Text.UTF8Encoding $false))   # no BOM
foreach ($c in $comps) {
  # Same name and size already in the bucket = already published (names carry versions).
  $ErrorActionPreference = "Continue"
  $have = aws s3api head-object --profile $Profile --bucket $Bucket --key "$Prefix/$($c.file)" --query ContentLength --output text 2>$null
  $ErrorActionPreference = "Stop"
  if ("$have" -eq "$($c.size)") { "skip $($c.file) (already published)"; continue }
  aws s3 cp --profile $Profile --no-progress (Join-Path $Feed $c.file) "s3://$Bucket/$Prefix/$($c.file)"
  if ($LASTEXITCODE -ne 0) { throw "upload $($c.file) failed" }
}
aws s3 cp --profile $Profile --no-progress --content-type application/json --cache-control "max-age=60" (Join-Path $Feed "manifest.json") "s3://$Bucket/$Prefix/manifest.json"
if ($LASTEXITCODE -ne 0) { throw "upload manifest failed" }
"published $Prefix/manifest.json ($Version)"
