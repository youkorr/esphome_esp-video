#!/usr/bin/env python3
"""Network sender regression checks, without browser, hardware or dependencies."""
import ast
import collections
import pathlib
import socket
import threading
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1] / "components" / "portall"


def extract(filename, names, scope):
    tree = ast.parse((ROOT / filename).read_text())
    nodes = [node for node in tree.body if
             (isinstance(node, ast.ClassDef) and node.name in names) or
             (isinstance(node, ast.Assign) and any(
                 isinstance(target, ast.Name) and target.id in names
                 for target in node.targets))]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), filename, "exec"), scope)
    return scope


tcp = extract("udisp_send.py", {"_TcpEndpoint", "TCP_WRITE_CHUNK"}, {})
sender = extract("ha_send.py", {"PanelWriter", "RateControl"},
                 dict(collections=collections, threading=threading, time=time,
                      AUDIO_CHANNELS=2, AUDIO_RATE=44100,
                      build_audio_header=lambda *_: b"H"))
Endpoint = tcp["_TcpEndpoint"]
Writer = sender["PanelWriter"]


class NetworkTests(unittest.TestCase):
    def test_chunk_boundaries_preserve_bytes(self):
        class Sink:
            def __init__(self):
                self.chunks = []

            def sendall(self, data):
                self.chunks.append(bytes(data))

        sink = Sink()
        payload = bytes(range(256)) * 801
        Endpoint(sink).write(payload)
        self.assertEqual(b"".join(sink.chunks), payload)
        self.assertLessEqual(max(map(len, sink.chunks)), 16384)
        self.assertEqual(len(sink.chunks), 13)

    def test_tcp_receiver_pause_preserves_stream(self):
        # Actual TCP sockets, deliberately small send buffer and paused reader.
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            with socket.create_connection(listener.getsockname(), timeout=2) as client:
                peer, _ = listener.accept()
                with peer:
                    client.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
                    peer.settimeout(3)
                    payload = b"x" * (256 * 1024)
                    received = bytearray()

                    def read():
                        time.sleep(0.1)
                        while len(received) < len(payload):
                            block = peer.recv(8192)
                            if not block:
                                break
                            received.extend(block)

                    thread = threading.Thread(target=read)
                    thread.start()
                    ep = Endpoint(client)
                    try:
                        ep.write(payload)
                        thread.join(3)
                        self.assertFalse(thread.is_alive())
                        self.assertEqual(received, payload)
                        info = ep.tcp_diagnostics()
                        self.assertIsInstance(info, str)
                    finally:
                        if thread.is_alive():
                            client.shutdown(socket.SHUT_RDWR)
                            thread.join(3)

    def test_writer_reports_audio_wait_and_socket_bytes(self):
        entered, release = threading.Event(), threading.Event()

        class Sink:
            def __init__(self):
                self.writes = []

            def write(self, blob):
                if blob == b"video":
                    entered.set()
                    if not release.wait(2):
                        raise OSError("test timeout")
                self.writes.append(blob)

        sink = Sink()
        writer = Writer(sink)
        try:
            writer.offer([b"video"])
            self.assertTrue(entered.wait(1))
            writer.offer_audio(b"a")
            writer.offer_audio(b"b")
            time.sleep(0.025)
            self.assertIn("audio queue 2/25", writer.take_network_stats(1))
            release.set()
            deadline = time.monotonic() + 2
            while not writer.ready() and time.monotonic() < deadline:
                time.sleep(0.002)
            self.assertTrue(writer.ready())
            self.assertEqual(sink.writes, [b"video", b"Ha", b"Hb"])
            self.assertEqual(writer.take_audio(), (2, 0))
            stats = writer.take_network_stats(1)
            self.assertIn("write max", stats)
            self.assertIn("audio age", stats)
        finally:
            release.set()
            writer.close()

    def test_rate_budget_spend(self):
        rate = sender["RateControl"](2400)
        rate.spend(400 * 1024, 0.0)
        self.assertFalse(rate.allows(0.0))
        self.assertTrue(rate.allows(0.1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
