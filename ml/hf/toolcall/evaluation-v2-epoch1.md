Measured with ToolEval, a local evaluation app, each model served by llama.cpp as Doosra runs it (Q4_K_M, thinking
off): the base models with the app's full prompt and tool list, the fine-tuned ones with the short prompt they were
trained on.

**End to end:** 40 open questions (Doosra's own evaluation set, written separately from the training templates and
kept out of training), each answered through a real tool loop against Doosra's database. 35 are graded automatically
on the facts the answer must contain (accepting name variants such as "V Kohli" for "Virat Kohli"); 5 are open-ended
(a qualification or definition to choose) and were checked by hand against the database.

### End to end (the same 40 questions for every model)

| Model | Correct of 40 | Auto-graded (35) | Reviewed (5) | Calls per question | Median s | 90th pct s |
|---|---:|---:|---:|---:|---:|---:|
| Qwen3.5 4B (long prompt) | **29** | 28 | 1 | 3.125 | 30.4 | 85.1 |
| Qwen3.5 9B (long prompt) | **30** | 27 | 3 | 1.95 | 26.7 | 72.6 |
| v1, run A | **16** | 14 | 2 | 1.75 | 12.3 | 25.6 |
| v1, run B at step 350 | **18** | 16 | 2 | 1.675 | 12.0 | 23.8 |
| v1, run B | **21** | 19 | 2 | 2 | 13.4 | 25.8 |
| v2 at epoch 1 | **33** | 30 | 3 | 1.575 | 13.1 | 23.7 |
| **v2** | **33** | 31 | 2 | 1.275 | 11.4 | 17.4 |

### Held-out test set (dataset v2: unseen players, teams and venues)

First call of 300 conversations, and every step of 100 with the final answer checked against the tool results.

| Model | Right tool | Exact arguments | Conversations all right | Answers when it should | Answers fully grounded |
|---|---:|---:|---:|---:|---:|
| Qwen3.5 4B (long prompt) | 75% | 42% | 48% | 35% | 70% |
| v2 at epoch 1 | 100% | 94% | 97% | 100% | 100% |
| **v2** | 100% | 93% | 96% | 100% | 100% |
