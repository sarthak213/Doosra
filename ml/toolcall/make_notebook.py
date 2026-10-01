"""
Write ml/toolcall/train.ipynb, the fine-tuning notebook (Google Colab, A100). Kept as code here so the notebook is
reviewable in diffs:

    python ml/toolcall/make_notebook.py

Version 2 is built for one run that has to count:
- the dataset is pinned to the v2 revision, and the GPU is checked before anything is downloaded;
- every conversation is measured with Qwen's tokenizer and the sequence limit set above the longest (v1's limit cut
  4.5% of conversations short, at the end, where the answer is);
- the worst-case batch is tried before training, so an out-of-memory error shows in minutes, not hours;
- two models come out of the run (end of epoch 1, end of epoch 2), each checked on held-out questions in the notebook
  and exported to its own repo; a re-run after a disconnect resumes training and skips finished exports.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

CELLS = [
("markdown", """# Doosra v3: fine-tuning Qwen3.5 4B to call Doosra's cricket tools (dataset v2)

Trains a LoRA adapter on [Sarthak213/doosra-toolcalls](https://huggingface.co/datasets/Sarthak213/doosra-toolcalls)
(version 2), checks it on held-out questions, and exports GGUFs (Q4_K_M) for Doosra's llama.cpp engine.

**One run, two models:** the adapter at the end of epoch 1 and at the end of epoch 2 are both checked and exported
(`…-toolcalls-v2-epoch1`, `…-toolcalls-v2`); ToolEval then picks the better one end to end.

**Before you run it**
1. *Runtime → Change runtime type → **A100 GPU***. The notebook stops at once on anything smaller.
2. The key icon in the left bar: a secret named `HF_TOKEN` (your Hugging Face **write** token) with notebook access on.
3. *Runtime → Run all*, and allow Google Drive. About 5.5 hours. Keep the tab open and the computer awake.
4. If the session drops: reconnect to an A100 and *Run all* again. Training resumes from its last checkpoint on
   Drive, and exports that already finished are skipped."""),
("code", """import os
os.environ["CUDA_VISIBLE_DEVICES"] = "0"
RUN = "v2"
CFG = dict(lora_r=32, lora_alpha=32, learning_rate=1e-4, epochs=2)   # run B's settings: the best end to end in v1
KEEP_EPOCHS = [1]            # also export the adapter as it was at the end of these epochs
BASE = "unsloth/Qwen3.5-4B"
DATASET = "Sarthak213/doosra-toolcalls"
REVISION = "02f59a9d7b1636feb686ca1a468f55c55230529c"   # dataset v2, pinned: a later update can't change this run
OUT_REPO = "Sarthak213/doosra-qwen3.5-4b-toolcalls-v2"
SANITY_N = 60                # held-out test questions each model answers in the notebook
HOURS = 20                   # stop (and save) after this much training
SMOKE = False                # True: 4 steps on 64 examples, any GPU, nothing exported -- a plumbing check only
print(RUN, CFG)"""),
("code", """%%capture
!pip install -q "unsloth==2026.9.12" || pip install -q unsloth
!pip install -q --upgrade "datasets>=3" "huggingface_hub>=0.30\""""),
("code", """# kernels for Qwen3.5's linear-attention layers (without them training falls back to much slower PyTorch code)
!pip install -q --no-deps fla-core flash-linear-attention
try:
    import fla; print("linear-attention kernels: fla", fla.__version__)
except Exception as e:  # only a speed-up: train without it rather than stop
    print("linear-attention kernels unavailable (training will be slower):", e)"""),
