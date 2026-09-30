"""
The model card for a fine-tuned Doosra tool-calling model, from its run log (run_log.json in the model repo) and,
when there is one, its ToolEval results. Used by ml/build_hf.py (build_toolcall_model).
"""

from __future__ import annotations

EPOCH_STEPS = 366          # 5,854 conversations / 16 per step

RUNS = {
    "a": "rank 16, learning rate 2e-4, 1 epoch",
    "b": "rank 32, learning rate 1e-4, 2 epochs",
    "b-step350": "rank 32, learning rate 1e-4, stopped at step 350 (about 1 epoch)",
}


def fmt_hours(seconds: float) -> str:
    return f"{seconds / 3600:.1f} hours"


def card(name: str, repo: str, log: dict, evaluation: str | None, sessions_note: str) -> str:
    c = log["config"]
    evals = [(h["step"], h["eval_loss"]) for h in log["log_history"] if "eval_loss" in h]
    first, best, last = evals[0], min(evals, key=lambda e: e[1]), evals[-1]
    epochs = c["epochs"]
    steps = last[0]
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

# Doosra tool caller: Qwen3.5 4B fine-tuned to use a cricket analytics toolkit ({name})

[Doosra](https://github.com/sarthak213/Doosra) is a cricket analytics app with a local AI copilot: you ask a question,
and the model answers it by calling Doosra's 25 tools (leaderboards, player profiles, match-ups, venues, records,
match replays, charts, SQL…) over 11 million balls of [Cricsheet](https://cricsheet.org) data, then writes
an answer from the results.

Qwen3.5 9B does this well. The 4B, the model for PCs with less memory, picks tools and arguments less reliably.
This is Qwen3.5 4B **fine-tuned on 5,854 of Doosra's own tool-calling conversations**, so the smaller model can do
the job, and does it with a **prompt about 8x shorter** (about 280 tokens instead of 2,100, plus compact tool
schemas), which matters on a laptop where reading the prompt is the slow part.

Run **{name.upper()}** of the project's comparison ({RUNS.get(name, "")}); see [Evaluation](#evaluation).

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
| `run_log.json` | the training run: settings, loss every 5 steps, validation loss every 50, timings |
| `training_loss.png` | the loss curves |

## Use

**In Doosra:** Settings → AI model → *Qwen3.5 4B Doosra* (from Doosra 3.0). The app sends the short prompt and
compact tools this model was trained with.

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

**Data:** [Sarthak213/doosra-toolcalls](https://huggingface.co/datasets/Sarthak213/doosra-toolcalls): 7,000
conversations (5,854 train, 306 validation, 840 test) covering all the tools across 29 kinds of question, single
calls and multi-step ones (a table then a chart; a player in two scopes). Questions come from templates over real
players, teams, venues and competitions, and 43% were reworded by Qwen3.5 9B; every tool call was **executed against
the real database**, and every answer was written from the results. About 15% of players, teams and venues appear
**only in the test split**, so the test measures names the model never saw.

**Method:** LoRA on `unsloth/Qwen3.5-4B` with [Unsloth](https://github.com/unslothai/unsloth) and TRL, on the
language layers (attention and MLP; vision layers frozen), loss on the assistant's turns only (tool calls and
answers, not the prompt, question or tool results). Conversations are rendered with Qwen's chat template, the tool
list and thinking off, exactly as the app sends them.

| Setting | |
|---|---|
| LoRA | rank {c['lora_r']}, alpha {c['lora_alpha']}, dropout 0, all attention and MLP projections ({64_929_792 if c['lora_r'] == 32 else 32_464_896:,} trainable parameters) |
| Base weights | 16-bit (bfloat16), not 4-bit QLoRA |
| Optimiser | AdamW 8-bit, learning rate {c['learning_rate']:g}, cosine schedule, 5 warm-up steps, weight decay 0.01 |
| Batch | 16 conversations per step ({log.get('batch', 2)} × {16 // (log.get('batch') or 2)} accumulation), sequences up to 6,144 tokens |
| Length | {epochs} epoch{'s' if epochs != 1 else ''}, {steps} steps |
| Hardware | Google Colab, {log['gpu']}, peak memory {log.get('peak_memory_gb', '?')} GB |
| Time | {sessions_note} |

![Loss curves](training_loss.png)

Validation loss fell from {first[1]:.3f} (step {first[0]}) to {best[1]:.4f} (step {best[0]}) and ended at {last[1]:.4f}.
{"It levelled off by the end of the first epoch (step " + str(EPOCH_STEPS) + "): the second epoch kept lowering the training loss but not the validation loss, so it fitted the training conversations more closely without doing better on new ones. The project's run A trains for one epoch, and a step-350 copy of this run is kept to test the difference." if epochs >= 2 else ""}

## Evaluation

{evaluation or '''Being measured with ToolEval, a local evaluation app, against the base 4B and 9B: the first call on
all 840 held-out test conversations (right tool, right arguments), every step of 100 conversations with their final
answers checked against the tool results, and 40 open questions end to end through Doosra's real tools. Results
will be added here.

Spot check on three held-out test questions (unseen players and competitions): all three calls exact; the base 4B got
one wrong (the example above).'''}

## Limitations

- **Doosra's tools only.** It learned these 25 tools, their arguments and Doosra's answer style. It isn't a general
  assistant or a general tool caller, and it expects the short system prompt it was trained with.
- **Synthetic questions.** Training questions come from templates, partly reworded; real users ask in more ways.
  The end-to-end questions are the check on that.
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
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=150)
    ax.plot(*zip(*train), color="#2a78d6", linewidth=1.4, label="training (every 5 steps)")
    ax.plot(*zip(*val), color="#eb6834", linewidth=2, marker="o", markersize=4, label="validation (every 50 steps)")
    ax.set_yscale("log")
    if max(s for s, _ in train) > EPOCH_STEPS:
        ax.axvline(EPOCH_STEPS, color="#7c7b76", linestyle="--", linewidth=1)
        ax.text(EPOCH_STEPS + 6, ax.get_ylim()[1] * 0.6, "end of epoch 1", color="#52514e", fontsize=9)
    ax.set(xlabel="step (16 conversations each)", ylabel="loss (log scale)")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#e1e0da", linewidth=0.8)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
