"""
Assemble the Hugging Face repos for the win-probability model from the trained files:

    python ml/build_hf.py          ->  ml/out/hf/winprob-model/   (model repo: card, models, predictor, reports)
                                       ml/out/hf/winprob-space/   (static Space: page, JS predictor, models, replays)

Run ml/winprob_train.py first. Publish with `python ml/publish.py winprob` / `winprob-space`.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from analytics import replay  # noqa: E402

SRC = ROOT / "ml" / "hf" / "winprob"
MODELS = ROOT / "backend" / "models"
REPORTS = ROOT / "ml" / "out" / "winprob"
OUT = ROOT / "ml" / "out" / "hf"
FINALS = {
    "2024 T20 World Cup final: India v South Africa": "1415755",
    "2023 ODI World Cup final: India v Australia": "1384439",
    "2026 T20 World Cup final: India v New Zealand": "1512773",
    "2025 Champions Trophy final: New Zealand v India": "1466428",
    "IPL 2026 final: Gujarat Titans v RCB": "1535465",
    "IPL 2019 final: Mumbai Indians v CSK": "1181768",
}


def fresh(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def model_card(reports: dict) -> str:
    rows = []
    for g in ("T20", "ODI"):
        r = reports[g]
        m, b = r["model"], r["baselines"]
        rows.append(f"| {g} | {r['test_matches']:,} | **{m['log_loss']:.3f}** | {m['brier']:.3f} | {m['auc']:.3f} | "
                    f"{100 * m['ece']:.1f}% | {m['accuracy']:.1%} | {b['logistic_regression_alone']['log_loss']:.3f} | "
                    f"{b['trees_alone']['log_loss']:.3f} | {b['par_heuristic']['log_loss']:.3f} |")
    swing = {g: reports[g]["swing_on_ordinary_balls"] for g in reports}
    return f"""---
license: odc-by
library_name: lightgbm
pipeline_tag: tabular-classification
tags:
- cricket
- sports-analytics
- win-probability
- lightgbm
- tabular-classification
datasets:
- {{user}}/doosra-cricket
---

# Doosra win probability (T20 and ODI)

