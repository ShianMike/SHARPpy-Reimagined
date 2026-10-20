"""The shared adapter must retain connections from concurrent map products."""

from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Barrier, Thread

import pytest

from sharpmod.providers import hrrr_field


@pytest.mark.parametrize("workers", [4, 8])
def test_concurrent_field_bursts_reuse_their_existing_connections(monkeypatch, workers):
    consumers = hrrr_field._RANGE_WORKERS * workers
    barrier = Barrier(consumers)
    connections = set()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            connections.add(self.client_address[1])
            barrier.wait(timeout=5)
            self.send_response(206)
            self.send_header("Content-Length", "4")
            self.end_headers()
            self.wfile.write(b"GRIB")

        def log_message(self, *_args):
            pass

    class Server(ThreadingHTTPServer):
        request_queue_size = consumers * 2

    monkeypatch.setattr(hrrr_field, "_SESSION", None)
    session = hrrr_field._session()
    # Exercise the production HTTPS adapter on a loopback HTTP transport.
    session.mount("http://", session.get_adapter("https://"))
    with Server(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/model.grib2"
            with ThreadPoolExecutor(consumers) as pool:
                for _burst in range(2):
                    assert list(pool.map(lambda _index: hrrr_field._default_opener(url, 5, 4), range(consumers))) == [b"GRIB"] * consumers
            assert len(connections) == consumers, "shared pool discarded reusable connections between bursts"
        finally:
            session.close()
            server.shutdown()
            thread.join(timeout=2)
        assert not thread.is_alive()