("code", """import os, json, time, glob, shutil, gc
import torch
from google.colab import userdata, drive
GPU = torch.cuda.get_device_name(0)
GPU_GB = torch.cuda.get_device_properties(0).total_memory / 1e9
BF16 = torch.cuda.is_bf16_supported()
print("GPU:", GPU, f"{GPU_GB:.0f} GB", "| bfloat16:", BF16)
if not SMOKE and not (BF16 and GPU_GB > 38):
    raise RuntimeError(f"This run needs an A100 (40 GB, bfloat16); this runtime has {GPU}. "
                     "Runtime -> Change runtime type -> A100 GPU, then Run all again.")
HF_TOKEN = userdata.get("HF_TOKEN")
os.environ["HF_TOKEN"] = HF_TOKEN
drive.mount("/content/drive")
WORK = "/content/drive/MyDrive/doosra-train"
CKPT = f"{WORK}/checkpoints-{RUN}" + ("-smoke" if SMOKE else "")
os.makedirs(CKPT, exist_ok=True)
RUN_LOG = {"run": RUN, "gpu": GPU, "config": CFG, "dataset_revision": REVISION,
           "started": time.strftime("%Y-%m-%d %H:%M:%S"), "events": []}"""),
("code", """from datasets import load_dataset
from huggingface_hub import hf_hub_download
data = load_dataset(DATASET, revision=REVISION)
TOOLS = json.load(open(hf_hub_download(DATASET, "tools.json", repo_type="dataset", revision=REVISION)))
assert any(t["function"]["name"] == "run_sql" and "Tables" in t["function"]["description"] for t in TOOLS), \\
    "tools.json isn't v2's (run_sql's schema is missing)"
if SMOKE:
    data["train"], data["validation"] = data["train"].select(range(64)), data["validation"].select(range(8))
print(data)"""),
("code", """from unsloth import FastModel
model, tokenizer = FastModel.from_pretrained(BASE, max_seq_length=8192, load_in_4bit=False, full_finetuning=False)
model = FastModel.get_peft_model(
    model, finetune_vision_layers=False, finetune_language_layers=True, finetune_attention_modules=True,
    finetune_mlp_modules=True, r=CFG["lora_r"], lora_alpha=CFG["lora_alpha"], lora_dropout=0, bias="none",
    use_gradient_checkpointing="unsloth", random_state=3407)
tok = getattr(tokenizer, "tokenizer", tokenizer)   # the text tokenizer inside the vision-language processor"""),
("code", """def chat(messages):
    \"\"\"Messages as the chat template wants them: tool-call arguments as objects, no empty fields.\"\"\"
    out = []
    for m in messages:
        m = {k: v for k, v in dict(m).items() if v is not None}
        if m.get("tool_calls"):
            m["tool_calls"] = [{**c, "function": {**c["function"], "arguments": json.loads(c["function"]["arguments"])}}
                               for c in m["tool_calls"]]
        out.append(m)
    return out

def render(example):
    return {"text": tok.apply_chat_template(chat(example["messages"]), tools=TOOLS, tokenize=False, enable_thinking=False)}

train = data["train"].map(render, remove_columns=data["train"].column_names)
valid = data["validation"].select(range(min(50, len(data["validation"])))).map(render, remove_columns=data["validation"].column_names)
LENS = [len(x) for x in tok(train["text"])["input_ids"]]
vlens = [len(x) for x in tok(valid["text"])["input_ids"]]
longest = max(LENS + vlens)
MAX_SEQ = -(-(longest + 16) // 256) * 256          # above the longest conversation: nothing is cut short
s = sorted(LENS)
print(f"tokens per conversation: median {s[len(s)//2]}, p99 {s[int(.99*len(s))]}, max {longest} -> MAX_SEQ {MAX_SEQ}")
assert MAX_SEQ <= 8192, "a conversation is longer than expected"
RUN_LOG.update(max_seq=MAX_SEQ, longest=longest)
print(train[0]["text"][-700:])"""),
("code", """from trl import SFTTrainer, SFTConfig
from transformers import TrainerCallback
from unsloth.chat_templates import train_on_responses_only

class TimeBudget(TrainerCallback):
    def on_step_end(self, args, state, control, **kw):
        if time.time() - T_START > HOURS * 3600:
            RUN_LOG["events"].append(f"stopped by the time budget at step {state.global_step}")
            control.should_save, control.should_training_stop = True, True

class KeepEpochs(TrainerCallback):
    \"\"\"Copy the checkpoint at the end of each epoch in KEEP_EPOCHS to its own folder on Drive (the rolling checkpoints
    are pruned), so that model can be exported too.\"\"\"
    def on_epoch_end(self, args, state, control, **kw):
        if any(abs(state.epoch - e) < 0.01 for e in KEEP_EPOCHS):
            control.should_save = True
    def on_save(self, args, state, control, **kw):
        for e in KEEP_EPOCHS:
            dst = f"{WORK}/keep-{RUN}-epoch{e}"
            if state.epoch >= e - 0.01 and not os.path.exists(f"{dst}/adapter_config.json"):
                shutil.copytree(f"{args.output_dir}/checkpoint-{state.global_step}", dst, dirs_exist_ok=True)
                RUN_LOG["events"].append(f"kept step {state.global_step} (epoch {state.epoch:.2f}) as {dst}")
                print("kept", dst)

def make_trainer(batch):
    args = SFTConfig(
        output_dir=CKPT, dataset_text_field="text", max_seq_length=MAX_SEQ,
        per_device_train_batch_size=batch, per_device_eval_batch_size=1, gradient_accumulation_steps=16 // batch,
        num_train_epochs=CFG["epochs"], max_steps=4 if SMOKE else -1, learning_rate=CFG["learning_rate"],
        lr_scheduler_type="cosine", warmup_steps=5, logging_steps=1 if SMOKE else 5,
        eval_strategy="steps", eval_steps=2 if SMOKE else 100, save_strategy="steps", save_steps=2 if SMOKE else 75,
        save_total_limit=2, optim="adamw_8bit", weight_decay=0.01, bf16=BF16, fp16=not BF16,
        prediction_loss_only=True, report_to="none", seed=3407)
    t = SFTTrainer(model=model, tokenizer=tok, train_dataset=train, eval_dataset=valid, args=args,
                   callbacks=[TimeBudget(), KeepEpochs()])
    # learn only the assistant's turns (tool calls and answers), not the prompt, question or tool results
    return train_on_responses_only(t, instruction_part="<|im_start|>user\\n", response_part="<|im_start|>assistant\\n")

BATCH = 2
trainer = make_trainer(BATCH)"""),
("code", """# The worst case first: one forward and backward pass on the longest conversations, so an out-of-memory error
# shows now and not hours into the run. If it doesn't fit, train one conversation at a time.
def probe(batch):
    idx = sorted(range(len(LENS)), key=lambda i: -LENS[i])[:batch]
    enc = tok([train[i]["text"] for i in idx], return_tensors="pt", padding=True).to("cuda")
    model.train()
    torch.cuda.reset_peak_memory_stats()
    loss = model(**enc, labels=enc["input_ids"]).loss
    loss.backward()
    model.zero_grad(set_to_none=True)
    return torch.cuda.max_memory_allocated() / 1e9

try:
    peak = probe(BATCH)
    print(f"longest batch fits: peak {peak:.1f} GB of {GPU_GB:.0f}")
    if peak > 0.92 * GPU_GB:
        raise torch.cuda.OutOfMemoryError("too close to the limit")
except torch.cuda.OutOfMemoryError as e:
    model.zero_grad(set_to_none=True); gc.collect(); torch.cuda.empty_cache()
    BATCH = 1
    trainer = make_trainer(BATCH)
    print(f"batch 2 doesn't fit ({e}); training with batch 1 x 16")
except Exception as e:  # the probe is a safeguard; if it can't run here, train as configured
    model.zero_grad(set_to_none=True); gc.collect(); torch.cuda.empty_cache()
    print("probe skipped:", repr(e)[:200])
RUN_LOG["batch"] = BATCH"""),
("code", """resume = bool(glob.glob(f"{CKPT}/checkpoint-*"))
T_START = time.time()
stats = trainer.train(resume_from_checkpoint=resume)
RUN_LOG.update(train_seconds=round(time.time() - T_START), resumed=resume, log_history=trainer.state.log_history,
               metrics=stats.metrics, peak_memory_gb=round(torch.cuda.max_memory_reserved() / 1e9, 1))
evals = [(h["step"], round(h["eval_loss"], 5)) for h in trainer.state.log_history if "eval_loss" in h]
print(stats.metrics, "| peak GPU memory GB:", RUN_LOG["peak_memory_gb"])
print("validation loss by step:", evals)
json.dump(RUN_LOG, open(f"{WORK}/run_log-{RUN}.json", "w"), indent=1, default=str)"""),
("code", """import re, random
from collections import defaultdict

def parse_call(text):
    \"\"\"The first tool call in Qwen's format: <function=NAME><parameter=KEY>VALUE</parameter>...\"\"\"
    m = re.search(r"<function=([\\w-]+)>(.*?)</function>", text, re.S)
    if not m:
        return None, {}
    args = {}
    for k, v in re.findall(r"<parameter=([\\w-]+)>\\s*(.*?)\\s*</parameter>", m.group(2), re.S):
        try:
            args[k] = json.loads(v)
        except json.JSONDecodeError:
            args[k] = v
    return m.group(1), args

def norm(x):
    if isinstance(x, dict):
        return {k.lower(): norm(v) for k, v in x.items() if v not in (None, "", [], {})}
    if isinstance(x, list):
        return [norm(v) for v in x]
    if isinstance(x, str):
        s = x.strip().lower()
        try:
            return float(s)
        except ValueError:
            return s
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else x

# the same questions for every model: spread over the kinds of question, held-out players, teams and venues
rng = random.Random(7)
by_kind = defaultdict(list)
for i, ex in enumerate(data["test"]):
    if not ex["intent"].endswith("_recover"):      # their reference first call is the deliberately wrong one
        by_kind[ex["intent"]].append(i)
SANITY = []
while len(SANITY) < min(SANITY_N, len(data["test"])):
    for kind in sorted(by_kind):
        if by_kind[kind] and len(SANITY) < SANITY_N:
            SANITY.append(by_kind[kind].pop(rng.randrange(len(by_kind[kind]))))

def sanity(m, t, label):
    \"\"\"Each question's first call against the reference: right tool, and exactly the right arguments. A check,
    never a blocker: if it fails, the export still runs.\"\"\"
    try:
        return _sanity(m, t, label)
    except Exception as e:
        print(f"{label}: check skipped ({e!r})"[:300])
        return {"error": repr(e)[:300]}

def _sanity(m, t, label):
    FastModel.for_inference(m)
    rows, per_kind = [], defaultdict(lambda: [0, 0])
    for i in SANITY:
        ex = data["test"][i]
        msgs = chat(ex["messages"][:2])
        gold = ex["messages"][2]["tool_calls"][0]["function"]
        enc = t.apply_chat_template(msgs, tools=TOOLS, add_generation_prompt=True, enable_thinking=False,
                                    return_tensors="pt", return_dict=True).to("cuda")
        out = m.generate(**enc, max_new_tokens=300, do_sample=False)
        name, args = parse_call(t.decode(out[0][enc["input_ids"].shape[1]:], skip_special_tokens=False))
        exact = name == gold["name"] and norm(args) == norm(json.loads(gold["arguments"]))
        rows.append((ex["intent"], name == gold["name"], exact))
        per_kind[ex["intent"]][0] += exact
        per_kind[ex["intent"]][1] += 1
    tool = sum(r[1] for r in rows) / len(rows)
    exact = sum(r[2] for r in rows) / len(rows)
    print(f"{label}: right tool {tool:.0%}, exact call {exact:.0%} on {len(rows)} held-out questions")
    print("  by kind:", ", ".join(f"{k} {a}/{b}" for k, (a, b) in sorted(per_kind.items())))
    return {"right_tool": round(tool, 4), "exact_call": round(exact, 4), "n": len(rows),
            "by_kind": {k: v for k, v in per_kind.items()}}

RUN_LOG["sanity"] = {"final": sanity(model, tok, f"epoch {CFG['epochs']}")}"""),
("code", """from huggingface_hub import HfApi

def export(m, processor, repo, log):
    \"\"\"The adapter, then a Q4_K_M GGUF, then the run log, to Hugging Face. Skipped if it already finished.\"\"\"
    flag = f"{WORK}/exported-{repo.split('/')[-1]}.done"
    if os.path.exists(flag):
        print("already exported:", repo)
        return
    t0 = time.time()
    m.push_to_hub(repo, token=HF_TOKEN)
    getattr(processor, "tokenizer", processor).push_to_hub(repo, token=HF_TOKEN)
    m.push_to_hub_gguf(repo, processor, quantization_method="q4_k_m", token=HF_TOKEN)
    log = {**log, "gguf_seconds": round(time.time() - t0), "finished": time.strftime("%Y-%m-%d %H:%M:%S")}
    path = f"{WORK}/run_log-{repo.split('/')[-1]}.json"
    json.dump(log, open(path, "w"), indent=1, default=str)
    HfApi(token=HF_TOKEN).upload_file(path_or_fileobj=path, path_in_repo="run_log.json", repo_id=repo)
    open(flag, "w").write(log["finished"])
    print("pushed to https://huggingface.co/" + repo)

if not SMOKE:
    export(model, tokenizer, OUT_REPO, RUN_LOG)"""),
