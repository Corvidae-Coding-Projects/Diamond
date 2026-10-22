"""Transparent framed Unix-socket PG fault fixture; never fabricates responses."""
from __future__ import annotations
import socket
import struct
import threading


def read_exact(connection, length):
    data = bytearray()
    while len(data) < length:
        part = connection.recv(length - len(data))
        if not part:
            raise EOFError
        data.extend(part)
    return bytes(data)


def frame(connection):
    tag = read_exact(connection, 1)
    length = read_exact(connection, 4)
    size = struct.unpack("!I", length)[0]
    if size < 4 or size > 4_194_304:
        raise ValueError("unbounded PostgreSQL fixture frame")
    body = read_exact(connection, size - 4)
    return tag, tag + length + body, body


class PostgresProxy:
    def __init__(self, path, upstream):
        self.path, self.upstream = str(path), str(upstream)
        self.stop = threading.Event()
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(self.path)
        self.listener.listen(16)
        self.listener.settimeout(.2)
        self.lock = threading.Lock()
        self.sockets, self.threads, self.events = set(), [], []
        self.gate_predicate = None
        self.gate_release = threading.Event()
        self.gate_release.set()
        self.gated = 0
        self.drop_commit = False
        self.commit_predicate = None
        self.dropped_commit = threading.Event()
        self.errors = []
        self.acceptor = threading.Thread(target=self.accept, name="w5-pg-accept")
        self.acceptor.start()

    def hold(self, predicate):
        with self.lock:
            self.gate_predicate, self.gated = predicate, 0
            self.gate_release.clear()

    def release(self):
        with self.lock:
            self.gate_predicate = None
            self.gate_release.set()

    def arm_commit_loss(self, predicate=None):
        with self.lock:
            self.drop_commit = True
            self.commit_predicate = predicate
            self.dropped_commit.clear()

    def accept(self):
        while not self.stop.is_set():
            try:
                incoming, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            outgoing = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                outgoing.connect(self.upstream)
            except OSError:
                incoming.close()
                outgoing.close()
                continue
            with self.lock:
                self.sockets.update([incoming, outgoing])
            thread = threading.Thread(target=self.connection, args=(incoming, outgoing), name="w5-pg-connection")
            self.threads.append(thread)
            thread.start()

    def connection(self, client, backend):
        state = {"sql": "", "statements": []}
        def frontend():
            try:
                # Startup is the only untagged frame. NoTls is the qualified
                # local transport, and actual startup/auth bytes are relayed.
                length = read_exact(client, 4)
                size = struct.unpack("!I", length)[0]
                if not 8 <= size <= 16_384:
                    raise ValueError("invalid startup fixture size")
                backend.sendall(length + read_exact(client, size - 4))
                while not self.stop.is_set():
                    tag, packet, body = frame(client)
                    if tag == b"Q":
                        state["sql"] = body.removesuffix(b"\0").decode("utf-8")
                    elif tag == b"P":
                        state["sql"] = body.split(b"\0", 2)[1].decode("utf-8")
                    if tag in [b"Q", b"P"]:
                        if state["sql"].startswith("BEGIN"):
                            state["statements"] = []
                        state["statements"].append(state["sql"])
                    backend.sendall(packet)
            except (EOFError, ConnectionError, OSError):
                pass
            except Exception as failure:
                self.errors.append(repr(failure))
            finally:
                for connection in [client, backend]:
                    try:
                        connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
        worker = threading.Thread(target=frontend, name="w5-pg-frontend")
        worker.start()
        try:
            while not self.stop.is_set():
                tag, packet, body = frame(backend)
                sql = state["sql"]
                gate = False
                with self.lock:
                    if tag == b"C" and self.gate_predicate is not None and self.gate_predicate(sql):
                        self.gated += 1
                        self.events.append({"kind": "held-real-query-completion", "sql_prefix": sql[:100]})
                        gate = True
                    if tag == b"C" and body == b"COMMIT\0" and self.drop_commit and (self.commit_predicate is None or self.commit_predicate(state["statements"])):
                        self.drop_commit = False
                        self.events.append({"kind": "dropped-real-commit-ack"})
                        self.dropped_commit.set()
                        break
                if gate and not self.gate_release.wait(timeout=8):
                    raise TimeoutError("bounded PG fixture gate expired")
                client.sendall(packet)
        except (EOFError, ConnectionError, OSError):
            pass
        except Exception as failure:
            self.errors.append(repr(failure))
        finally:
            for connection in [client, backend]:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()
            worker.join(timeout=10)
            if worker.is_alive():
                self.errors.append("frontend thread did not drain")
            with self.lock:
                self.sockets.discard(client)
                self.sockets.discard(backend)

    def close(self):
        self.stop.set()
        self.release()
        self.listener.close()
        with self.lock:
            connections = list(self.sockets)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.acceptor.join(timeout=5)
        for worker in self.threads:
            worker.join(timeout=10)
        if self.acceptor.is_alive() or any(worker.is_alive() for worker in self.threads):
            self.errors.append("PG fixture owned threads did not drain")
