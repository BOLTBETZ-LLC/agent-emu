# Run one guest shell command through the console bridge and print its output.
# usage: python gsh.py "<command>" [timeout_seconds]
import socket, sys, time, uuid

cmd = sys.argv[1]
timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 60
marker = "__AE_" + uuid.uuid4().hex[:8] + "__"
s = socket.create_connection(("127.0.0.1", 7000))
s.sendall(f"{cmd}; echo {marker}\n".encode())
out, end = b"", time.time() + timeout
s.settimeout(1)
while time.time() < end:
    try:
        d = s.recv(65536)
    except socket.timeout:
        continue
    if not d:
        break
    out += d
    text = out.decode(errors="replace")
    if "\n" + marker in text or text.startswith(marker):
        break
s.close()
print(out.decode(errors="replace"))
