"""
Paraphrase the generated questions with the local Qwen3.5 9B, so the fine-tuned model learns to handle the
many ways people ask, not just the templates. Runs on Doosra's own llama.cpp engine (backend/local_llm.py).

    python ml/toolcall/paraphrase.py                  # rewrites ~60% of questions in ml/out/toolcall/*.jsonl

A rewrite is kept only if every name, competition and number in the original is still there (so the gold
tool call still matches the question); otherwise the original stays. Batches of questions per request keep
it quick; progress is saved per file, so it can be stopped and resumed.
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import doosra_home  # noqa: E402
import local_llm  # noqa: E402

OUT = ROOT / "ml" / "out" / "toolcall"
SHARE, BATCH = 0.6, 8
MODEL = Path(os.environ.get("LOCALAPPDATA", "")) / "Doosra" / "models" / "Qwen3.5-9B-Q4_K_M.gguf"
PROMPT = """Rewrite each cricket question below the way a different fan might type it: vary the wording, word \
order and tone (casual, terse, or formal). Keep every player, team, ground and competition name, every year \
or season, and every number exactly as written. Don't add or drop details. Reply with only a JSON array of \
the rewritten questions, in the same order.

{items}"""


def protected(q: str) -> set[str]:
    """What a rewrite must keep: numbers/seasons and capitalised words (names), apart from the first word."""
    words = re.findall(r"[A-Za-z0-9'/.-]+", q)
    keep = {w for w in words[1:] if w[0].isupper() or any(c.isdigit() for c in w)}
    return {w.rstrip(".'s").rstrip("'") for w in keep if w not in ("I", "IPL's")}


def ok(original: str, rewrite: str) -> bool:
    if not rewrite or len(rewrite) > 2.5 * len(original) or rewrite.strip() == original.strip():
        return False
    return all(w in rewrite for w in protected(original))


def ask(base_url: str, questions: list[str]) -> list[str] | None:
    body = {"model": "local", "temperature": 0.9, "max_tokens": 900,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": PROMPT.format(
                items="\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions)))}]}
    req = urllib.request.Request(f"{base_url}/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            text = json.load(r)["choices"][0]["message"]["content"]
        out = json.loads(text[text.index("["):text.rindex("]") + 1])
        return out if isinstance(out, list) and len(out) == len(questions) else None
    except Exception:  # noqa: BLE001 - a bad batch just keeps its originals
        return None


def main() -> None:
    if not MODEL.exists():
        sys.exit(f"The 9B model isn't at {MODEL}")
    engine = local_llm.ENGINE
    mode = engine.start(MODEL)
    print(f"engine up ({mode}) at {engine.base_url}")
    rng = random.Random(11)
    try:
        for split in ("train", "validation", "test"):
            path = OUT / f"{split}.jsonl"
            rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
            todo = [r for r in rows if not r.get("paraphrased") and rng.random() < SHARE]
            kept, t = 0, time.time()
            for i in range(0, len(todo), BATCH):
                batch = todo[i:i + BATCH]
                got = ask(engine.base_url, [r["question"] for r in batch])
                for r, new in zip(batch, got or []):
                    r["paraphrased"] = True
                    if isinstance(new, str) and ok(r["question"], new):
                        r["original_question"] = r["question"]
                        r["question"] = new.strip()
                        r["messages"][1]["content"] = new.strip()
                        kept += 1
                if (i // BATCH) % 20 == 0:
                    print(f"  {split}: {i + len(batch)}/{len(todo)} done, {kept} rewrites kept "
                          f"({time.time() - t:.0f}s)", flush=True)
                    path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8")
            path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8")
            print(f"{split}: {kept} of {len(todo)} rewrites kept")
    finally:
        engine.stop()


def clean() -> None:
    """Undo rewrites that change only capitals, punctuation or spacing: they add no variety."""
    def key(q: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", q.lower()).strip()

    for split in ("train", "validation", "test"):
        path = OUT / f"{split}.jsonl"
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
        undone = 0
        for r in rows:
            if r.get("original_question") and key(r["original_question"]) == key(r["question"]):
                r["question"] = r["messages"][1]["content"] = r.pop("original_question")
                undone += 1
        path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8")
        print(f"{split}: {undone} trivial rewrites undone, {sum('original_question' in r for r in rows)} kept")


def reuse(src: Path = OUT / "v1") -> None:
    """Carry over an earlier version's paraphrases: a question generated again word for word gets the rewrite it
    had (already checked), and one whose rewrite was rejected stays as it is. Only new questions are left."""
    done, rewrites = set(), {}
    for split in ("train", "validation", "test"):
        path = src / f"{split}.jsonl"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("original_question"):
                rewrites[r["original_question"]] = r["question"]
            elif r.get("paraphrased"):
                done.add(r["question"])
    for split in ("train", "validation", "test"):
        path = OUT / f"{split}.jsonl"
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
        n_new = n_kept = 0
        for r in rows:
            q = r["question"]
            if q in rewrites:
                r.update(original_question=q, question=rewrites[q], paraphrased=True)
                r["messages"][1]["content"] = rewrites[q]
                n_new += 1
            elif q in done:
                r["paraphrased"] = True
                n_kept += 1
        path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8")
        print(f"{split}: {n_new} rewrites reused, {n_kept} kept as they were, "
              f"{sum(not r.get('paraphrased') for r in rows)} not yet processed")


if __name__ == "__main__":
    clean() if "--clean" in sys.argv else reuse() if "--reuse" in sys.argv else main()
