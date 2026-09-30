"""
First-run setup (and model changes later): get the database and a model, start the engine, and
check it answers. Runs in a background thread; the setup screen follows it through `snapshot()`.

Steps: data -> model -> engine -> test. Each is skipped when already done, so running setup again
(after a cancelled or failed attempt) picks up where it stopped, including half-finished downloads.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import desktop_app
import downloads
import local_llm
from agent import graph
from analytics import db
from ingest import pull

HF = "https://huggingface.co/{repo}/resolve/main/{file}"


@dataclass
class Step:
    id: str
    label: str
    state: str = "waiting"          # waiting | running | done | skipped | error
    detail: str = ""
    done: int = 0
    total: int | None = None

    def public(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Job:
    steps: list[Step] = field(default_factory=list)
    state: str = "idle"             # idle | running | done | error | cancelled
    error: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    started: float = 0.0

    def step(self, sid: str) -> Step:
        return next(s for s in self.steps if s.id == sid)

    def snapshot(self) -> dict:
        return {"state": self.state, "error": self.error, "steps": [s.public() for s in self.steps],
                "engine": local_llm.ENGINE.status()}


JOB = Job()
_lock = threading.Lock()


def start(engine: str = "builtin", model: str | None = None, use_lmstudio_copy: bool = False,
          lmstudio_model: str | None = None) -> Job:
    """Begin (or resume) setup. Only one runs at a time."""
    global JOB
    with _lock:
        if JOB.state == "running":
            return JOB
        if engine == "lmstudio":
            steps = [Step("data", "Cricket database"), Step("engine", "Connect to LM Studio"), Step("test", "Test the AI")]
        else:
            steps = [Step("data", "Cricket database"), Step("model", "AI model"), Step("engine", "Start the AI engine"),
                     Step("test", "Test the AI")]
        JOB = Job(steps=steps, state="running", started=time.time())
        threading.Thread(target=_run, args=(JOB, engine, model, use_lmstudio_copy, lmstudio_model),
                         name="setup", daemon=True).start()
        return JOB


def cancel() -> None:
    JOB.cancel.set()


def _run(job: Job, engine: str, model: str | None, use_copy: bool, lm_model: str | None) -> None:
    current = None
    try:
        current = job.step("data")
        _data(job, current)
        if engine == "lmstudio":
            current = job.step("engine")
            _lmstudio(current, lm_model)
        else:
            model = model or desktop_app.hardware()["recommended"]
            current = job.step("model")
            path = _model(job, current, model, use_copy)
            current = job.step("engine")
            _engine(current, path)
        current = job.step("test")
        _test(current)
        job.state = "done"
    except downloads.Cancelled as e:
        job.state, job.error = "cancelled", str(e)
        if current:
            current.state, current.detail = "waiting", "Stopped"
    except Exception as e:  # noqa: BLE001 - reported on the setup screen, which offers "Try again"
        job.state, job.error = "error", str(e)
        if current:
            current.state, current.detail = "error", str(e)


def _progress(step: Step):
    def cb(done: int, total: int | None) -> None:
        step.done, step.total = done, total
    return cb


def _data(job: Job, step: Step) -> None:
    if pull.local_info(db.DB_PATH):
        step.state, step.detail = "skipped", "Already installed"
        return
    step.state = "running"

    def stage(name: str) -> None:
        step.detail = {"downloading": "Downloading", "unpacking": "Unpacking", "verifying": "Checking",
                       "installing": "Installing"}.get(name, name)
    pull.pull(target=db.DB_PATH, progress=_progress(step), cancel=job.cancel, stage=stage)
    local_llm_catalog_reset()
    step.state, step.detail = "done", "Installed"


def local_llm_catalog_reset() -> None:
    """A database appeared mid-run: drop anything cached from before it existed."""
    from agent import tools
    tools.invalidate_caches()
    tools.warm_caches()


def _model(job: Job, step: Step, model_id: str, use_copy: bool):
    info = desktop_app.model_info(model_id)
    settings = desktop_app.load_settings()
    path = desktop_app.model_path(model_id, settings)
    if path.exists():
        step.state, step.detail = "skipped", f"{info['label']} already installed"
        desktop_app.save_settings(model=model_id)
        return path
    step.state = "running"
    copy = desktop_app.lmstudio_copies().get(model_id) if use_copy else None
    if copy:
        step.detail = "Checking the copy LM Studio already has"
        if downloads.sha256_file(copy, _progress(step)) == info["sha256"]:
            desktop_app.save_settings(model=model_id, model_path=str(copy))
            step.state, step.detail = "done", f"Using LM Studio's copy of {info['label']}"
            return copy
        step.detail = "LM Studio's copy didn't match; downloading instead"
    step.detail = f"Downloading {info['label']}"
    downloads.download(HF.format(**info), path, sha256=info["sha256"], progress=_progress(step), cancel=job.cancel)
    desktop_app.save_settings(model=model_id, model_path=None)
    step.state, step.detail = "done", f"{info['label']} installed"
    return path


def _engine(step: Step, path) -> None:
    step.state, step.detail = "running", "Loading the model (the first time can take a minute)"
    settings = desktop_app.load_settings()
    mode = local_llm.ENGINE.start(path, settings.get("engine_mode"), settings.get("ctx_size") or local_llm.CTX_SIZE)
    desktop_app.save_settings(engine="builtin", engine_mode=mode)
    graph.configure("llamacpp", base_url=local_llm.ENGINE.base_url)
    step.state = "done"
    step.detail = {"vulkan": "Running on the GPU", "vulkan-nocoopmat": "Running on the GPU (compatibility mode)",
                   "cpu": "Running on the CPU (no usable GPU found)"}[mode]


def _lmstudio(step: Step, model: str | None) -> None:
    step.state = "running"
    found = desktop_app.lmstudio()
    if not found["running"]:
        raise RuntimeError("LM Studio isn't answering on localhost:1234. Open it and start its server, then try again.")
    model = model or next((m for m in found["models"] if "qwen" in m.lower()), found["models"][0] if found["models"] else None)
    if not model:
        raise RuntimeError("LM Studio has no models loaded. Download or load one there first.")
    local_llm.ENGINE.stop()
    graph.configure("lmstudio", model=model)
    desktop_app.save_settings(engine="lmstudio", lmstudio_model=model)
    step.state, step.detail = "done", f"Using {model} in LM Studio"


def _test(step: Step) -> None:
    step.state, step.detail = "running", "Asking a test question"
    t = time.time()
    reply = test_prompt()
    step.state, step.detail = "done", f"Answered in {time.time() - t:.0f}s: {reply[:40]}"


def test_prompt() -> str:
    """One tiny request straight to the model server, without reasoning. It uses a client of its own:
    this runs on its own event loop, and the app's shared client would keep a connection tied to that
    loop after it closes, failing the next real question with "Event loop is closed"."""
    import asyncio

    from openai import AsyncOpenAI

    async def ask():
        extra = {"chat_template_kwargs": {"enable_thinking": False}, "reasoning_effort": "none"}
        async with AsyncOpenAI(base_url=graph.BASE_URL, api_key=graph.client.api_key, timeout=60, max_retries=0) as c:
            r = await c.chat.completions.create(
                model=graph.MODEL, messages=[{"role": "user", "content": "Reply with just the word: ready"}],
                max_tokens=16, temperature=0, extra_body=extra)
        return (r.choices[0].message.content or "").strip() or "(no text)"
    return asyncio.run(ask())


def apply_on_startup() -> None:
    """Desktop app start-up: bring the chosen engine up in the background, if setup was finished."""
    settings = desktop_app.load_settings()
    if settings["engine"] == "lmstudio" and settings.get("lmstudio_model"):
        graph.configure("lmstudio", model=settings["lmstudio_model"])
        return
    model = settings.get("model")
    if model and desktop_app.model_path(model, settings).exists():
        local_llm.start_in_background(desktop_app.model_path(model, settings), settings.get("engine_mode"),
                                      on_ready=lambda e: graph.configure("llamacpp", base_url=e.base_url))


def ready() -> bool:
    """Setup is finished: there's data, and an engine choice that can run."""
    settings = desktop_app.load_settings()
    if not pull.local_info(db.DB_PATH):
        return False
    if settings["engine"] == "lmstudio":
        return bool(settings.get("lmstudio_model"))
    return bool(settings.get("model")) and desktop_app.model_path(settings["model"], settings).exists()
