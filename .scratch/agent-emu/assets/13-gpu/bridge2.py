# Hold a Device's guest console named pipe (crosvm accepts one client, once) and expose it on TCP.
# Overlapped I/O through asyncio's Proactor, so a pending read never blocks a write.
# usage: python bridge2.py <pipe> <log> <port>
import asyncio, sys

PIPE, LOG, PORT = sys.argv[1], sys.argv[2], int(sys.argv[3])
clients = set()
transport = None


class PipeProto(asyncio.Protocol):
    def __init__(self):
        self.log = open(LOG, "ab", buffering=0)

    def data_received(self, data):
        self.log.write(data)
        for w in list(clients):
            try:
                w.write(data)
            except Exception:
                clients.discard(w)

    def connection_lost(self, exc):
        asyncio.get_event_loop().stop()


async def client(reader, writer):
    clients.add(writer)
    try:
        while d := await reader.read(4096):
            transport.write(d)
    finally:
        clients.discard(writer)
        writer.close()


async def main():
    global transport
    loop = asyncio.get_running_loop()
    while True:
        try:
            transport, _ = await loop.create_pipe_connection(PipeProto, PIPE)
            break
        except OSError:
            await asyncio.sleep(0.5)
    print("pipe connected", flush=True)
    srv = await asyncio.start_server(client, "127.0.0.1", PORT)
    await srv.serve_forever()


asyncio.run(main())
