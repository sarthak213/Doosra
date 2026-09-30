"""Resumable downloads, against a local server that supports Range requests and can drop the
connection part-way (as Hugging Face does), without touching the internet."""

import hashlib
import http.server
import threading

import pytest

import downloads

DATA = bytes(range(256)) * 20_000          # ~5 MB
SHA = hashlib.sha256(DATA).hexdigest()


class Handler(http.server.BaseHTTPRequestHandler):
    drops = 0                              # how many responses to cut off part-way
    stall = False                          # when cutting off, send nothing at all
    requests: list = []
    corrupt = False

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = DATA if not type(self).corrupt else DATA[:-1] + b"X"
        start = 0
        rng = self.headers.get("Range")
        type(self).requests.append(rng)
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            if start >= len(body):
                self.send_response(416)
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
        else:
            self.send_response(200)
        chunk = body[start:]
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        if type(self).drops > 0:
            type(self).drops -= 1
            self.wfile.write(b"" if type(self).stall else chunk[: len(chunk) // 3])   # then drop the connection
            self.wfile.flush()
            self.connection.close()
            return
        self.wfile.write(chunk)


@pytest.fixture
def server(monkeypatch):
    Handler.drops, Handler.requests, Handler.corrupt, Handler.stall = 0, [], False, False
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(downloads.time, "sleep", lambda s: None)       # no back-off waits in tests
    yield f"http://127.0.0.1:{srv.server_address[1]}/file.gguf"
    srv.shutdown()


def test_downloads_and_verifies(server, tmp_path):
    seen = []
    out = downloads.download(server, tmp_path / "m.gguf", sha256=SHA, progress=lambda a, b: seen.append((a, b)))
    assert out.read_bytes() == DATA and not (tmp_path / "m.gguf.part").exists()
    assert seen[-1] == (len(DATA), len(DATA))


def test_resumes_after_dropped_connections(server, tmp_path):
    Handler.drops = 3
    downloads.download(server, tmp_path / "m.gguf", sha256=SHA)
    assert (tmp_path / "m.gguf").read_bytes() == DATA
    assert Handler.requests[0] is None and all(r and r.startswith("bytes=") for r in Handler.requests[1:])


def test_continues_a_download_from_an_earlier_session(server, tmp_path):
    (tmp_path / "m.gguf.part").write_bytes(DATA[:1_000_000])            # the app was closed mid-download
    downloads.download(server, tmp_path / "m.gguf", sha256=SHA)
    assert Handler.requests == ["bytes=1000000-"] and (tmp_path / "m.gguf").read_bytes() == DATA


def test_a_bad_checksum_is_refused_and_discarded(server, tmp_path):
    Handler.corrupt = True
    with pytest.raises(downloads.DownloadError, match="checksum"):
        downloads.download(server, tmp_path / "m.gguf", sha256=SHA)
    assert not (tmp_path / "m.gguf").exists() and not (tmp_path / "m.gguf.part").exists()


def test_cancel_keeps_the_partial_file_for_later(server, tmp_path):
    stop = threading.Event()

    def progress(done, total):
        if done > 1_000_000:
            stop.set()

    with pytest.raises(downloads.Cancelled):
        downloads.download(server, tmp_path / "m.gguf", sha256=SHA, progress=progress, cancel=stop)
    assert (tmp_path / "m.gguf.part").stat().st_size > 1_000_000 and not (tmp_path / "m.gguf").exists()


def test_gives_up_when_the_server_stalls(server, tmp_path):
    (tmp_path / "m.gguf.part").write_bytes(DATA[:10])
    Handler.drops, Handler.stall = 100, True
    with pytest.raises(downloads.DownloadError, match="retries"):
        downloads.download(server, tmp_path / "m.gguf", sha256=SHA, retries=2)
    assert (tmp_path / "m.gguf.part").exists()                           # kept, to resume next time


def test_slow_but_steady_progress_never_gives_up(server, tmp_path):
    Handler.drops = 20                                                   # many cuts, but each one delivers data
    downloads.download(server, tmp_path / "m.gguf", sha256=SHA, retries=2)
    assert (tmp_path / "m.gguf").read_bytes() == DATA
