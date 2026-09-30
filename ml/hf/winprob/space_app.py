"""
Doosra win probability: a Hugging Face Space (Gradio).

  What if:           any T20 or ODI situation -> the batting side's chance of winning
  Replay a final:    famous finals ball by ball, with the moments that swung them

The model and its code are the same as in Doosra (https://github.com/sarthak213/Doosra).
"""

import json
from pathlib import Path

import gradio as gr
import plotly.graph_objects as go

from predict import WinProbability

HERE = Path(__file__).parent
MODELS = {"T20": WinProbability(HERE / "winprob-t20.json"), "ODI": WinProbability(HERE / "winprob-odi.json")}
REPLAYS = json.loads((HERE / "replays.json").read_text(encoding="utf-8"))
BRASS, BLUE, INK, MUTED = "#b08a3a", "#3987e5", "#f1e8d6", "#a79c87"


def what_if(fmt, innings, score, wickets, overs_bowled, target, venue_par, strength, women):
    model = MODELS[fmt]
    total = 50 if fmt == "ODI" else 20
    whole, part = divmod(round(overs_bowled * 10), 10)
    balls_left = max(total * 6 - (whole * 6 + min(part, 5)), 0)
    chase = innings == "Chasing"
    if chase and not target:
        return "Enter the target for a chase.", None
    p = model.predict(innings=2 if chase else 1, score=int(score), wickets=int(wickets), balls_left=balls_left,
                      target=int(target) if chase else None, venue_par=venue_par or None, female=women,
                      elo_diff=strength)
    side = "chasing" if chase else "batting first"
    need = f" (needing {int(target) - int(score)} off {balls_left} balls)" if chase else ""
    text = f"## {p:.0%}\nThe side {side} at {int(score)}/{int(wickets)} after {overs_bowled} overs{need} wins about {p:.0%} of the time."
    fig = go.Figure(go.Bar(x=[p, 1 - p], y=["Batting side", "Bowling side"], orientation="h",
                           marker_color=[BRASS, BLUE], text=[f"{p:.0%}", f"{1 - p:.0%}"], textposition="inside"))
    fig.update_layout(height=180, margin=dict(l=10, r=10, t=10, b=10), xaxis=dict(range=[0, 1], tickformat=".0%"),
                      template="plotly_white", showlegend=False)
    return text, fig


def replay(name):
    r = REPLAYS[name]
    overs = r["overs"]
    x = [(b["innings"] - 1) * overs + b["legal"] / 6 for b in r["balls"]]
    y = [100 * b["wp"] for b in r["balls"]]
    hover = [f"{b['batting_team']} {b['score']}/{b['wickets']}<br>{b['text']}<br>{r['team1']} {100 * b['wp']:.0f}%"
             for b in r["balls"]]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=y, mode="lines", line=dict(color=BRASS, width=2), hovertext=hover,
                             hoverinfo="text", name=f"{r['team1']}'s chance"))
    wk = [i for i, b in enumerate(r["balls"]) if b["wicket"]]
    fig.add_trace(go.Scatter(x=[x[i] for i in wk], y=[y[i] for i in wk], mode="markers", hoverinfo="skip",
                             marker=dict(color="#16281d", size=7, line=dict(color="white", width=1.5)), name="wicket"))
    at = {(b["innings"], b["seq"]): i for i, b in enumerate(r["balls"])}
    for n, m in enumerate(r["moments"], 1):
        i = at.get((m["innings"], m["seq"]))
        if i is not None:
            fig.add_annotation(x=x[i], y=y[i], text=str(n), showarrow=False, bgcolor=BLUE, font=dict(color="white"),
                               borderpad=3)
    fig.add_hline(y=50, line_dash="dash", line_color=MUTED)
    fig.add_vline(x=overs, line_color=MUTED)
    step = 10 if overs >= 40 else 5
    ticks = list(range(0, 2 * overs + 1, step))
    fig.update_layout(height=440, margin=dict(l=10, r=10, t=70, b=10), template="plotly_white",
                      yaxis=dict(range=[0, 100], ticksuffix="%", title=f"{r['team1']}'s chance of winning"),
                      xaxis=dict(title="Overs: first innings, then the chase", tickvals=ticks,
                                 ticktext=[str(t if t <= overs else t - overs) if t != overs else "break" for t in ticks]),
                      legend=dict(orientation="h", y=1.02, yanchor="bottom", x=0),
                      title=dict(text=f"{r['team1']} v {r['team2']}: {r['summary']}", y=0.97))
    rows = [[n, m["text"], m["score"], m["team"], f"{m['before']:.0%} → {m['after']:.0%}"]
            for n, m in enumerate(r["moments"], 1)]
    return fig, rows


with gr.Blocks(title="Doosra win probability") as demo:
    gr.Markdown("# Doosra: cricket win probability\nThe batting side's chance of winning a T20 or ODI after any ball, "
                "from a model trained on 17,000 matches of ball-by-ball data "
                "([Cricsheet](https://cricsheet.org), ODC-By 1.0). Part of [Doosra](https://github.com/sarthak213/Doosra).")
    with gr.Tab("Replay a final"):
        pick = gr.Dropdown(list(REPLAYS), value=next(iter(REPLAYS)), label="Match")
        worm = gr.Plot()
        moments = gr.Dataframe(headers=["#", "Moment", "Score", "Helped", "Chance"], interactive=False)
        pick.change(replay, pick, [worm, moments])
        demo.load(replay, pick, [worm, moments])
    with gr.Tab("What if"):
        with gr.Row():
            fmt = gr.Radio(["T20", "ODI"], value="T20", label="Format")
            inn = gr.Radio(["Batting first", "Chasing"], value="Chasing", label="Innings")
            women = gr.Checkbox(label="Women's cricket")
        with gr.Row():
            score = gr.Number(151, label="Score", precision=0)
            wkts = gr.Slider(0, 9, 4, step=1, label="Wickets down")
            overs_bowled = gr.Number(16.0, label="Overs bowled (e.g. 16.3)")
            target = gr.Number(177, label="Target (chasing)", precision=0)
        with gr.Row():
            par = gr.Number(160, label="Ground's par first-innings score (optional)")
            strength = gr.Slider(-400, 400, 0, step=10, label="Team strength: batting side's Elo minus the bowling side's")
        go_btn = gr.Button("Win probability", variant="primary")
        out_text, out_fig = gr.Markdown(), gr.Plot()
        go_btn.click(what_if, [fmt, inn, score, wkts, overs_bowled, target, par, strength, women], [out_text, out_fig])

if __name__ == "__main__":
    demo.launch()
