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

v1 was near-perfect on its templated tests but lost end to end: it couldn't write SQL (the compact tool list had
dropped the schema), answered with a template's headline figure instead of the one asked (sixes, balls faced), made
one call and stopped where a second was needed, and had learned answers with no figures from a template bug. Dataset
v2 fixed each of these.

### Held-out test set (dataset v1: unseen players, teams and venues)

First call of 300 conversations, and every step of 100 with the final answer checked against the tool results.

| Model | Right tool | Exact arguments | Conversations all right | Answers when it should | Answers fully grounded |
|---|---:|---:|---:|---:|---:|
| Qwen3.5 4B (long prompt) | 82% | 52% | 57% | 23% | 52% |
| Qwen3.5 9B (long prompt) | 88% | 57% | 56% | 90% | 69% |
| v1, run A | 98% | 93% | 92% | 100% | 99% |
| v1, run B at step 350 | 99% | 94% | 94% | 100% | 99% |
| v1, run B | 99% | 96% | 96% | 100% | 99% |
