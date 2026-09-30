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
   two configurations.
4. *Run all.* Checkpoints are saved as training goes, so if a session drops, run all again and it resumes."""),
("code", """RUN = "A"            # "A" (Kaggle) or "B" (Colab)
SMOKE = False        # True: a few steps on a few examples, to check the notebook end to end (~10 minutes)

CONFIGS = {
    "A": dict(lora_r=16, lora_alpha=16, learning_rate=2e-4, epochs=2),
    "B": dict(lora_r=32, lora_alpha=32, learning_rate=1e-4, epochs=3),
}
CFG = CONFIGS[RUN]
BASE = "unsloth/Qwen3.5-4B"
DATASET = "Sarthak213/doosra-toolcalls"
OUT_REPO = f"Sarthak213/doosra-qwen3.5-4b-toolcalls-{RUN.lower()}"
MAX_SEQ = 4096
print(RUN, CFG)"""),
("code", """%%capture
!pip install -q unsloth
!pip install -q --upgrade "datasets>=3" "huggingface_hub>=0.30\""""),
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
if SMOKE:
    data["train"], data["validation"] = data["train"].select(range(64)), data["validation"].select(range(16))
print(data)"""),
("code", """from unsloth import FastModel
model, tokenizer = FastModel.from_pretrained(BASE, max_seq_length=MAX_SEQ, load_in_4bit=True, full_finetuning=False)
model = FastModel.get_peft_model(
    model, finetune_vision_layers=False, finetune_language_layers=True, finetune_attention_modules=True,
    finetune_mlp_modules=True, r=CFG["lora_r"], lora_alpha=CFG["lora_alpha"], lora_dropout=0, bias="none",
    random_state=3407)
tok = getattr(tokenizer, "tokenizer", tokenizer)   # the text tokenizer inside a vision-language processor"""),
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
valid = data["validation"].map(render, remove_columns=data["validation"].column_names)
lengths = [len(tok(t)["input_ids"]) for t in train.select(range(min(300, len(train))))["text"]]
print("tokens per example: median", sorted(lengths)[len(lengths) // 2], "max", max(lengths))
print(train[0]["text"][-1500:])"""),
("code", """from trl import SFTTrainer, SFTConfig
from unsloth.chat_templates import train_on_responses_only
args = SFTConfig(
    output_dir=f"{WORK}/checkpoints-{RUN}", dataset_text_field="text", max_seq_length=MAX_SEQ,
    per_device_train_batch_size=2, gradient_accumulation_steps=8, num_train_epochs=CFG["epochs"],
    max_steps=6 if SMOKE else -1, learning_rate=CFG["learning_rate"], lr_scheduler_type="cosine", warmup_ratio=0.03,
    logging_steps=5, eval_strategy="steps", eval_steps=3 if SMOKE else 100, save_strategy="steps",
    save_steps=3 if SMOKE else 100, save_total_limit=2, optim="adamw_8bit", weight_decay=0.01, fp16=True,
    report_to="none", seed=3407)
trainer = SFTTrainer(model=model, tokenizer=tok, train_dataset=train, eval_dataset=valid, args=args)
# learn only the assistant's turns (tool calls and answers), not the prompt, question or tool results
trainer = train_on_responses_only(trainer, instruction_part="<|im_start|>user\\n",
                                  response_part="<|im_start|>assistant\\n")"""),
("code", """import glob
resume = bool(glob.glob(f"{WORK}/checkpoints-{RUN}/checkpoint-*"))
t0 = time.time()
stats = trainer.train(resume_from_checkpoint=resume)
RUN_LOG["train_seconds"] = round(time.time() - t0)
RUN_LOG["resumed"] = resume
RUN_LOG["log_history"] = trainer.state.log_history
RUN_LOG["metrics"] = stats.metrics
print(stats.metrics)"""),
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
