"""
Watch the paraphrasing (paraphrase.py) as it runs: how many questions are done, and the latest rewrites.

    python ml/toolcall/progress.py            # once
    python ml/toolcall/progress.py --watch    # refresh every 30 seconds (Ctrl+C to stop)

paraphrase.py saves every 20 batches (160 questions), so the counts move in steps.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "ml" / "out" / "toolcall"
LOG = ROOT / "backend" / "data" / "logs" / "engine.log"


def show() -> None:
    total_done = total_all = 0
    latest = []
    for split in ("train", "validation", "test"):
        rows = []
        for line in (OUT / f"{split}.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:      # mid-save
                continue
        done = [r for r in rows if r.get("paraphrased")]
        kept = [r for r in rows if r.get("original_question")]
        total_done += len(done)
        total_all += len(rows)
        print(f"{split:10} {len(done):5,} processed, {len(kept):5,} rewritten, of {len(rows):,}")
        latest += kept[-3:]
    print(f"about {total_done / (0.6 * total_all):.0%} of the ~{0.6 * total_all:,.0f} questions to rewrite are done")
    if LOG.exists():
        speeds = re.findall(r"eval time =\s+[\d.]+ ms /\s+\d+ tokens \(.*?([\d.]+) tokens per second\)",
                            LOG.read_text(encoding="utf-8", errors="ignore")[-4000:])
        if speeds:
            print(f"engine: {float(speeds[-1]):.1f} tokens/second writing")
    print("\nlatest rewrites:")
    for r in latest[-5:]:
        print(f"  {r['original_question']}\n    -> {r['question']}")


if __name__ == "__main__":
    if "--watch" in sys.argv:
        while True:
            print("\033[2J\033[H" + time.strftime("%H:%M:%S"))
            show()
            time.sleep(30)
    else:
        show()
