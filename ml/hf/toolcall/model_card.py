"""
The model card for a fine-tuned Doosra tool-calling model, from its run log (run_log.json in the model repo) and its
evaluation (ml/hf/toolcall/evaluation-<run>.md, written by ml/toolcall/eval_report.py from ToolEval's results).
Used by ml/build_hf.py (build_toolcall_model).
"""

from __future__ import annotations

DATA = {   # the dataset version each run trained on
    1: dict(n="7,000", train="5,854", valid="306", test="840", kinds=29, reworded="43%",
            seq="sequences up to 6,144 tokens (4.5% of conversations were cut short at the end, where the answer is; "
                "fixed in v2)"),
    2: dict(n="8,000", train="6,695", valid="345", test="960", kinds=31, reworded="46%",
            seq="sequences up to 7,424 tokens, above the longest conversation"),
}

V2 = "Sarthak213/doosra-qwen3.5-4b-toolcalls-v2"

RUNS = {
    "v2": dict(title="v2", data=2, about="rank 32, learning rate 1e-4, 2 epochs, dataset v2; **the model Doosra ships**",
               story="Unlike v1, validation loss kept falling into the second epoch (v2's broader data had more to "
                     "learn) and levelled off around step 500. The adapter at the end of epoch 1 was exported too "
                     "(`…-v2-epoch1`); the two tie on the open questions, and this one is faster and more accurate on "
                     "the auto-graded ones, so it's the one Doosra ships."),
    "v2-epoch1": dict(title="v2 at epoch 1", data=2,
                      about="rank 32, learning rate 1e-4, the v2 run's adapter at the end of epoch 1",
                      story="Exported from the end of the first epoch (step 419) of the v2 run, as a second candidate "
                            "from the same training. Doosra ships the end of epoch 2 (`…-v2`)."),
    "a": dict(title="v1, run A", data=1, about="rank 16, learning rate 2e-4, 1 epoch, dataset v1", superseded=True),
    "b": dict(title="v1, run B", data=1, about="rank 32, learning rate 1e-4, 2 epochs, dataset v1", superseded=True,
              story="It levelled off by the end of the first epoch: the second kept lowering the training loss but not "
                    "the validation loss."),
    "b-step350": dict(title="v1, run B at step 350", data=1, superseded=True,
                      about="rank 32, learning rate 1e-4, stopped at step 350 of 732 (about 1 epoch), dataset v1"),
}

SUPERSEDED = f"""> **Superseded by [v2]({{v2_url}}).** This first version is near-perfect on questions shaped like its training
> templates but answered fewer open questions right than the base model it started from. The evaluation below shows
> why, and [v2](https://huggingface.co/{V2}) fixes it. Kept for the comparison.

"""


def epoch_steps(log: dict) -> int:
    total = max(h.get("step", 0) for h in log["log_history"])
    return round((log.get("total_steps") or total) / max(log["config"]["epochs"], 1))


