# Hold a Device's guest console named pipe (crosvm allows one client) with async I/O, and expose
# it on 127.0.0.1:<Port>. Guest output is appended to <Log> and sent to the TCP client.
param([string]$Id = "", [int]$Port = 7000, [string]$Log = "C:\dev\agent-emu-work\run\console.log")
$pipeName = "agentemu-console" + $(if ($Id) { "-$Id" } else { "" })
$pipe = New-Object System.IO.Pipes.NamedPipeClientStream(".", $pipeName,
  [System.IO.Pipes.PipeDirection]::InOut, [System.IO.Pipes.PipeOptions]::Asynchronous)
while ($true) { try { $pipe.Connect(2000); break } catch { Start-Sleep -Milliseconds 500 } }
"pipe connected" | Out-Host
$listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
$listener.Start()
$pbuf = New-Object byte[] 65536
$pread = $pipe.ReadAsync($pbuf, 0, $pbuf.Length)
$client = $null; $cstream = $null; $cbuf = New-Object byte[] 65536; $cread = $null
$accept = $listener.AcceptTcpClientAsync()
while ($true) {
  if ($pread.IsCompleted) {
    $n = $pread.Result; if ($n -le 0) { break }
    $fs = [System.IO.File]::Open($Log, 'Append', 'Write', 'ReadWrite'); $fs.Write($pbuf, 0, $n); $fs.Close()
    if ($cstream) { try { $cstream.Write($pbuf, 0, $n) } catch { $client = $null; $cstream = $null; $cread = $null } }
    $pread = $pipe.ReadAsync($pbuf, 0, $pbuf.Length)
  }
  if ($accept.IsCompleted) {
    if ($client) { $client.Close() }
    $client = $accept.Result; $cstream = $client.GetStream(); $cread = $cstream.ReadAsync($cbuf, 0, $cbuf.Length)
    $accept = $listener.AcceptTcpClientAsync()
  }
  if ($cread -and $cread.IsCompleted) {
    $n = $cread.Result
    if ($n -le 0) { $client.Close(); $client = $null; $cstream = $null; $cread = $null }
    else { $pipe.Write($cbuf, 0, $n); $pipe.Flush(); $cread = $cstream.ReadAsync($cbuf, 0, $cbuf.Length) }
  }
  Start-Sleep -Milliseconds 10
}
