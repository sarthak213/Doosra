"""The desktop app's first-run setup and settings, with a fake engine and fake downloads
(no GPU, no network)."""

import asyncio
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

import desktop_app
import downloads
import local_llm
import main
import setup_job
from agent import graph
from ingest import pull


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    monkeypatch.setenv("DOOSRA_DESKTOP", "1")
    monkeypatch.setenv("DOOSRA_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(desktop_app, "lmstudio_copies", lambda: {})
    monkeypatch.setattr(desktop_app, "lmstudio", lambda: {"running": False, "models": []})
    monkeypatch.setattr(desktop_app, "gpus", lambda: [])
    before = (graph.PROVIDER, graph.BASE_URL, graph.MODEL)
    yield tmp_path / "home"
    graph.configure(*before)
    setup_job.JOB = setup_job.Job()


def wait_for(job, timeout=10):
    end = time.time() + timeout
    while job.state == "running" and time.time() < end:
        time.sleep(0.05)
    return job.state


class TestSettingsAndHardware:
    def test_settings_persist_and_ignore_unknown_keys(self, desktop):
        assert desktop_app.load_settings()["engine"] == "builtin"
        desktop_app.save_settings(model="qwen3.5-4b", evil="x")
        assert desktop_app.load_settings()["model"] == "qwen3.5-4b" and "evil" not in desktop_app.load_settings()

    def test_the_recommendation_follows_memory_and_gpu(self):
        gpu = [{"memory_gb": 16}]
        assert desktop_app.recommend(32, gpu) == "qwen3.5-9b"
        assert desktop_app.recommend(16, gpu) == "qwen3.5-9b"
        assert desktop_app.recommend(16, []) == "qwen3.5-4b"          # 16 GB and no GPU: the small one
        assert desktop_app.recommend(32, []) == "qwen3.5-9b"          # plenty of memory makes up for no GPU
        assert desktop_app.recommend(8, gpu) == "qwen3.5-4b"

    def test_every_listed_model_is_pinned(self):
        for m in desktop_app.REGISTRY:
            assert len(m["sha256"]) == 64 and m["file"].endswith(".gguf") and m["license"] == "Apache-2.0"


class TestRoutes:
    def test_hidden_outside_the_desktop_app(self, monkeypatch):
        monkeypatch.delenv("DOOSRA_DESKTOP", raising=False)
        c = TestClient(main.app)
        assert c.get("/api/desktop/status").json() == {"desktop": False}
        assert c.post("/api/desktop/setup", json={}).status_code == 404
        assert c.post("/api/desktop/open-folder").status_code == 404

    def test_only_this_computer(self, desktop):
        far = TestClient(main.app, client=("203.0.113.9", 5000))
        assert far.post("/api/desktop/setup", json={}).status_code == 403

    def test_status_describes_the_install(self, desktop):
        s = TestClient(main.app).get("/api/desktop/status").json()
        assert s["desktop"] is True and s["settings"]["engine"] == "builtin"
        assert {m["id"] for m in s["models"]} == {"qwen3.5-9b", "qwen3.5-4b"} and s["hardware"]["ram_gb"] > 0

    def test_a_model_in_use_cant_be_deleted(self, desktop, monkeypatch):
        (desktop / "models").mkdir(parents=True, exist_ok=True)
        (desktop / "models" / "Qwen3.5-4B-Q4_K_M.gguf").write_text("x")
        desktop_app.save_settings(model="qwen3.5-4b")
        monkeypatch.setattr(local_llm.ENGINE, "running", lambda: True)
        c = TestClient(main.app)
        assert c.delete("/api/desktop/models/qwen3.5-4b").status_code == 409
        monkeypatch.setattr(local_llm.ENGINE, "running", lambda: False)
        assert c.delete("/api/desktop/models/qwen3.5-4b").json()["installed"] == []
        assert desktop_app.load_settings()["model"] is None


class TestSetupJob:
    def fakes(self, monkeypatch, have_data=True):
        monkeypatch.setattr(pull, "local_info", lambda path=None: {"built_at": "x"} if have_data else None)
        started = []

        def start(model, preferred=None, ctx=None):
            started.append((str(model), preferred))
            local_llm.ENGINE.port = 9999
            return "cpu"
        monkeypatch.setattr(local_llm.ENGINE, "start", start)
        monkeypatch.setattr(setup_job, "test_prompt", lambda: "ready")
        return started

    def test_downloads_the_model_starts_the_engine_and_remembers(self, desktop, monkeypatch):
        started = self.fakes(monkeypatch)
        got = []

        def fake_download(url, dest, sha256=None, progress=None, cancel=None):
            got.append(url)
            dest.write_text("weights")
            progress(7, 7)
            return dest
        monkeypatch.setattr(downloads, "download", fake_download)
        job = setup_job.start(model="qwen3.5-4b")
        assert wait_for(job) == "done", job.snapshot()
        assert got == ["https://huggingface.co/lmstudio-community/Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B-Q4_K_M.gguf"]
        assert [s.state for s in job.steps] == ["skipped", "done", "done", "done"]
        assert started[0][0].endswith("Qwen3.5-4B-Q4_K_M.gguf")
        s = desktop_app.load_settings()
        assert s["model"] == "qwen3.5-4b" and s["engine_mode"] == "cpu"
        assert graph.PROVIDER == "llamacpp" and graph.BASE_URL.endswith(":9999/v1") and setup_job.ready()

    def test_a_cancelled_download_can_be_resumed(self, desktop, monkeypatch):
        self.fakes(monkeypatch)
        monkeypatch.setattr(downloads, "download",
                            lambda *a, **k: (_ for _ in ()).throw(downloads.Cancelled("stopped")))
        job = setup_job.start(model="qwen3.5-4b")
        assert wait_for(job) == "cancelled" and job.step("model").state == "waiting"
        assert job.step("model").detail == "Stopped"
        assert not setup_job.ready()

    def test_lmstudios_copy_is_used_only_if_its_checksum_matches(self, desktop, monkeypatch, tmp_path):
        self.fakes(monkeypatch)
        copy = tmp_path / "lmstudio" / "Qwen3.5-4B-Q4_K_M.gguf"
        copy.parent.mkdir()
        copy.write_bytes(b"real weights")
        monkeypatch.setattr(desktop_app, "lmstudio_copies", lambda: {"qwen3.5-4b": copy})
        info = dict(desktop_app.model_info("qwen3.5-4b"), sha256=hashlib.sha256(b"real weights").hexdigest())
        monkeypatch.setattr(desktop_app, "model_info", lambda mid: info)
        monkeypatch.setattr(downloads, "download", lambda *a, **k: pytest.fail("should not download"))
        job = setup_job.start(model="qwen3.5-4b", use_lmstudio_copy=True)
        assert wait_for(job) == "done", job.snapshot()
        assert desktop_app.load_settings()["model_path"] == str(copy)

    def test_lm_studio_needs_to_be_running(self, desktop, monkeypatch):
        self.fakes(monkeypatch)
        job = setup_job.start(engine="lmstudio")
        assert wait_for(job) == "error" and "LM Studio isn't answering" in job.error
        monkeypatch.setattr(desktop_app, "lmstudio", lambda: {"running": True, "models": ["qwen/qwen3.5-9b"]})
        job = setup_job.start(engine="lmstudio")
        assert wait_for(job) == "done" and graph.PROVIDER == "lmstudio" and graph.MODEL == "qwen/qwen3.5-9b"
        assert desktop_app.load_settings()["engine"] == "lmstudio" and setup_job.ready()


class _FakeModelServer(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"          # keep-alive, like llama-server: the connection outlives a request

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        body = json.dumps({"id": "x", "object": "chat.completion", "created": 0, "model": "m", "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ready"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_the_setup_check_leaves_the_apps_client_usable(desktop):
    # The check runs on an event loop of its own; the next real question runs on the server's loop.
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeModelServer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        graph.configure("llamacpp", base_url=f"http://127.0.0.1:{server.server_port}/v1")
        assert setup_job.test_prompt() == "ready"

        async def question():
            r = await graph.client.chat.completions.create(model=graph.MODEL, messages=[{"role": "user", "content": "hi"}])
            return r.choices[0].message.content
        assert asyncio.run(question()) == "ready"
    finally:
        server.shutdown()