The batting side's chance of winning a limited-overs cricket match, after any ball. It powers
**Match Replay** in [Doosra](https://github.com/sarthak213/Doosra), a cricket analytics app, and you can try it
in the [demo Space](https://huggingface.co/spaces/{{user}}/doosra-win-probability).

![Calibration on 2025+ T20 matches](reports/T20/reliability.png)

## Use

```python
from predict import WinProbability          # predict.py in this repo; needs only NumPy

wp = WinProbability("winprob-t20.json")
# 2024 T20 World Cup final: South Africa 151/4 after 16 overs, chasing 177 at Kensington Oval
wp.predict(innings=2, score=151, wickets=4, balls_left=24, target=177, venue_par=159, elo_diff=-123)   # 0.925
```

`wickets` is wickets fallen; `venue_par` the ground's typical first-innings total (leave it out if unknown);
`elo_diff` the batting side's Elo rating minus the bowling side's (0 = evenly matched). Use
`winprob-odi.json` for one-day matches.

## How it works

One model per format (T20, ODI) and per innings. Each is a blend, averaged on the log-odds scale, of:

- **gradient-boosted trees** (LightGBM) with monotone constraints, encoding cricket common sense: more
  wickets in hand, more balls left, a higher score or a stronger side can only help the batting side, and
  more runs needed or a higher required rate can only hurt;
- **a logistic regression** on the same features, which keeps the curve smooth from ball to ball.

**Features:** score, wickets in hand and balls left; in the chase also the target, runs needed and required
rate; the ground's par (the average of its last 20 first-innings totals **before** the match); gender; and
each side's **Elo rating** built from earlier results only, so nothing from the match or later leaks in.

**Data:** every ball of 17,000+ T20 and ODI matches (men's and women's, internationals and leagues) from the
[Doosra dataset](https://huggingface.co/datasets/{{user}}/doosra-cricket) (Cricsheet). Training uses matches with
6-ball overs and a decided result, with no rain rule and a full-length chase.

## Evaluation

Split by time: trained on matches up to 2022, early-stopped on 2023-24, and tested on everything from 2025
on, so the test is a genuine forecast of matches the model never saw.

| Test (2025+) | Matches | Log loss | Brier | AUC | Calibration error | Accuracy | Logistic alone | Trees alone | Par heuristic |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

The blend beats both of its halves and a hand-built par-score heuristic. It is well calibrated: when it says
70%, the batting side wins about 70% of the time (plots in `reports/`). On an ordinary ball (no wicket or
boundary) the prediction moves {100 * swing['T20']['mean']:.1f} points on average in T20
({100 * swing['ODI']['mean']:.1f} in ODIs), so the worm follows the game rather than noise.

![Accuracy through the match](reports/T20/by_over.png)

## Limitations

- **Limited-overs only.** Tests have draws and aren't covered.
- **Rain rules.** Rain-affected matches are left out of training. Scored with their revised target, they
  are approximate.
- **No player or pitch information** beyond the ground's par and team Elo. A side with its best batter still
  in is treated like any other side at that score.
- **Coverage.** Cricsheet coverage is uneven; some teams have few or no matches (for example, there are no
  Afghanistan matches), so their Elo ratings are weak.

## Licence

The data is from [Cricsheet](https://cricsheet.org) under the
[Open Data Commons Attribution License (ODC-By 1.0)](https://opendatacommons.org/licenses/by/1-0/); the model is
released under the same licence. Credit Cricsheet and Doosra if you use it.
"""


def space_readme() -> str:
    return """---
title: Doosra win probability
emoji: 🏏
colorFrom: green
colorTo: yellow
sdk: static
app_file: index.html
pinned: false
license: odc-by
short_description: Cricket win probability, ball by ball, for T20 and ODI
---

Cricket win probability for T20 and ODI matches: replay famous finals ball by ball, or try any situation.
Static page: the model runs in your browser (`predict.js`, checked against the Python predictor).
The model is [{user}/doosra-win-probability](https://huggingface.co/{user}/doosra-win-probability), from
[Doosra](https://github.com/sarthak213/Doosra). Data: [Cricsheet](https://cricsheet.org) (ODC-By 1.0).
"""


def build_ball_outcome() -> Path:
    """The ball-outcome model repo: per format the LightGBM model, its spec, the player-form snapshot, the
    report and calibration plot, plus the predictor. The card is written after trying the predictor."""
    src, out = ROOT / "ml" / "out" / "ball_outcome", fresh(OUT / "ball-outcome-model")
    shutil.copy(ROOT / "ml" / "hf" / "ball_outcome" / "predict_ball.py", out)
    for g in ("T20", "ODI"):
        low = g.lower()
        shutil.copy(src / g / "lightgbm.txt", out / f"lightgbm-{low}.txt")
        shutil.copy(src / g / "spec.json", out / f"spec-{low}.json")
        shutil.copy(src / g / "players.parquet", out / f"players-{low}.parquet")
        (out / "reports" / g).mkdir(parents=True)
        for f in ("report.json", "calibration.png"):
            shutil.copy(src / g / f, out / "reports" / g / f)
    card = ROOT / "ml" / "hf" / "ball_outcome" / "README.md"
    if card.exists():
        shutil.copy(card, out / "README.md")
    return out


def build_toolcall_data() -> Path:
    """The tool-calling dataset repo: the three splits, the tool list and system prompt, and the card."""
    src, out = ROOT / "ml" / "out" / "toolcall", fresh(OUT / "toolcall-data")
    for f in ("tools.json", "system_prompt.txt"):
        shutil.copy(src / f, out / f)
    keys = ("role", "content", "tool_calls", "tool_call_id", "name")
    for split in ("train", "validation", "test"):
        # every message with the same keys (null where absent), so datasets infers one schema for all rows
        rows = []
        for line in (src / f"{split}.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            r["messages"] = [{k: m.get(k) for k in keys} for m in r["messages"]]
            r.setdefault("original_question", None)
            r.pop("paraphrased", None)
            rows.append(json.dumps(r, ensure_ascii=False))
        (out / f"{split}.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    counts = {s: len((src / f"{s}.jsonl").read_text(encoding="utf-8").splitlines()) for s in ("train", "validation", "test")}
    intents, para = {}, 0
    for s in counts:
        for line in (src / f"{s}.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            intents[r["intent"]] = intents.get(r["intent"], 0) + 1
            para += "original_question" in r
    card = (ROOT / "ml" / "hf" / "toolcall" / "dataset_card.md").read_text(encoding="utf-8")
    (out / "README.md").write_text(card.format(
        total=sum(counts.values()), **counts, paraphrased_share=para / sum(counts.values()),
        intent_rows="\n".join(f"| {k} | {v:,} |" for k, v in sorted(intents.items(), key=lambda kv: -kv[1]))),
        encoding="utf-8")
    return out


TOOLCALL_SESSIONS = {
    "b": "about 4 hours of A100 time over two sessions (about 20 s a step); the first session disconnected at step 195 "
         "and training resumed from the step-150 checkpoint on Google Drive",
}


def build_toolcall_model(run: str) -> Path:
    """The card and loss plot for a fine-tuned tool-calling model repo (the model files are already there, pushed
    by the training notebook): ml/out/hf/toolcall-model-<run>/, published with `python ml/publish.py toolcall-<run>`."""
    sys.path.insert(0, str(ROOT / "ml" / "hf" / "toolcall"))
    import model_card
    from huggingface_hub import hf_hub_download
    repo = f"Sarthak213/doosra-qwen3.5-4b-toolcalls-{run}"
    log = json.loads(Path(hf_hub_download(repo, "run_log.json", force_download=True)).read_text(encoding="utf-8"))
    out = fresh(OUT / f"toolcall-model-{run}")
    model_card.loss_plot(log, out / "training_loss.png")
    evaluation = ROOT / "ml" / "hf" / "toolcall" / f"evaluation-{run}.md"      # written once ToolEval has results
    (out / "README.md").write_text(model_card.card(
        run, repo, log, evaluation.read_text(encoding="utf-8") if evaluation.exists() else None,
        TOOLCALL_SESSIONS.get(run, f"{log.get('train_seconds', 0) / 3600:.1f} hours on the GPU")), encoding="utf-8")
    return out


def main() -> None:
    reports = {g: json.loads((REPORTS / g / "report.json").read_text(encoding="utf-8")) for g in ("T20", "ODI")}
    model = fresh(OUT / "winprob-model")
    for g in ("t20", "odi"):
        shutil.copy(MODELS / f"winprob-{g}.json", model)
    shutil.copy(SRC / "predict.py", model)
    for g in ("T20", "ODI"):
        (model / "reports" / g).mkdir(parents=True)
        for f in ("report.json", "reliability.png", "by_over.png"):
            shutil.copy(REPORTS / g / f, model / "reports" / g / f)
    (model / "README.md").write_text(model_card(reports), encoding="utf-8")

    # a static Space (free on Hugging Face): the page runs the model in the browser (static/predict.js)
    space = fresh(OUT / "winprob-space")
    for f in ("index.html", "predict.js"):
        shutil.copy(SRC / "static" / f, space)
    for g in ("t20", "odi"):
        shutil.copy(MODELS / f"winprob-{g}.json", space)
    (space / "README.md").write_text(space_readme(), encoding="utf-8")
    replays = {}
    for name, mid in FINALS.items():
        r = replay.replay(mid)
        m = r["match"]
        replays[name] = {"team1": m["team1"], "team2": m["team2"], "summary": m["summary"],
                         "overs": m["overs_per_innings"] or 20,
                         "balls": [{k: b[k] for k in ("innings", "seq", "legal", "score", "wickets", "batting_team",
                                                      "wicket", "wp", "text")} for b in r["balls"]],
                         "moments": r["moments"]}
    (space / "replays.json").write_text(json.dumps(replays), encoding="utf-8")
    ball = build_ball_outcome()
    for d in (model, space, ball):
        size = sum(p.stat().st_size for p in d.rglob("*") if p.is_file())
        print(f"{d}: {len(list(d.rglob('*.*')))} files, {size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