def card(name: str, repo: str, log: dict, evaluation: str | None, sessions_note: str) -> str:
    run = RUNS.get(name, {"title": name, "data": 2, "about": ""})
    d = DATA[run["data"]]
    c = log["config"]
    evals = [(h["step"], h["eval_loss"]) for h in log["log_history"] if "eval_loss" in h]
    first, best, last = evals[0], min(evals, key=lambda e: e[1]), evals[-1]
    epochs, steps = c["epochs"], last[0]
    length = (f"{steps} steps (the end of epoch 1) of a {epochs}-epoch run" if log.get("stopped_at")
              else f"{epochs} epoch{'s' if epochs != 1 else ''}, {steps} steps")
    batch = log.get("batch") or 2
    banner = SUPERSEDED.format(v2_url=f"https://huggingface.co/{V2}") if run.get("superseded") else ""
    lead = ("In Doosra's evaluation it answers **33 of 40** open questions right, against 30 for Qwen3.5 9B and 29 for "
            "the base 4B, about **2.5x faster** (see [Evaluation](#evaluation))." if run["data"] == 2 else
            "See [Evaluation](#evaluation) for how it compares with the base models and v2.")
    return f"""---
license: apache-2.0
base_model: Qwen/Qwen3.5-4B
library_name: gguf
pipeline_tag: text-generation
language:
- en
tags:
- tool-calling
- function-calling
- agents
- cricket
- sports-analytics
- gguf
- lora
- unsloth
- qwen3.5
datasets:
- Sarthak213/doosra-toolcalls
---

# Doosra tool caller: Qwen3.5 4B fine-tuned to use a cricket analytics toolkit ({run['title']})

{banner}[Doosra](https://github.com/sarthak213/Doosra) is a cricket analytics app with a local AI copilot: you ask a question,
and the model answers it by calling Doosra's 25 tools (leaderboards, player profiles, match-ups, venues, records,
match replays, charts, SQL…) over 11 million balls of [Cricsheet](https://cricsheet.org) data, then writes
an answer from the results.

This is Qwen3.5 4B **fine-tuned on {d['train']} of Doosra's own tool-calling conversations**, so the small model does
the job reliably, with a **prompt about 8x shorter** than the app gives general models (about 280 tokens instead
of 2,100, plus compact tool schemas), which matters on a laptop where reading the prompt is the slow part. {lead}

**{run['title']}**: {run['about']}.

## What it does

> **Who led in dot % during the death overs of the BPL?**

| | Call |
|---|---|
| Qwen3.5 4B | `leaderboard(metric="dot_pct", role="bowling", filters={{"competition": "BPL", "phase": "death"}}, limit=5)` |
| **This model** | `leaderboard(metric="dot_pct", role="bowling", filters={{"competition": "Bangladesh Premier League", "phase": "death"}})` |

The base model abbreviates the competition and cuts the table to 5 rows nobody asked for. The fine-tuned model makes
the exact call; Doosra runs it, and the answer is written from the result (this one is from the test set):

> **Rashid Khan** is top with **49.07** dot % (Bangladesh Premier League, male, death).
> - **Nasum Ahmed** (Khulna Tigers): 47.92
> - **Mohammad Irfan** (Rajshahi Royals): 45.61

## Files

| File | |
|---|---|
| `Qwen3.5-4B.Q4_K_M.gguf` | the merged model, 4-bit (2.8 GB), for llama.cpp and Doosra's built-in engine |
| `adapter_model.safetensors`, `adapter_config.json` | the LoRA adapter (rank {c['lora_r']}), to apply to `Qwen/Qwen3.5-4B` yourself |
| `Qwen3.5-4B.BF16-mmproj.gguf` | the base model's vision projector, unchanged (vision wasn't trained or tested) |
| `run_log.json` | the training run: settings, losses, timings |
| `training_loss.png` | the loss curves |

## Use

**In Doosra** (3.0 and later): Settings → AI model → *Qwen3.5 4B Doosra*. The app sends the short prompt and compact
tools this model was trained with.

**With llama.cpp** (any OpenAI-compatible client). The model emits tool calls; your code runs the tools. Give it
the system prompt and tool list it was trained with, from the dataset, with thinking off:

```bash
llama-server -hf {repo}:Q4_K_M --jinja --ctx-size 16384
```

```python
import json
from huggingface_hub import hf_hub_download
from openai import OpenAI

data = "Sarthak213/doosra-toolcalls"
tools = json.load(open(hf_hub_download(data, "tools.json", repo_type="dataset")))
system = open(hf_hub_download(data, "system_prompt.txt", repo_type="dataset"), encoding="utf-8").read()

client = OpenAI(base_url="http://127.0.0.1:8080/v1", api_key="none")
reply = client.chat.completions.create(
    model="local", temperature=0, tools=tools,
    messages=[{{"role": "system", "content": system}},
              {{"role": "user", "content": "Who led in dot % during the death overs of the BPL?"}}],
    extra_body={{"chat_template_kwargs": {{"enable_thinking": False}}}})
print(reply.choices[0].message.tool_calls)
```

Send each tool's result back as a `tool` message and call again; the model finishes with a `final_answer` call.

## Training

**Data:** [Sarthak213/doosra-toolcalls](https://huggingface.co/datasets/Sarthak213/doosra-toolcalls), version
{run['data']}: {d['n']} conversations ({d['train']} train, {d['valid']} validation, {d['test']} test) across {d['kinds']}
kinds of question, single calls and multi-step ones. Questions come from templates over real players, teams, venues
and competitions, and {d['reworded']} were reworded by Qwen3.5 9B; every tool call was **executed against the real
database**, and every answer was written from the results. About 15% of players, teams and venues appear **only in
the test split**, so the test measures names the model never saw.{" Version 2 adds single-figure questions, about 20 SQL patterns, recovery from a failed call, fixed answer templates, and keeps the end-to-end evaluation's players, grounds and facts out of training (see the dataset card)." if run['data'] == 2 else ""}

**Method:** LoRA on `unsloth/Qwen3.5-4B` with [Unsloth](https://github.com/unslothai/unsloth) and TRL, on the
language layers (attention and MLP; vision layers frozen), loss on the assistant's turns only (tool calls and
answers, not the prompt, question or tool results). Conversations are rendered with Qwen's chat template, the tool
list and thinking off, exactly as the app sends them.

| Setting | |
|---|---|
| LoRA | rank {c['lora_r']}, alpha {c['lora_alpha']}, dropout 0, all attention and MLP projections ({64_929_792 if c['lora_r'] == 32 else 32_464_896:,} trainable parameters) |
| Base weights | 16-bit (bfloat16), not 4-bit QLoRA |
| Optimiser | AdamW 8-bit, learning rate {c['learning_rate']:g}, cosine schedule, 5 warm-up steps, weight decay 0.01 |
| Batch | 16 conversations per step ({batch} × {16 // batch} accumulation), {d['seq']} |
| Length | {length} |
| Hardware | Google Colab, {log['gpu']} (40 GB) |
| Time | {sessions_note} |

![Loss curves](training_loss.png)

Validation loss fell from {first[1]:.3f} (step {first[0]}) to {best[1]:.4f} (step {best[0]}) and ended at {last[1]:.4f}.
{run.get('story', '')}

## Evaluation

{evaluation or "Being measured; results will be added here."}

## Limitations

- **Doosra's tools only.** It learned these 25 tools, their arguments and Doosra's answer style. It isn't a general
  assistant or a general tool caller, and it expects the short system prompt it was trained with.
- **Synthetic questions.** Training questions come from templates, partly reworded; real users ask in more ways.
  The end-to-end questions are the check on that.
- **SQL fine print.** It writes valid SQL on Doosra's schema but can miss a rule in the schema notes: asked how many
  matches were ties, it counted `winner IS NULL`, which also includes draws and no-results. Check SQL answers to
  unusual questions.
- **Names as the data has them.** Cricsheet names players as "V Kohli", "JJ Bumrah"; the tools resolve most forms,
  but unusual spellings may miss.
- **The data's coverage.** Answers are only as complete as Cricsheet: some competitions and teams are thin or missing
  (for example, there are no Afghanistan matches).

## Licence

The model is released under Apache 2.0, like [Qwen3.5 4B](https://huggingface.co/Qwen/Qwen3.5-4B). The training
data's cricket results are from [Cricsheet](https://cricsheet.org) under the
[Open Data Commons Attribution License](https://opendatacommons.org/licenses/by/1-0/).
"""


def loss_plot(log: dict, path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    train = [(h["step"], h["loss"]) for h in log["log_history"] if "loss" in h]
    val = [(h["step"], h["eval_loss"]) for h in log["log_history"] if "eval_loss" in h]
    every = val[1][0] - val[0][0] if len(val) > 1 else 50
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=150)
    ax.plot(*zip(*train), color="#2a78d6", linewidth=1.4, label="training (every 5 steps)")
    ax.plot(*zip(*val), color="#eb6834", linewidth=2, marker="o", markersize=4, label=f"validation (every {every} steps)")
    ax.set_yscale("log")
    end1 = epoch_steps(log)
    if max(s for s, _ in train) > end1:
        ax.axvline(end1, color="#7c7b76", linestyle="--", linewidth=1)
        ax.text(end1 + 6, ax.get_ylim()[1] * 0.6, "end of epoch 1", color="#52514e", fontsize=9)
    ax.set(xlabel="step (16 conversations each)", ylabel="loss (log scale)")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#e1e0da", linewidth=0.8)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
