"""Minimal fake IRC server for testing notify_irc.py, plus a blackhole."""
import asyncio


class FakeIRC:
    def __init__(self):
        self.messages = []
        self.server = None
        self.port = None

    async def start(self):
        self.server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def _client(self, reader, writer):
        nick = "x"

        def send(line):
            writer.write((line + "\r\n").encode())

        while True:
            raw = await reader.readline()
            if not raw:
                break
            line = raw.decode(errors="replace").rstrip("\r\n")
            parts = line.split(" ")
            cmd = parts[0].upper()
            if cmd == "CAP":
                if len(parts) > 1 and parts[1].upper() == "LS":
                    send(":srv CAP * LS :")
            elif cmd == "NICK":
                nick = parts[1]
            elif cmd == "USER":
                send(f":srv 001 {nick} :Welcome")
                send(f":srv 375 {nick} :- srv Message of the day -")
                send(f":srv 372 {nick} :- hello")
                send(f":srv 376 {nick} :End of MOTD")
            elif cmd == "PING":
                send(f":srv PONG srv :{parts[1].lstrip(':')}")
            elif cmd == "JOIN":
                send(f":{nick}!u@h JOIN {parts[1]}")
            elif cmd == "PRIVMSG" or cmd == "NOTICE":
                self.messages.append(line.split(" ", 2)[2].lstrip(":"))
            elif cmd == "VERSION":
                send(f":srv 351 {nick} fake :fake")
            elif cmd == "PART":
                send(f":{nick}!u@h PART {parts[1]}")
            elif cmd == "QUIT":
                break
            await writer.drain()
        writer.close()

    def stop(self):
        self.server.close()


class Blackhole:
    """Accepts TCP connections and never says anything (stalled handshake)."""

    def __init__(self):
        self.server = None
        self.port = None
        self.connections = 0

    async def start(self):
        self.server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def _client(self, reader, writer):
        self.connections += 1
        await asyncio.sleep(3600)

    def stop(self):
        self.server.close()
