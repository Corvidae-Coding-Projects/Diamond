"""Small dependency-free real WebSocket client; no Diamond implementation."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct


class WebSocket:
    """RFC 6455 test client with explicit origin/cookie and bounded messages."""

    def __init__(self, host, port, path, *, authority, origin=None, cookie=None,
                 timeout=5, extra_headers=None, receive_buffer=None, tls_context=None):
        self.socket = socket.create_connection((host, port), timeout)
        if tls_context is not None:
            self.socket = tls_context.wrap_socket(self.socket, server_hostname=host)
        self.socket.settimeout(timeout)
        if receive_buffer is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, receive_buffer)
        self.buffer = bytearray()
        self.closed = False
        key = base64.b64encode(os.urandom(16)).decode()
        headers = {"Host": authority, "Connection": "Upgrade", "Upgrade": "websocket",
                   "Sec-WebSocket-Version": "13", "Sec-WebSocket-Key": key}
        if origin is not None:
            headers["Origin"] = origin
        if cookie is not None:
            headers["Cookie"] = cookie
        headers.update(extra_headers or {})
        self.socket.sendall((f"GET {path} HTTP/1.1\r\n" + "".join(
            f"{name}: {value}\r\n" for name, value in headers.items()) + "\r\n").encode())
        while b"\r\n\r\n" not in self.buffer:
            data = self.socket.recv(4096)
            if not data:
                raise EOFError("WebSocket upgrade closed")
            self.buffer.extend(data)
            if len(self.buffer) > 65536:
                raise ValueError("WebSocket response headers exceed bound")
        raw, self.buffer = bytes(self.buffer).split(b"\r\n\r\n", 1)
        self.buffer = bytearray(self.buffer)
        lines = raw.decode("iso-8859-1").split("\r\n")
        self.status = int(lines[0].split()[1])
        self.headers = {name.lower(): value.strip() for line in lines[1:]
                        for name, _, value in [line.partition(":")]}
        if self.status == 101:
            expected = base64.b64encode(hashlib.sha1((key +
                "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            if self.headers.get("sec-websocket-accept") != expected:
                raise ValueError("WebSocket handshake accept mismatch")

    def _read(self, count):
        while len(self.buffer) < count:
            data = self.socket.recv(max(4096, count - len(self.buffer)))
            if not data:
                self.closed = True
                raise EOFError("WebSocket peer closed")
            self.buffer.extend(data)
        value = bytes(self.buffer[:count])
        del self.buffer[:count]
        return value

    def send_frame(self, payload, *, opcode=1):
        payload = payload.encode() if isinstance(payload, str) else payload
        length = len(payload)
        if length > 1024 * 1024:
            raise ValueError("test client payload exceeds one MiB bound")
        header = bytes([0x80 | opcode])
        if length < 126:
            header += bytes([0x80 | length])
        elif length <= 65535:
            header += b"\xfe" + struct.pack("!H", length)
        else:
            header += b"\xff" + struct.pack("!Q", length)
        mask = os.urandom(4)
        self.socket.sendall(header + mask + bytes(value ^ mask[index % 4]
                            for index, value in enumerate(payload)))

    def send_json(self, value):
        self.send_frame(json.dumps(value, separators=(",", ":")))

    def receive(self):
        first, second = self._read(2)
        if second & 0x80:
            raise ValueError("server frames must not be masked")
        if first & 0x70 or not first & 0x80:
            raise ValueError("unexpected extension or fragmented server frame")
        length = second & 0x7f
        if length == 126:
            length = struct.unpack("!H", self._read(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read(8))[0]
        if length > 1024 * 1024:
            raise ValueError("server frame exceeds independent one MiB bound")
        data = self._read(length)
        opcode = first & 15
        if opcode == 9:
            self.send_frame(data, opcode=10)
            return self.receive()
        if opcode == 8:
            self.closed = True
            return {"kind": "close", "code": struct.unpack("!H", data[:2])[0]
                    if len(data) >= 2 else None, "reason": data[2:].decode("utf-8")}
        if opcode != 1:
            return {"kind": "frame", "opcode": opcode, "bytes": len(data)}
        return json.loads(data)

    def close(self):
        try:
            if not self.closed and self.status == 101:
                self.send_frame(struct.pack("!H", 1000), opcode=8)
        except OSError:
            pass
        finally:
            self.socket.close()
            self.closed = True
