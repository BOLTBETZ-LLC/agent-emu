param([string]$Log = 'C:\dev\agent-emu-work\redroid\host.log')
# Every 30 s: log host Available + vmmemWSL; if Available < 3000 MB stop the newest rr* container.
while ($true) {
  $line = & powershell -NoProfile -ExecutionPolicy Bypass -File 'C:\dev\agent-emu-work\redroid\hostmem.ps1'
  $n = (& wsl -d Ubuntu -- docker ps --format '{{.Names}}' 2>$null | Where-Object { $_ -like 'rr*' }) -join ','
  Add-Content $Log "$line rr=$n"
  if ($line -match 'avail=(\d+)' -and [int]$Matches[1] -lt 3000 -and $n) {
    $newest = & wsl -d Ubuntu -- docker ps --latest --filter name=rr --format '{{.Names}}'
    & wsl -d Ubuntu -- docker rm -f $newest | Out-Null
    Add-Content $Log "GUARD stopped $newest (avail under 3000)"
  }
  Start-Sleep 30
}
