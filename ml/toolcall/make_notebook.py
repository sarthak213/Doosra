"""
Write ml/toolcall/train.ipynb, the fine-tuning notebook for Kaggle and Google Colab (one file for both:
it detects where it runs). Kept as code here so the notebook is reviewable in diffs:

    python ml/toolcall/make_notebook.py
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

CELLS = [
("markdown", """# Doosra: fine-tuning Qwen3.5 4B to call Doosra's cricket tools

Trains a LoRA adapter on [Sarthak213/doosra-toolcalls](https://huggingface.co/datasets/Sarthak213/doosra-toolcalls):
conversations where a cricket question is answered by calling Doosra's tools and writing an answer from their results.
Exports a GGUF (Q4_K_M) that Doosra's built-in llama.cpp engine runs, and pushes it to Hugging Face.

**Before you run it**
1. **GPU:** Kaggle: *Settings → Accelerator → GPU T4 x2* (one is used) and *Internet on*.
   Colab: *Runtime → Change runtime type → T4 GPU*.
2. **Secret:** add your Hugging Face write token as a secret named `HF_TOKEN`. Kaggle: *Add-ons → Secrets*.
   Colab: the key icon in the left bar, with notebook access on.
3. **Configuration:** set `RUN` in the next cell: `"A"` on Kaggle, `"B"` on Colab, so the two runs compare
   two configurations (LoRA rank 16 at a higher learning rate vs rank 32 at a lower one; 2 epochs each).
4. *Run all.* Checkpoints are saved as training goes, so if a session drops, run all again and it resumes."""),
("code", """import os
os.environ["CUDA_VISIBLE_DEVICES"] = "0"   # one GPU: Kaggle's second T4 would split the model and slow it down
RUN = "A"            # "A" (Kaggle) or "B" (Colab)
SMOKE = False        # True: a few steps on a few examples, to check the notebook end to end (~20 minutes)
TRAIN_EXAMPLES = 1600  # a shuffled subset: at ~3 min per 16 examples on a T4 (float32), 2 epochs of 1,600 is ~10 h
HOURS = 10.5         # stop training (and save) after this, leaving time for the GGUF export in a 12-hour session

CONFIGS = {
    "A": dict(lora_r=16, lora_alpha=16, learning_rate=2e-4, epochs=2),
    "B": dict(lora_r=32, lora_alpha=32, learning_rate=1e-4, epochs=2),
}
CFG = CONFIGS[RUN]
BASE = "unsloth/Qwen3.5-4B"
DATASET = "Sarthak213/doosra-toolcalls"
OUT_REPO = f"Sarthak213/doosra-qwen3.5-4b-toolcalls-{RUN.lower()}"
MAX_SEQ = 6144       # conversations run to ~5.4k tokens with the tool list
print(RUN, CFG)"""),
("code", """%%capture
!pip install -q unsloth
!pip install -q --upgrade "datasets>=3" "huggingface_hub>=0.30"
# kernels for Qwen3.5's linear-attention layers (without them transformers falls back to slow PyTorch code)
!pip install -q --no-deps fla-core flash-linear-attention"""),
("code", """import os, json, time, platform
ON_KAGGLE = os.path.exists("/kaggle")
WORK = "/kaggle/working" if ON_KAGGLE else "/content"
if ON_KAGGLE:
    from kaggle_secrets import UserSecretsClient
    HF_TOKEN = UserSecretsClient().get_secret("HF_TOKEN")
else:
    from google.colab import userdata
    HF_TOKEN = userdata.get("HF_TOKEN")
os.environ["HF_TOKEN"] = HF_TOKEN
import torch
print("platform:", "Kaggle" if ON_KAGGLE else "Colab", "| GPU:", torch.cuda.get_device_name(0),
      "| memory GB:", round(torch.cuda.get_device_properties(0).total_memory / 1e9, 1))
RUN_LOG = {"run": RUN, "platform": "Kaggle" if ON_KAGGLE else "Colab", "gpu": torch.cuda.get_device_name(0),
           "config": CFG, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "events": []}"""),
("code", """from datasets import load_dataset
from huggingface_hub import hf_hub_download
data = load_dataset(DATASET)
TOOLS = json.load(open(hf_hub_download(DATASET, "tools.json", repo_type="dataset")))
data["train"] = data["train"].shuffle(seed=3407).select(range(min(TRAIN_EXAMPLES, len(data["train"]))))
N_TRAIN = len(data["train"])
if SMOKE:
    data["train"], data["validation"] = data["train"].select(range(64)), data["validation"].select(range(8))
print(data)"""),
("code", """from unsloth import FastModel
model, tokenizer = FastModel.from_pretrained(BASE, max_seq_length=MAX_SEQ, load_in_4bit=True, full_finetuning=False)
model = FastModel.get_peft_model(
    model, finetune_vision_layers=False, finetune_language_layers=True, finetune_attention_modules=True,
    finetune_mlp_modules=True, r=CFG["lora_r"], lora_alpha=CFG["lora_alpha"], lora_dropout=0, bias="none",
    use_gradient_checkpointing="unsloth", random_state=3407)
tok = getattr(tokenizer, "tokenizer", tokenizer)   # the text tokenizer inside a vision-language processor
try:
    import fla
    print("linear-attention kernels: fla", fla.__version__)
except Exception as e:
    print("linear-attention kernels unavailable:", e)"""),
("code", """def render(example):
    \"\"\"The conversation in Qwen's chat format, with the tool list, as the app will send it (thinking off).\"\"\"
    msgs = []
    for m in example["messages"]:
        m = dict(m)
        if m.get("tool_calls"):
            m["tool_calls"] = [{**c, "function": {**c["function"], "arguments": json.loads(c["function"]["arguments"])}}
                               for c in m["tool_calls"]]
        msgs.append({k: v for k, v in m.items() if v is not None})
    text = tok.apply_chat_template(msgs, tools=TOOLS, tokenize=False, enable_thinking=False)
    return {"text": text}

train = data["train"].map(render, remove_columns=data["train"].column_names)
valid = data["validation"].select(range(min(50, len(data["validation"])))).map(
    render, remove_columns=data["validation"].column_names)
lengths = [len(tok(t)["input_ids"]) for t in train.select(range(min(300, len(train))))["text"]]
print("tokens per example: median", sorted(lengths)[len(lengths) // 2], "max", max(lengths))
print(train[0]["text"][-1500:])"""),
("code", """from trl import SFTTrainer, SFTConfig
from transformers import TrainerCallback
from unsloth.chat_templates import train_on_responses_only

class TimeBudget(TrainerCallback):
    \"\"\"Save and stop once HOURS of training have passed, so a long run still exports within the session.\"\"\"
    def on_step_end(self, args, state, control, **kw):
        if time.time() - T_START > HOURS * 3600:
            RUN_LOG["events"].append(f"stopped by the time budget at step {state.global_step}")
            control.should_save, control.should_training_stop = True, True

args = SFTConfig(
    output_dir=f"{WORK}/checkpoints-{RUN}", dataset_text_field="text", max_seq_length=MAX_SEQ,
    per_device_train_batch_size=1, per_device_eval_batch_size=1,   # eval at batch 8 ran out of memory
    gradient_accumulation_steps=16, num_train_epochs=CFG["epochs"],
    max_steps=4 if SMOKE else -1, learning_rate=CFG["learning_rate"], lr_scheduler_type="cosine", warmup_steps=5,
    logging_steps=1 if SMOKE else 5, eval_strategy="steps", eval_steps=2 if SMOKE else 50, save_strategy="steps",
    save_steps=2 if SMOKE else 25, save_total_limit=2, optim="adamw_8bit", weight_decay=0.01, fp16=True,
    prediction_loss_only=True, report_to="none", seed=3407)
trainer = SFTTrainer(model=model, tokenizer=tok, train_dataset=train, eval_dataset=valid, args=args,
                     callbacks=[TimeBudget()])
# learn only the assistant's turns (tool calls and answers), not the prompt, question or tool results
trainer = train_on_responses_only(trainer, instruction_part="<|im_start|>user\\n",
                                  response_part="<|im_start|>assistant\\n")"""),
("code", """import glob
resume = bool(glob.glob(f"{WORK}/checkpoints-{RUN}/checkpoint-*"))
T_START = time.time()
stats = trainer.train(resume_from_checkpoint=resume)
RUN_LOG["train_seconds"] = round(time.time() - T_START)
RUN_LOG["resumed"] = resume
RUN_LOG["log_history"] = trainer.state.log_history
RUN_LOG["metrics"] = stats.metrics
RUN_LOG["peak_memory_gb"] = round(torch.cuda.max_memory_reserved() / 1e9, 1)
print(stats.metrics, "| peak GPU memory GB:", RUN_LOG["peak_memory_gb"])
per_step = stats.metrics["train_runtime"] / max(trainer.state.global_step, 1)
full_steps = N_TRAIN * CFG["epochs"] // 16 if SMOKE else trainer.state.global_step
print(f"{per_step:.0f} s per step; a full run is ~{full_steps} steps = ~{per_step * full_steps / 3600:.1f} hours")"""),
("code", """# three held-out questions, answered by the fine-tuned model with the app's tool list
FastModel.for_inference(model)
for ex in data["test"].select(range(3)):
    msgs = ex["messages"][:2]
    ids = tok.apply_chat_template(msgs, tools=TOOLS, add_generation_prompt=True, enable_thinking=False,
                                  return_tensors="pt").to("cuda")
    out = model.generate(input_ids=ids, max_new_tokens=160, do_sample=False)
    print("Q:", msgs[1]["content"]); print(tok.decode(out[0][ids.shape[1]:], skip_special_tokens=False)[:400]); print()"""),
("code", """# the adapter, then a Q4_K_M GGUF for Doosra's llama.cpp engine, both to Hugging Face
model.push_to_hub(OUT_REPO, token=HF_TOKEN)
tok.push_to_hub(OUT_REPO, token=HF_TOKEN)
t0 = time.time()
model.push_to_hub_gguf(OUT_REPO, tokenizer, quantization_method="q4_k_m", token=HF_TOKEN)
RUN_LOG["gguf_seconds"] = round(time.time() - t0)
RUN_LOG["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
from huggingface_hub import HfApi
json.dump(RUN_LOG, open(f"{WORK}/run_log.json", "w"), indent=1, default=str)
HfApi(token=HF_TOKEN).upload_file(path_or_fileobj=f"{WORK}/run_log.json", path_in_repo="run_log.json", repo_id=OUT_REPO)
print("pushed to https://huggingface.co/" + OUT_REPO)"""),
]


def main() -> None:
    nb = {"nbformat": 4, "nbformat_minor": 5,
          "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"},
                       "accelerator": "GPU", "colab": {"gpuType": "T4"}},
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