("code", """# the epoch-1 model: the adapter kept at the end of epoch 1, checked on the same questions and exported
for e in KEEP_EPOCHS:
    path = f"{WORK}/keep-{RUN}-epoch{e}"
    if not os.path.exists(f"{path}/adapter_config.json"):
        print("no epoch", e, "checkpoint was kept"); continue
    del trainer, model; gc.collect(); torch.cuda.empty_cache()
    model, tokenizer = FastModel.from_pretrained(path, max_seq_length=MAX_SEQ, load_in_4bit=False)
    t = getattr(tokenizer, "tokenizer", tokenizer)
    RUN_LOG["sanity"][f"epoch{e}"] = sanity(model, t, f"epoch {e}")
    if not SMOKE:
        export(model, tokenizer, f"{OUT_REPO}-epoch{e}", {**RUN_LOG, "exported_from": f"keep-{RUN}-epoch{e}"})
    trainer = None

print("\\nheld-out check (first call):")
for k, v in RUN_LOG["sanity"].items():
    if "error" not in v:
        print(f"  {k:8} right tool {v['right_tool']:.0%}  exact call {v['exact_call']:.0%}")
json.dump(RUN_LOG, open(f"{WORK}/run_log-{RUN}.json", "w"), indent=1, default=str)
print("Done. Disconnect the runtime (Runtime -> Disconnect and delete runtime) to stop using compute units.")"""),
]


def main() -> None:
    nb = {"nbformat": 4, "nbformat_minor": 5,
          "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"},
                       "accelerator": "GPU", "colab": {"gpuType": "A100"}},
          "cells": []}
    for i, (kind, src) in enumerate(CELLS):
        cell = {"cell_type": kind, "id": f"cell{i}", "metadata": {}, "source": src.splitlines(keepends=True)}
        if kind == "code":
            cell.update(execution_count=None, outputs=[])
        nb["cells"].append(cell)
    (HERE / "train.ipynb").write_text(json.dumps(nb, indent=1), encoding="utf-8")
    print("wrote", HERE / "train.ipynb")


if __name__ == "__main__":
    main()
