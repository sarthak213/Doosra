"""The built-in llama.cpp engine: command line, start-mode fallbacks, and pointing the copilot at it.
No GPU or real llama-server needed: processes and health checks are faked."""

from pathlib import Path

import pytest

import local_llm
from agent import graph


@pytest.fixture
def engine_dir(tmp_path, monkeypatch):
    for build in ("vulkan", "cpu"):
        (tmp_path / "engine" / build).mkdir(parents=True)
        (tmp_path / "engine" / build / "llama-server.exe").write_text("")
    monkeypatch.setenv("DOOSRA_ENGINE_DIR", str(tmp_path / "engine"))
    monkeypatch.setenv("DOOSRA_HOME", str(tmp_path / "home"))
    model = tmp_path / "model.gguf"
    model.write_text("")
    return model


def test_the_command_line(engine_dir):
    args, env = local_llm.command("vulkan", engine_dir, 9000)
    assert args[0].endswith(str(Path("vulkan") / "llama-server.exe"))
    for flag in ("--jinja", "--reasoning-format", "-ngl"):
        assert flag in args
    assert args[args.index("--port") + 1] == "9000" and args[args.index("--host") + 1] == "127.0.0.1" and env == {}
    args, env = local_llm.command("vulkan-nocoopmat", engine_dir, 9000)
    assert env == {"GGML_VK_DISABLE_COOPMAT": "1"}
    args, _ = local_llm.command("cpu", engine_dir, 9000)
    assert str(Path("cpu") / "llama-server.exe") in args[0] and "-ngl" not in args


class FakeProc:
    def __init__(self, ok):
        self.ok, self.killed = ok, False

    def poll(self):
        return None if self.ok and not self.killed else 1

    def kill(self):
        self.killed = True

    terminate = kill

    def wait(self, timeout=None):
        return 0


def test_falls_back_from_vulkan_to_cpu_and_remembers(engine_dir, monkeypatch):
    tried = []

    def popen(args, env, **kw):
        mode = "cpu" if "cpu" in args[0] else ("vulkan-nocoopmat" if env.get("GGML_VK_DISABLE_COOPMAT") else "vulkan")
        tried.append(mode)
        return FakeProc(ok=mode == "cpu")

    monkeypatch.setattr(local_llm.subprocess, "Popen", popen)
    monkeypatch.setattr(local_llm, "_healthy", lambda port: True)
    monkeypatch.setattr(local_llm, "START_TIMEOUT", 0.2)
    eng = local_llm.Engine()
    assert eng.start(engine_dir) == "cpu"
    assert tried == ["vulkan", "vulkan-nocoopmat", "cpu"]
    tried.clear()
    assert eng.start(engine_dir, preferred="cpu") == "cpu" and tried == ["cpu"]     # the saved mode goes first
    assert eng.status()["base_url"].startswith("http://127.0.0.1:")


def test_a_missing_model_or_engine_is_a_clear_error(engine_dir, tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="isn't there"):
        local_llm.Engine().start(tmp_path / "nope.gguf")
    monkeypatch.setenv("DOOSRA_ENGINE_DIR", str(tmp_path / "empty"))
    with pytest.raises(RuntimeError, match="missing"):
        local_llm.Engine().start(engine_dir)


def test_the_copilot_can_be_pointed_at_the_engine_and_back(monkeypatch):
    before = (graph.PROVIDER, graph.BASE_URL, graph.MODEL)
    try:
        graph.configure("llamacpp", base_url="http://127.0.0.1:9123/v1")
        assert graph.LOCAL and graph.client.max_retries == 0 and graph.BASE_URL.endswith(":9123/v1")
        graph.configure("lmstudio")
        assert graph.BASE_URL == "http://localhost:1234/v1" and graph.MODEL == "local-model"
    finally:
        graph.configure(*before)
