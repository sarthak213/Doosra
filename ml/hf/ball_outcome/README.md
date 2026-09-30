---
license: odc-by
library_name: lightgbm
pipeline_tag: tabular-classification
tags:
- cricket
- sports-analytics
- expected-runs
- lightgbm
- tabular-classification
datasets:
- Sarthak213/doosra-cricket
---

# Doosra ball outcome: the next ball, with the batter and bowler in it (T20 and ODI)

For a ball faced, the chance of each result (**dot, 1, 2, 3, 4, 6, out**), and so the expected runs, given the
match situation **and both players' career form**. Built from 5.8 million balls faced from the
[Doosra dataset](https://huggingface.co/datasets/Sarthak213/doosra-cricket) (Cricsheet).

## Why

"Expected" cricket stats (true strike rate, runs above expected) compare what a player did with what an average
player would do in the same situation. [Doosra](https://github.com/sarthak213/Doosra) computes that baseline with a
lookup table: the average outcome per format, gender, year, innings, over and wickets down. This model asks how much
better the next ball can be predicted by also knowing *who* is batting and bowling, and how set the batter is.

## Use

```python
from predict_ball import BallOutcome          # predict_ball.py in this repo: pip install lightgbm numpy duckdb

bo = BallOutcome("T20")
# a chase: 27 needed off 12, 4 down, the batter on 45 off 30
bo.predict(batter="V Kohli", bowler="JJ Bumrah", innings=2, over=18, ball=1, wickets=4, score=150,
           balls_left=12, target=177, venue_par=165, bat_inn_balls=30, bat_inn_runs=45)
```

| Batter v bowler (same situation) | Dot | 1 | 4 | 6 | Out | Expected runs |
|---|---:|---:|---:|---:|---:|---:|
| Kohli v Bumrah | 18% | 41% | 13% | 9% | 6% | 1.76 |
| Kohli v Rohit Sharma (part-time bowler) | 12% | 36% | 12% | 17% | 6% | 2.21 |
| Russell v Bumrah | 25% | 29% | 14% | 17% | 9% | 1.99 |
| Bumrah (batting) v Bumrah | 28% | 45% | 8% | 2% | 9% | 1.07 |

Players are looked up by Cricsheet name in `players-t20.parquet` / `players-odi.parquet` (career totals up to the
dataset's build date); an unknown name counts as an average player. `over` is 0-based, as in Cricsheet.

## How it works

One LightGBM multiclass model per format.

**Situation features:** innings, over, ball of the over, wickets down, score, balls left, in the chase the runs
needed and required rate, the ground's par (its last 20 first-innings totals before the match), and gender.

**Player features:**

- **Batter:** career balls faced and, per ball, runs, dismissals, dots, fours and sixes.
- **The batter's current innings:** balls faced and runs so far, i.e. how set they are.
- **Bowler:** career balls bowled and, per ball, runs conceded off the bat, wickets, dots, fours and sixes.

All career figures come from **earlier matches only**. Rates are shrunk toward the average, as if every player
started with 120 average balls, so a newcomer looks like an average player until the balls add up.

**Split:** trained on matches up to 2022, early-stopped on 2023-24, tested on 2025 onwards.

## Evaluation (matches from 2025 on)

**T20** (690,105 balls faced):

| | Log loss (7 outcomes) | Dismissal log loss | Expected runs RMSE |
|---|---:|---:|---:|
| **This model** | **1.398** | **0.2111** | **1.526** |
| Same model without player form | 1.418 | 0.2121 | 1.541 |
| Outcome mix seen in each situation (format, innings, over, wickets) | 1.432 | 0.2129 | 1.552 |
| Doosra's `ball_expectation` table | | 0.2118 | 1.549 |
| Overall outcome mix | 1.473 | 0.2155 | 1.575 |

**ODI** (395,625 balls faced):

| | Log loss (7 outcomes) | Dismissal log loss | Expected runs RMSE |
|---|---:|---:|---:|
| **This model** | **1.186** | **0.1259** | **1.272** |
| Same model without player form | 1.199 | 0.1264 | 1.282 |
| Outcome mix seen in each situation | 1.210 | 0.1272 | 1.288 |
| Doosra's `ball_expectation` table | | 0.1258 | 1.284 |
| Overall outcome mix | 1.243 | 0.1295 | 1.300 |

A single ball is mostly noise, so every gain is small in absolute terms. Relative to knowing only the overall mix
of outcomes, the model captures about **1.7 to 1.8 times** the information of the situation alone. On expected runs it
beats the `ball_expectation` lookup in both formats. On dismissals it is ahead in T20 and level in ODIs.

That table is built from all years, the test years included, so it has seen the answers for its own cells. The
model hasn't.

Most important features in T20: the score, the batter's runs so far this innings, balls left, the over, the
batter's career six rate. Calibration plots are in `reports/`.

## Limitations

- **Career form, not recent form:** a player's whole career in the format counts equally.
- **No pitch, weather or bowler type** beyond the ground's par, and no head-to-head record between the two players.
- **Players are matched by Cricsheet name** within a format and gender; namesakes can merge.
- **Coverage follows Cricsheet,** which has gaps (for example, no Afghanistan matches).

## Licence

Data from [Cricsheet](https://cricsheet.org) under the
[Open Data Commons Attribution License (ODC-By 1.0)](https://opendatacommons.org/licenses/by/1-0/); the model and
player snapshots are released under the same licence. Credit Cricsheet and Doosra if you use them.
