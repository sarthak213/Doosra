"""
Question patterns for the tool-calling training data. Each intent draws real entities from the pools and
returns an Example: the user's question and the gold tool calls, in order. A later call can use the
table of an earlier one ("T1"). The generator executes every call against the database and drops
examples whose calls fail or come back empty, so every example is grounded in real data.

Phrasings vary (several templates per intent, casual and precise); a paraphrase pass later adds more.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from entities import FORMAT_LABEL, Player, Pools

FORMATS = ["T20I", "ODI", "Test"]
DEFAULT_BAT = {"matches", "innings", "runs", "average", "strike_rate", "true_sr", "hundreds", "fifties"}  # player_stats
DEFAULT_BOWL = {"matches", "wickets", "average", "economy", "strike_rate", "true_economy", "best"}


@dataclass
class Example:
    intent: str
    question: str
    calls: list[tuple[str, dict]]
    kind: str = "table"                         # how the answer is written (answers.py)
    meta: dict = field(default_factory=dict)    # what the answer writer needs to know (the metric, names...)
    # calls meant to go wrong, by index: "error" (the call must fail, and its error is kept for the model to read)
    # or "lacking" (it succeeds but without what was asked, so the next call fixes it). Recovery examples.
    expect: dict = field(default_factory=dict)


# ---- vocabulary ---------------------------------------------------------------------------------------

BAT_METRICS = {   # id: (phrases for "who has the ...", label in answers)
    "runs": (["most runs", "the most runs", "top run-scorer"], "runs"),
    "average": (["the best batting average", "the highest average"], "average"),
    "strike_rate": (["the highest strike rate", "the fastest scoring rate"], "strike rate"),
    "sixes": (["the most sixes", "hit the most sixes"], "sixes"),
    "hundreds": (["the most hundreds", "the most centuries"], "hundreds"),
    "fifties": (["the most fifties", "the most half-centuries"], "fifties"),
    "true_sr": (["the best true strike rate", "the highest true strike rate"], "true strike rate"),
    "true_average": (["the best true average"], "true average"),
    "match_factor": (["the best match factor", "the most dominant batting relative to their teammates"], "match factor"),
    "boundary_pct": (["the highest boundary percentage", "the most boundaries per ball"], "boundary %"),
    "dot_pct": (["the lowest dot-ball percentage"], "dot %"),
    "runs_above_expected": (["the most runs above expected"], "runs above expected"),
    "ducks": (["the most ducks"], "ducks"),
}
BOWL_METRICS = {
    "wickets": (["the most wickets", "taken the most wickets", "top wicket-taker"], "wickets"),
    "economy": (["the best economy", "the most economical bowling", "the lowest economy rate"], "economy"),
    "average": (["the best bowling average"], "average"),
    "strike_rate": (["the best bowling strike rate"], "strike rate"),
    "dot_pct": (["the highest dot-ball percentage", "bowled the most dot balls per over"], "dot %"),
    "five_wkt_hauls": (["the most five-wicket hauls", "the most five-fors"], "five-wicket hauls"),
    "true_economy": (["the best true economy"], "true economy"),
    "runs_saved": (["saved the most runs"], "runs saved"),
    "maidens": (["the most maidens"], "maidens"),
}
PHASES = {"powerplay": ["powerplay", "in the powerplay", "in the first six overs"],
          "middle": ["middle-overs", "in the middle overs"], "death": ["death-overs", "at the death", "in the death overs"]}
SPLITS = {"season": "season by season", "opposition": "against each opponent", "format": "by format",
          "phase": "by phase", "position": "by batting position", "venue": "at each ground",
          "dismissal": "by how they got out", "result": "in wins and losses", "innings": "batting first vs chasing"}
COMP_SHORT = {"Indian Premier League": ["IPL", "the IPL", "Indian Premier League"], "Big Bash League": ["BBL", "the Big Bash"],
              "Pakistan Super League": ["PSL", "the PSL"], "Caribbean Premier League": ["CPL", "the CPL"],
              "Women's Premier League": ["WPL", "the WPL"], "ICC Men's T20 World Cup": ["the T20 World Cup"],
              "ICC Cricket World Cup": ["the World Cup", "the ODI World Cup"], "The Hundred Men's Competition": ["The Hundred"],
              "Women's Big Bash League": ["the WBBL", "the Women's Big Bash"], "SA20": ["SA20"],
              "Vitality Blast": ["the T20 Blast"], "Bangladesh Premier League": ["the BPL"],
              "Lanka Premier League": ["the LPL"], "International League T20": ["the ILT20"]}


def pick(rng: random.Random, xs):
    """A random choice; entities with a weight (players, teams, venues, competitions) are picked in proportion
    to it, so well-known names come up more often, as they do in real questions."""
    xs = list(xs)
    ws = [getattr(x, "weight", None) if not isinstance(x, dict) else x.get("weight") for x in xs]
    if xs and all(w is not None for w in ws):
        return rng.choices(xs, weights=ws)[0]
    return rng.choice(xs)


def comp_phrase(rng, name: str) -> str:
    return pick(rng, COMP_SHORT.get(name, [name]))


def fmt_phrase(rng, fmt: str) -> str:
    return pick(rng, {"T20I": ["T20Is", "T20 internationals"], "ODI": ["ODIs", "one-dayers", "ODI cricket"],
                      "Test": ["Tests", "Test cricket"], "T20": ["T20s", "all T20 cricket"]}[fmt])


def player_scope(rng, p: Player) -> tuple[dict, str]:
    """A scope the player actually played in, and how a person would say it."""
    if p.competitions and rng.random() < 0.35:
        c = pick(rng, p.competitions)
        return {"competition": c}, "in " + comp_phrase(rng, c)
    f = pick(rng, p.formats)
    return {"format": f}, "in " + fmt_phrase(rng, f)


def league(rng, pools: Pools, fmt_prefix: str = "T20") -> dict:
    cs = [c for c in pools.competitions if c["format"].startswith(fmt_prefix) and c["seasons"]]
    return pick(rng, cs)


def gender_filter(p_or_g) -> dict:
    g = p_or_g if isinstance(p_or_g, str) else p_or_g.gender
    return {"gender": "female"} if g == "female" else {}


# ---- intents -----------------------------------------------------------------------------------------

def leaderboard_bat(rng, pools):
    mid, (phrases, _) = pick(rng, BAT_METRICS.items())
    c = league(rng, pools, pick(rng, ["T20", "T20", "ODI"]))
    f = {"competition": c["name"]}
    scope = "in " + comp_phrase(rng, c["name"])
    if rng.random() < 0.3 and len(c["seasons"]) > 2:
        s = pick(rng, c["seasons"][-4:])
        f["season"] = s
        scope += f" {s}"
    elif rng.random() < 0.25:
        y = rng.choice([2018, 2020, 2022, 2023])
        f["from_year"] = y
        scope += f" since {y}"
    q = pick(rng, ["Who has {m} {s}?", "Which batter has {m} {s}?", "{s_cap}, who has {m}?", "top batters by {l} {s}",
                   "Leaderboard: {l} {s}"]).format(m=pick(rng, phrases), s=scope, s_cap=scope[0].upper() + scope[1:],
                                                   l=BAT_METRICS[mid][1])
    return Example("leaderboard", q, [("leaderboard", {"metric": mid, "filters": f})], "leaderboard",
                   {"metric": mid, "label": BAT_METRICS[mid][1]})


def leaderboard_bowl(rng, pools):
    mid, (phrases, label) = pick(rng, BOWL_METRICS.items())
    c = league(rng, pools, pick(rng, ["T20", "T20", "ODI"]))
    f = {"competition": c["name"]}
    scope = "in " + comp_phrase(rng, c["name"])
    phase_words = ""
    if rng.random() < 0.4 and c["format"].startswith(("T20", "ODI")):
        ph = pick(rng, PHASES)
        f["phase"] = ph
        phase_words = pick(rng, PHASES[ph])
    q = pick(rng, ["Who has {m} {p} {s}?", "Which bowler has {m} {s} {p}?", "Best bowlers by {l} {s} {p}"]).format(
        m=pick(rng, phrases), s=scope, p=phase_words, l=label)
    return Example("leaderboard", " ".join(q.split()), [("leaderboard", {"metric": mid, "role": "bowling", "filters": f})],
                   "leaderboard", {"metric": mid, "label": label})


def leaderboard_format(rng, pools):
    role = pick(rng, ["batting", "bowling"])
    metrics = BAT_METRICS if role == "batting" else BOWL_METRICS
    mid, (phrases, label) = pick(rng, metrics.items())
    fmt = pick(rng, FORMATS)
    female = rng.random() < 0.25
    mens = not female and rng.random() < 0.3
    f = {"format": fmt, **({"gender": "female"} if female else {"gender": "male"} if mens else {})}
    who = "women's " if female else "men's " if mens else ""
    if rng.random() < 0.3:
        y = rng.choice([2015, 2019, 2020, 2023])
        f["from_year"] = y
        extra = f" since {y}"
    else:
        extra = ""
    q = pick(rng, ["Who has {m} in {w}{fp}{e}?", "{W}{fp}{e}: who has {m}?", "Top {l} in {w}{fp}{e}"]).format(
        m=pick(rng, phrases), w=who, W=who.capitalize(), fp=fmt_phrase(rng, fmt), e=extra, l=label)
    args = {"metric": mid, "filters": f}
    if role == "bowling":
        args["role"] = "bowling"
    return Example("leaderboard", q, [("leaderboard", args)], "leaderboard", {"metric": mid, "label": label})


def profile(rng, pools):
    p = pick(rng, pools.players)
    if rng.random() < 0.5:
        q = pick(rng, ["Tell me about {n}", "Who is {n}?", "{n}'s career numbers", "Give me {n}'s profile",
                       "How good is {n}?"]).format(n=p.name)
        args = {"player": p.name}
    else:
        f, s = player_scope(rng, p)
        q = pick(rng, ["How has {n} done {s}?", "{n} {s}: the numbers", "Tell me about {n} {s}"]).format(n=p.name, s=s)
        args = {"player": p.name, "filters": f}
    return Example("profile", q, [("player_profile", args)], "profile", {"player": p.name})


def stats_split(rng, pools):
    p = pick(rng, [x for x in pools.players if x.batter] if rng.random() < 0.7 else [x for x in pools.players if x.bowler])
    role = "batting" if p.batter and (not p.bowler or rng.random() < 0.7) else "bowling"
    split = pick(rng, ["season", "opposition", "format", "phase", "position", "venue", "dismissal", "result", "innings"]
                 if role == "batting" else ["season", "opposition", "format", "phase", "venue", "result"])
    f, s = player_scope(rng, p)
    if split == "format":
        f, s = {}, ""
    q = pick(rng, ["{n}'s {r} {s} {sp}", "Break down {n}'s {r} {sp} {s}", "How does {n} {v} {sp} {s}?"]).format(
        n=p.name, r=role, s=s, sp=SPLITS[split], v="bat" if role == "batting" else "bowl")
    args = {"player": p.name, "split_by": split, **({"role": "bowling"} if role == "bowling" else {}),
            **({"filters": f} if f else {})}
    return Example("stats_split", " ".join(q.split()), [("player_stats", args)], "split",
                   {"player": p.name, "split": split, "role": role})


def stats_scope(rng, pools):
    p = pick(rng, [x for x in pools.players if x.batter])
    f, s = player_scope(rng, p)
    metric, word = pick(rng, [("strike_rate", "strike rate"), ("average", "average"), ("true_sr", "true strike rate"),
                              ("runs", "runs"), ("sixes", "sixes"), ("dot_pct", "dot-ball percentage")])
    if rng.random() < 0.4:
        ph = pick(rng, ["powerplay", "death"])
        f = {**f, "phase": ph}
        s += " " + pick(rng, PHASES[ph])
    q = pick(rng, ["What's {n}'s {w} {s}?", "{n} {w} {s}", "How many {w} does {n} have {s}?" if metric in ("runs", "sixes")
                   else "What is {n}'s {w} {s}?"]).format(n=p.name, w=word, s=s)
    args = {"player": p.name, "filters": f}
    if metric not in DEFAULT_BAT:
        args["metrics"] = [metric]
    return Example("stats_scope", " ".join(q.split()), [("player_stats", args)], "stats",
                   {"player": p.name, "metric": metric, "label": word})


def compare(rng, pools):
    role = pick(rng, ["batting", "bowling"])
    pool = [x for x in pools.players if (x.batter if role == "batting" else x.bowler)]
    a = pick(rng, pool)
    fmt = pick(rng, a.formats)
    others = [x for x in pool if x.gender == a.gender and fmt in x.formats and x.name != a.name]
    if not others:
        return None
    b = pick(rng, others)
    names = [a.name, b.name]
    if rng.random() < 0.25:
        more = [x for x in others if x.name != b.name]
        if more:
            names.append(pick(rng, more).name)
    listed = ", ".join(names[:-1]) + " and " + names[-1]
    r = "batting" if role == "batting" else "bowling"
    q = pick(rng, ["Compare the {r} of {l} in {f}", "{l}: who's the better {rr} in {f}?", "{a} vs {b} with the {bb} in {f}",
                   "How do {l} compare as {rs} in {f}?"]).format(
        l=listed, f=fmt_phrase(rng, fmt), a=names[0], b=names[1], r=r, rr="batter" if r == "batting" else "bowler",
        bb="bat" if role == "batting" else "ball", rs="batters" if role == "batting" else "bowlers")
    args = {"players": names, "filters": {"format": fmt}, **({"role": "bowling"} if role == "bowling" else {})}
    return Example("compare", q, [("compare_players", args)], "compare", {"players": names, "role": role})


def form(rng, pools):
    p = pick(rng, pools.players)
    role = "batting" if p.batter else "bowling"
    f, s = player_scope(rng, p)
    q = pick(rng, ["Is {n} in form {s}?", "How is {n}'s recent form {s}?", "Is {n} out of form {s}?",
                   "{n}'s form {s} lately"]).format(n=p.name, s=s)
    args = {"player": p.name, "filters": f, **({"role": "bowling"} if role == "bowling" else {})}
    return Example("form", q, [("player_form", args)], "form", {"player": p.name, "role": role})


def arc(rng, pools):
    a, b = rng.sample([x for x in pools.players if x.batter and ("Test" in x.formats or "ODI" in x.formats)], 2)
    fmt = pick(rng, [f for f in a.formats if f in b.formats] or a.formats)
    q = pick(rng, ["Compare the careers of {a} and {b} in {f}, innings by innings",
                   "How did {a}'s and {b}'s {f} careers develop?"]).format(a=a.name, b=b.name, f=fmt_phrase(rng, fmt))
    return Example("arc", q, [("career_arc", {"players": [a.name, b.name], "filters": {"format": fmt}})], "arc",
                   {"players": [a.name, b.name]})


def percentiles(rng, pools):
    p = pick(rng, [x for x in pools.players if x.batter])
    fmt = pick(rng, p.formats)
    q = pick(rng, ["Where does {n} rank among {f} batters?", "How does {n} compare with other {f} batters?",
                   "{n}'s percentiles in {f}"]).format(n=p.name, f=fmt_phrase(rng, fmt))
    return Example("percentiles", q, [("percentiles", {"players": [p.name], "filters": {"format": fmt}})], "percentiles",
                   {"player": p.name})


def matrix(rng, pools):
    role = pick(rng, ["batting", "bowling"])
    x, y, words = pick(rng, [("true_average", "true_sr", "both consistent and fast"), ("average", "strike_rate", "score big and quickly")]
                       if role == "batting" else [("true_economy", "true_wickets", "both economical and wicket-taking"),
                                                  ("economy", "strike_rate", "economical and strike often")])
    fmt = pick(rng, FORMATS)
    q = pick(rng, ["Which {f} {r} are {w}?", "Who stands out in {f} as {r} who {w}?"]).format(
        f=fmt_phrase(rng, fmt), r="batters" if role == "batting" else "bowlers", w=words)
    args = {"x": x, "y": y, "filters": {"format": fmt}, **({"role": "bowling"} if role == "bowling" else {})}
    return Example("matrix", q, [("player_matrix", args)], "matrix", {"role": role})


def entry(rng, pools):
    p = pick(rng, [x for x in pools.players if x.batter])
    f, s = player_scope(rng, p)
    q = pick(rng, ["When does {n} usually come in to bat {s}?", "Where does {n} bat {s}, and how does he do from there?",
                   "{n}'s entry points {s}"]).format(n=p.name, s=s)
    if p.gender == "female":
        q = q.replace(" he ", " she ")
    return Example("entry", q, [("entry_points", {"player": p.name, "filters": f})], "entry", {"player": p.name})


def similar(rng, pools):
    p = pick(rng, pools.players)
    role = "batting" if p.batter else "bowling"
    q = pick(rng, ["Who are the players most like {n}?", "Find me players similar to {n}", "Which {r}s resemble {n}?"]).format(
        n=p.name, r="batter" if role == "batting" else "bowler")
    return Example("similar", q, [("similar_players", {"player": p.name, **({"role": "bowling"} if role == "bowling" else {})})],
                   "similar", {"player": p.name})


def matchup(rng, pools):
    bat = pick(rng, [x for x in pools.players if x.batter])
    bowlers = [x for x in pools.players if x.bowler and x.gender == bat.gender and x.name != bat.name
               and set(x.formats) & set(bat.formats)]
    if not bowlers:
        return None
    bowl = pick(rng, bowlers)
    q = pick(rng, ["How has {a} done against {b}?", "{a} vs {b}: head to head", "Has {b} got the better of {a}?",
                   "What's {a}'s record against {b}'s bowling?"]).format(a=bat.name, b=bowl.name)
    return Example("matchup", q, [("matchup", {"batter": bat.name, "bowler": bowl.name})], "matchup",
                   {"batter": bat.name, "bowler": bowl.name})


def team_record(rng, pools):
    t = pick(rng, [x for x in pools.teams if x["international"]] or pools.teams)
    opp = [x for x in pools.teams if x["international"] == t["international"] and x["name"] != t["name"]
           and x["gender"] == t["gender"]]
    fmt = pick(rng, FORMATS if t["international"] else ["T20"])
    g = "women's " if t["gender"] == "female" else ("men's " if rng.random() < 0.4 else "")
    gf = {"gender": "female"} if t["gender"] == "female" else ({"gender": "male"} if g else {})
    if opp and rng.random() < 0.6:
        o = pick(rng, opp)["name"]
        q = pick(rng, ["{t} vs {o} head to head in {g}{f}", "What's {t}'s record against {o} in {g}{f}?",
                       "What is the head-to-head record between {t} and {o} in {g}{f}?"]).format(
            t=t["name"], o=o, g=g, f=fmt_phrase(rng, fmt))
        args = {"team": t["name"], "opposition": o, "filters": {"format": fmt, **gf}}
    else:
        split = pick(rng, [None, "season", "opposition"])
        q = pick(rng, ["What's {t}'s record in {g}{f}?", "How have {t} done in {g}{f}?"]).format(
            t=t["name"], g=g, f=fmt_phrase(rng, fmt))
        args = {"team": t["name"], "filters": {"format": fmt, **gf}}
        if split:
            args["split_by"] = split
            q += " " + {"season": "Season by season.", "opposition": "Split by opponent."}[split]
    return Example("team_record", q, [("team_record", args)], "team", {"team": t["name"], "opposition": args.get("opposition")})


def team_board(rng, pools):
    c = league(rng, pools)
    metric, words = pick(rng, [("wins", "won the most matches"), ("win_pct", "the best win percentage")])
    q = pick(rng, ["Which team has {w} in {c}?", "Most successful sides in {c}"]).format(w=words, c=comp_phrase(rng, c["name"]))
    return Example("team_board", q, [("team_leaderboard", {"metric": metric, "filters": {"competition": c["name"]}})],
                   "leaderboard", {"metric": metric, "label": "wins" if metric == "wins" else "win %"})


def venue(rng, pools):
    v = pick(rng, pools.venues)["name"]
    q = pick(rng, ["How does {v} play?", "Is {v} a batting or bowling ground?", "Should you bat first or chase at {v}?",
                   "What's a good score at {v}?"]).format(v=v)
    return Example("venue", q, [("venue_profile", {"venue": v})], "venue", {"venue": v})


def records(rng, pools):
    kind, words = pick(rng, [("batting_innings", "highest individual scores"), ("bowling_figures", "best bowling figures"),
                             ("team_total", "highest team totals")])
    order = "highest"
    if kind == "team_total" and rng.random() < 0.4:
        order, words = "lowest", "lowest team totals"
    if rng.random() < 0.5:
        c = league(rng, pools)
        f, s = {"competition": c["name"]}, "in " + comp_phrase(rng, c["name"])
    else:
        fmt = pick(rng, FORMATS)
        f, s = {"format": fmt}, "in " + fmt_phrase(rng, fmt)
    q = pick(rng, ["What are the {w} {s}?", "{W} {s}", "Show me the {w} {s}"]).format(w=words, W=words.capitalize(), s=s)
    args = {"kind": kind, "filters": f, **({"order": "lowest"} if order == "lowest" else {})}
    return Example("records", q, [("records", args)], "records", {"kind": kind})


def luck(rng, pools):
    role = pick(rng, ["bowling", "batting"])
    unlucky = rng.random() < 0.5
    c = league(rng, pools)
    season = pick(rng, c["seasons"][-3:])
    q = pick(rng, ["Who's been the {u} {r} in {c} {s}?", "Which {r} got {u2} in {c} {s}?"]).format(
        u="unluckiest" if unlucky else "luckiest", u2="unlucky" if unlucky else "lucky",
        r="bowler" if role == "bowling" else "batter", c=comp_phrase(rng, c["name"]), s=season)
    args = {"role": role, "filters": {"competition": c["name"], "season": season}, **({"unlucky": True} if unlucky else {})}
    return Example("luck", q, [("luck_leaderboard", args)], "luck", {"unlucky": unlucky, "role": role})


def fibs(rng, pools):
    fmt = pick(rng, ["T20", "ODI", "Test"])
    q = pick(rng, ["How much of a bowler's {f} economy is skill and how much is luck?",
                   "Which {f} stats are reliable and which are mostly noise?",
                   "What does FIBS say about {f} bowling stats?"]).format(f=fmt_phrase(rng, fmt))
    return Example("fibs", q, [("fibs_report", {"format": fmt})], "fibs", {"format": fmt})


def replay(rng, pools):
    t = pick(rng, [x for x in pools.teams if x["international"]])
    opp = pick(rng, [x for x in pools.teams if x["international"] and x["name"] != t["name"] and x["gender"] == t["gender"]])
    q = pick(rng, ["What was the turning point in the last {t} vs {o} match?", "How did the latest {t}-{o} game swing?",
                   "Walk me through the most recent {t} v {o} match"]).format(t=t["name"], o=opp["name"])
    return Example("replay", q, [("match_replay", {"team": t["name"], "opposition": opp["name"],
                                                   **({"filters": {"gender": "female"}} if t["gender"] == "female" else {})})],
                   "replay", {})


def coverage(rng, pools):
    fmt = pick(rng, ["Test", "ODI"])
    t = pick(rng, [x for x in pools.teams if x["international"]])
    if rng.random() < 0.5:
        q = pick(rng, ["Which {t} {f} matches are missing from the data?", "Is every {t} {f} in the database?"]).format(
            t=t["name"], f=pick(rng, ["ODI", "one-day international"]) if fmt == "ODI" else "Test")
        args = {"view": "missing", "format": fmt, "team": t["name"]}
    else:
        q = pick(rng, ["How complete is the {f} data?", "What does the data cover for {f}?"]).format(f=fmt_phrase(rng, fmt))
        args = {"format": fmt}
    return Example("coverage", q, [("data_coverage", args)], "coverage", {"format": fmt})


def metric_search(rng, pools):
    word, q = pick(rng, [("boundary", "Which metrics measure boundary hitting?"), ("true", "What are the 'true' metrics?"),
                         ("consistency", "Is there a stat for batting consistency?"),
                         ("dot", "What dot-ball stats can I look at?"), ("luck", "Which stats measure luck?")])
    return Example("search_metrics", q, [("search_metrics", {"query": word})], "metrics", {})


def lookup(rng, pools):
    p = pick(rng, pools.players)
    last = p.name.split()[-1]
    q = pick(rng, ["Which {l} do you have in the data?", "Who is '{l}' in your database?", "How is {n} stored in the data?"]).format(
        l=last, n=p.name)
    return Example("lookup", q, [("lookup_entity", {"kind": "player", "name": last if "{n}" not in q else p.name})],
                   "lookup", {})


def split_then_chart(rng, pools):
    p = pick(rng, [x for x in pools.players if x.batter and x.competitions])
    c = pick(rng, p.competitions)
    metric, word = pick(rng, [("runs", "runs"), ("strike_rate", "strike rate"), ("average", "average")])
    q = pick(rng, ["Plot {n}'s {c} {w} season by season", "Chart how {n}'s {w} in {c} has changed over the seasons",
                   "Show {n}'s {c} {w} by season as a chart"]).format(n=p.name, c=comp_phrase(rng, c).replace("the ", ""), w=word)
    return Example("chart", q, [("player_stats", {"player": p.name, "split_by": "season", "filters": {"competition": c}}),
                                ("plot_chart", {"table_id": "T1", "x": "season", "y": [metric], "type": "line"})],
                   "chart", {"player": p.name, "metric": metric, "label": word})


def compare_then_chart(rng, pools):
    ex = compare(rng, pools)
    if not ex or ex.meta["role"] != "batting":
        return None
    metric = pick(rng, ["average", "strike_rate", "true_sr"])
    ex.question = pick(rng, ["{q}, with a chart", "{q} and plot the {m}"]).format(q=ex.question.rstrip("?"), m=metric.replace("_", " "))
    ex.calls.append(("plot_chart", {"table_id": "T1", "x": "player", "y": [metric], "type": "bar"}))
    ex.intent, ex.kind, ex.meta = "compare_chart", "compare", {**ex.meta, "chart": metric}
    return ex


def open_app(rng, pools):
    p = pick(rng, pools.players)
    if rng.random() < 0.5:
        q = pick(rng, ["Open {n}'s page", "Show me {n} in the Player Hub", "Take me to {n}"]).format(n=p.name)
        return Example("open", q, [("open_in_app", {"view": "player", "state": {"player": p.name}})], "open", {"what": p.name})
    mid, (_, label) = pick(rng, BAT_METRICS.items())
    fmt = pick(rng, FORMATS)
    q = pick(rng, ["Set up a query for the top {l} in {f}", "Open the Query Builder with {f} {l}"]).format(l=label, f=fmt_phrase(rng, fmt))
    return Example("open", q, [("open_in_app", {"view": "query", "state": {"role": "batting", "metrics": [mid],
                                                                           "sort_by": mid, "filters": {"format": fmt}}})],
                   "open", {"what": "the Query Builder"})


def two_scopes(rng, pools):
    p = pick(rng, [x for x in pools.players if x.batter and x.competitions and "T20I" in x.formats])
    c = pick(rng, p.competitions)
    q = pick(rng, ["Is {n} better in {c} or in T20Is?", "Compare {n}'s {c} and T20I numbers"]).format(n=p.name, c=comp_phrase(rng, c))
    return Example("two_scopes", q, [("player_stats", {"player": p.name, "filters": {"competition": c}}),
                                     ("player_stats", {"player": p.name, "filters": {"format": "T20I"}})],
                   "two_scopes", {"player": p.name, "scopes": [comp_phrase(rng, c), "T20Is"]})


# ---- v2: questions the first models couldn't answer -------------------------------------------------------

STATS = {   # one figure for one player: id -> (role, ways to ask with {n} and {s}, word in the answer)
    "sixes": ("batting", ["How many sixes has {n} hit {s}?", "{n}'s sixes {s}", "Number of sixes by {n} {s}"], "sixes"),
    "fours": ("batting", ["How many fours has {n} hit {s}?", "{n}'s boundary count (fours) {s}"], "fours"),
    "balls": ("batting", ["How many balls has {n} faced {s}?", "Balls faced by {n} {s}"], "balls faced"),
    "dismissals": ("batting", ["How many times has {n} been dismissed {s}?", "How often has {n} got out {s}?"], "dismissals"),
    "ducks": ("batting", ["How many ducks has {n} made {s}?", "{n}'s ducks {s}"], "ducks"),
    "not_outs": ("batting", ["How many times has {n} finished not out {s}?"], "not outs"),
    "highest": ("batting", ["What is {n}'s highest score {s}?", "{n}'s top score {s}"], "highest score"),
    "hundreds": ("batting", ["How many hundreds has {n} scored {s}?"], "hundreds"),
    "innings": ("batting", ["How many innings has {n} batted {s}?"], "innings"),
    "wickets": ("bowling", ["How many wickets has {n} taken {s}?", "{n}'s wicket tally {s}"], "wickets"),
    "maidens": ("bowling", ["How many maidens has {n} bowled {s}?"], "maidens"),
    "five_wkt_hauls": ("bowling", ["How many five-wicket hauls does {n} have {s}?"], "five-wicket hauls"),
    "bowl_balls": ("bowling", ["How many legal deliveries has {n} bowled {s}?", "How many balls has {n} bowled {s}?"],
                   "legal deliveries bowled"),
    "economy": ("bowling", ["What is {n}'s economy rate {s}?"], "economy"),
}


def _stat_scope(rng, p: Player) -> tuple[dict, str]:
    """A scope for a single figure, sometimes the whole career by gender ('in men's cricket')."""
    if rng.random() < 0.3:
        return ({"gender": "female"}, "in women's cricket") if p.gender == "female" else ({"gender": "male"}, "in men's cricket")
    f, s = player_scope(rng, p)
    if p.gender == "female" and "format" in f:
        f = {**f, "gender": "female"}
        s = s.replace("in ", "in women's ", 1)
    return f, s


def stat_one(rng, pools, recover: bool = False):
    key, (role, asks, word) = pick(rng, STATS.items())
    pool = [x for x in pools.players if (x.batter if role == "batting" else x.bowler)]
    p = pick(rng, pool)
    f, s = _stat_scope(rng, p)
    metric = "balls" if key == "bowl_balls" else key
    q = pick(rng, asks).format(n=p.name, s=s)
    base = {"player": p.name, **({"role": "bowling"} if role == "bowling" else {}), "filters": f}
    defaults = DEFAULT_BAT if role == "batting" else DEFAULT_BOWL
    meta = {"player": p.name, "metric": metric, "label": word}
    if recover:      # first without the metric, see it's missing, then ask for it
        if metric in defaults:
            return None
        return Example("stat_recover", q, [("player_stats", base), ("player_stats", {**base, "metrics": [metric]})],
                       "stats", {**meta, "use_last": True}, expect={0: "lacking"})
    return Example("stat_one", q, [("player_stats", {**base, "metrics": [metric]})], "stats", meta)


def stat_recover(rng, pools):
    return stat_one(rng, pools, recover=True)


def _q(text: str) -> str:
    return text.replace("'", "''")


def _sql_cases(rng, pools) -> list[tuple[str, str, str, str | None]]:
    """(question, query, what the number is, a plausible wrong query or None). Queries follow run_sql's schema
    and rules: gender filtered, internationals by team_type, super overs excluded."""
    g = pick(rng, ["male", "female"])
    gw = "men's" if g == "male" else "women's"
    comp = pick(rng, [c for c in pools.competitions if (c.get("gender") or "male") == g] or pools.competitions)
    team = pick(rng, [t for t in pools.teams if t["international"] and t["gender"] == g] or pools.teams)
    venue = pick(rng, pools.venues)["name"]
    fmt, fq = pick(rng, [("T20I", "match_type = 'T20' AND team_type = 'international'"), ("ODI", "match_type = 'ODI'"),
                         ("Test", "match_type = 'Test'")])
    cn, tn, vn = _q(comp["name"]), _q(team["name"]), _q(venue)
    return [
        ("How many matches are in the dataset?", "SELECT COUNT(*) AS matches FROM matches", "matches in the data", None),
        (f"How many {gw} matches are in the data?", f"SELECT COUNT(*) AS matches FROM matches WHERE gender = '{g}'",
         f"{gw} matches in the data", None),
        ("How many deliveries are in the dataset?", "SELECT COUNT(*) AS deliveries FROM deliveries", "deliveries in the data",
         "SELECT COUNT(*) AS deliveries FROM balls"),
        (f"How many {gw} {fmt}s are in the data?", f"SELECT COUNT(*) AS matches FROM matches WHERE gender = '{g}' AND {fq}",
         f"{gw} {fmt}s in the data", None),
        ("How many run-outs are there in the dataset?",
         "SELECT COUNT(*) AS run_outs FROM deliveries WHERE wicket_kind = 'run out' AND innings_num <= 2",
         "run-outs in the data", "SELECT COUNT(*) AS run_outs FROM matches WHERE dismissal = 'run out'"),
        ("How many stumpings are in the data?",
         "SELECT COUNT(*) AS stumpings FROM deliveries WHERE wicket_kind = 'stumped' AND innings_num <= 2",
         "stumpings in the data", "SELECT COUNT(*) AS stumpings FROM deliveries WHERE dismissal_kind = 'stumped'"),
        ("How many distinct competitions does the data cover?", "SELECT COUNT(DISTINCT event_name) AS competitions FROM matches",
         "competitions (distinct event names) in the data", "SELECT COUNT(DISTINCT competition) AS competitions FROM matches"),
        (f"How many {comp['name']} matches are in the data?",
         f"SELECT COUNT(*) AS matches FROM matches WHERE event_name = '{cn}' AND gender = '{g}'",
         f"{comp['name']} matches in the data", f"SELECT COUNT(*) AS matches FROM matches WHERE competition = '{cn}'"),
        (f"How many {gw} matches have {team['name']} played in the data?",
         f"SELECT COUNT(*) AS matches FROM matches WHERE gender = '{g}' AND (team1 = '{tn}' OR team2 = '{tn}')",
         f"{gw} matches for {team['name']}", f"SELECT COUNT(*) AS matches FROM matches WHERE team = '{tn}'"),
        (f"How many {gw} {fmt}s have {team['name']} won?",
         f"SELECT COUNT(*) AS wins FROM matches WHERE gender = '{g}' AND {fq} AND winner = '{tn}'",
         f"{gw} {fmt} wins for {team['name']}", None),
        (f"How many matches have been played at {venue}?", f"SELECT COUNT(*) AS matches FROM matches WHERE venue LIKE '{vn}%'",
         f"matches at {venue}", f"SELECT COUNT(*) AS matches FROM venues WHERE name = '{vn}'"),
        ("What is the most common way to get out?",
         "SELECT wicket_kind, COUNT(*) AS dismissals FROM deliveries WHERE is_wicket AND innings_num <= 2 "
         "GROUP BY 1 ORDER BY 2 DESC LIMIT 5", "", "SELECT dismissal_type, COUNT(*) FROM deliveries GROUP BY 1"),
        (f"Who has won the most player-of-the-match awards in {gw} cricket?",
         f"SELECT player_of_match AS player, COUNT(*) AS awards FROM matches WHERE gender = '{g}' AND player_of_match "
         "IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 10", "", None),
        (f"How often do captains choose to bat first after winning the toss in {gw} {fmt}s?",
         f"SELECT toss_decision, COUNT(*) AS matches FROM matches WHERE gender = '{g}' AND {fq} GROUP BY 1 ORDER BY 2 DESC",
         "", None),
        (f"Which grounds have hosted the most {gw} {fmt}s?",
         f"SELECT venue, COUNT(*) AS matches FROM matches WHERE gender = '{g}' AND {fq} GROUP BY 1 ORDER BY 2 DESC LIMIT 10",
         "", None),
        (f"How many {comp['name']} seasons are in the data?",
         f"SELECT COUNT(DISTINCT season) AS seasons FROM matches WHERE event_name = '{cn}' AND gender = '{g}'",
         f"{comp['name']} seasons in the data", None),
        ("How many super overs are in the data?",
         "SELECT COUNT(DISTINCT match_id) AS matches FROM deliveries WHERE innings_num > 2 AND match_id IN "
         "(SELECT match_id FROM matches WHERE match_type IN ('T20', 'ODI', 'IT20'))",
         "matches with a super over in the data", None),
    ]


def sql(rng, pools, recover: bool = False):
    cases = _sql_cases(rng, pools)
    if recover:
        cases = [c for c in cases if c[3]]
    q, query, what, wrong = pick(rng, cases)
    if recover:
        return Example("sql_recover", q, [("run_sql", {"query": wrong}), ("run_sql", {"query": query})], "sql",
                       {"what": what, "use_last": True}, expect={0: "error"})
    return Example("sql", q, [("run_sql", {"query": query})], "sql", {"what": what})


def sql_recover(rng, pools):
    return sql(rng, pools, recover=True)


def venue_record(rng, pools):
    v = pick(rng, pools.venues)["name"]
    kind, ask = pick(rng, [("team_total", ["What is the highest team total at {v}?", "Highest innings total at {v}"]),
                           ("batting_innings", ["What is the highest individual score at {v}?", "Best innings ever played at {v}"]),
                           ("bowling_figures", ["What are the best bowling figures at {v}?"])])
    return Example("venue_record", pick(rng, ask).format(v=v), [("records", {"kind": kind, "filters": {"venue": v}})],
                   "records", {"kind": kind})


INTENTS = {   # intent: (function, weight)
    "stat_one": (stat_one, 7), "stat_recover": (stat_recover, 2), "sql_recover": (sql_recover, 2),
    "venue_record": (venue_record, 2),
    "leaderboard_bat": (leaderboard_bat, 8), "leaderboard_bowl": (leaderboard_bowl, 7), "leaderboard_format": (leaderboard_format, 7),
    "profile": (profile, 6), "stats_split": (stats_split, 7), "stats_scope": (stats_scope, 6), "compare": (compare, 6),
    "form": (form, 4), "arc": (arc, 2), "percentiles": (percentiles, 3), "matrix": (matrix, 3), "entry": (entry, 2),
    "similar": (similar, 3), "matchup": (matchup, 5), "team_record": (team_record, 4), "team_board": (team_board, 3),
    "venue": (venue, 4), "records": (records, 4), "luck": (luck, 3), "fibs": (fibs, 1), "replay": (replay, 3),
    "coverage": (coverage, 2), "search_metrics": (metric_search, 1), "lookup": (lookup, 2), "sql": (sql, 5),
    "chart": (split_then_chart, 4), "compare_chart": (compare_then_chart, 2), "open": (open_app, 2),
    "two_scopes": (two_scopes, 3),
}


def sample(rng: random.Random, pools: Pools) -> Example | None:
    names = list(INTENTS)
    fn = INTENTS[rng.choices(names, weights=[INTENTS[n][1] for n in names])[0]][0]
    try:
        ex = fn(rng, pools)
    except (IndexError, ValueError):
        return None
    if ex:
        ex.question = " ".join(ex.question.split()).replace(" ?", "?")
    return ex
