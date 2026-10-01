"""
Write the final answer for a training example from its tool results, in Doosra's answer style: the direct
answer and key numbers first, then a few short points; player and team names and key numbers in bold;
the scope named; caveats from the results' notes. Every number comes from a result. Returns None when a
result doesn't support a sensible answer (the example is then dropped).
"""

from __future__ import annotations

import random
import re

STORED = re.compile(r"'([^']+)' is stored as '([^']+)'")


def num(v) -> str:
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".") if abs(v) < 1000 else f"{v:,.0f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def names_note(views: list) -> dict[str, str]:
    """{stored name: what the user wrote} from the results' notes."""
    out = {}
    for v in views:
        for n in (v.get("notes") or []) if isinstance(v, dict) else []:
            m = STORED.search(n)
            if m:
                out[m.group(2)] = m.group(1)
    return out


class Writer:
    def __init__(self, rng: random.Random, views: list):
        self.rng, self.views = rng, views
        self.aliases = names_note(views)
        self.shown: set[str] = set()

    def name(self, n) -> str:
        """Bold; the first time a renamed player appears, 'RG Sharma (Rohit Sharma)'."""
        n = str(n)
        if n in self.aliases and n not in self.shown:
            self.shown.add(n)
            return f"**{n}** ({self.aliases[n]})"
        return f"**{n}**"

    def scope(self, view: dict, also: tuple = ()) -> str:
        """The scope from a result's title ('Batting — V Kohli by season — T20I' -> 'T20I'), without the
        parts that just repeat who it's about."""
        title = view.get("title") or ""
        who = {str(view.get("player") or "")} | set(self.aliases) | set(self.aliases.values()) | {str(a) for a in also if a}
        parts = [p.strip() for p in title.split("—")[1:]]
        return ", ".join(p for p in parts if not any(w and w in p for w in who))

    def filter_scope(self, view: dict, skip=("team", "opposition", "player")) -> str:
        """The scope from a result's filters ('male, ODI'), for results whose title only names who it's about."""
        f = view.get("filters") or {}
        return ", ".join(str(v) for k, v in f.items() if k not in skip and v not in (None, ""))

    def caveats(self, view: dict, limit: int = 1) -> list[str]:
        keep = []
        for n in view.get("notes") or []:
            low = n.lower()
            if "is stored as" in low or "resolved to" in low or "no gender specified" in low:
                continue
            if any(k in low for k in ("defaulted to", "qualification", "missing", "withhold", "associate", "at least")):
                keep.append(n.split(" -- ")[0].rstrip("."))
        return keep[:limit]

    def pick(self, *options: str) -> str:
        return self.rng.choice(options)


def _label_col(row: dict) -> str | None:
    for k in ("player", "team", "venue", "season", "format", "opposition", "competition", "match_type", "metric"):
        if k in row:
            return k
    return None


def _metric_col(row: dict, metric: str | None) -> str | None:
    if metric and metric in row:
        return metric
    for k in row:
        if k.endswith("_luck"):
            return k
    for k, v in row.items():
        if k not in ("rank", "team", "player", "matches", "innings") and isinstance(v, (int, float)):
            return k
    return None


def leaderboard(w: Writer, v: dict, meta: dict) -> str | None:
    rows = v.get("rows") or []
    if not rows:
        return None
    label, metric = _label_col(rows[0]), _metric_col(rows[0], meta.get("metric"))
    if not label or not metric:
        return None
    word = meta.get("label", metric.replace("_", " "))
    top = rows[0]
    lead = w.pick("{n} leads with {x} {m}", "{n} is top with {x} {m}", "{n} comes out on top: {x} {m}").format(
        n=w.name(top[label]), x=f"**{num(top[metric])}**", m=word)
    lines = [f"{lead} ({w.scope(v)})." if w.scope(v) else f"{lead}."]
    for r in rows[1:4]:
        extra = f" ({r['team']})" if "team" in r and label == "player" else ""
        lines.append(f"- {w.name(r[label])}{extra}: {num(r[metric])}")
    for c in w.caveats(v):
        lines.append(f"\n_{c}._")
    return "\n".join(lines)


SPLIT_LABEL = {   # how a split's bare value reads in an answer: '(7)' -> '(No. 7)'
    "position": lambda x: f"No. {x}",
    "innings": lambda x: {"1": "1st innings", "2": "2nd innings", "3": "3rd innings", "4": "4th innings"}.get(x, f"innings {x}"),
}


def table_summary(w: Writer, v: dict, meta: dict, intro: str) -> str | None:
    rows = v.get("rows") or []
    hl = v.get("highlights") or {}
    if not rows and not hl:
        return None
    lines = [intro]
    first = meta.get("metric") or ""
    counts = ("best_matches", "best_innings", "best_balls", "most_matches", "most_innings")    # not worth leading with
    items = sorted(((k, x) for k, x in hl.items() if k not in counts), key=lambda kv: (first not in kv[0], kv[0] == "totals"))
    for k, val in items[:4]:
        if k == "totals" and isinstance(val, dict):
            val = ", ".join(f"{num(x)} {y.replace('_', ' ')}" for y, x in val.items())
        elif isinstance(val, str) and meta.get("split") in SPLIT_LABEL:
            val = re.sub(r"\((\w+)\)$", lambda m: f"({SPLIT_LABEL[meta['split']](m.group(1))})", val)
        name = k.replace("true_sr", "true_strike_rate").replace("_pct", " %").replace("_", " ")
        lines.append(f"- {name.capitalize()}: **{val}**")
    if not hl and not _label_col(rows[0]):
        r = rows[0]
        figs = [f"{k.replace('_', ' ')} **{num(x)}**" for k, x in r.items() if isinstance(x, (int, float))][:5]
        if not figs:
            return None
        lines.append("- " + ", ".join(figs))
    elif not hl:
        label = _label_col(rows[0])
        metric = _metric_col(rows[0], meta.get("metric"))
        for r in rows[:4]:
            if label and metric:
                lines.append(f"- {w.name(r[label]) if label in ('player', 'team') else r[label]}: {num(r[metric])} "
                             f"{metric.replace('_', ' ')}")
    for c in w.caveats(v):
        lines.append(f"\n_{c}._")
    return "\n".join(lines)


def profile(w, v, meta):
    parts = []
    for role in ("batting", "bowling"):
        s = ((v.get(role) or {}).get("summary") or {}).get("rows") or []
        if not s:
            continue
        r = s[0]
        if role == "batting" and r.get("runs") is not None:
            parts.append(f"**{num(r['runs'])}** runs at an average of **{num(r.get('average', '-'))}** and a strike rate of "
                         f"**{num(r.get('strike_rate', '-'))}** in {num(r.get('matches', '?'))} matches")
        if role == "bowling" and r.get("wickets") is not None:
            parts.append(f"**{num(r['wickets'])}** wickets at **{num(r.get('average', '-'))}**, economy "
                         f"**{num(r.get('economy', '-'))}**")
    if not parts:
        return None
    who = w.name(v.get("player", meta.get("player")))
    role = v.get("primary_role")
    head = f"{who}" + (f", a {role}" if role else "") + f" ({v.get('span', '')}): " + "; ".join(parts) + "."
    teams = v.get("teams") or []
    lines = [head]
    if teams:
        lines.append(f"- Teams: {', '.join(teams[:3])}")
    bat = ((v.get("batting") or {}).get("summary") or {}).get("rows") or []
    if bat and bat[0].get("true_sr") is not None:
        t = bat[0]["true_sr"]
        lines.append(f"- True strike rate **{num(t)}**: {'above' if t > 0 else 'below'} an average batter in the same "
                     "situations")
    return "\n".join(lines)


def stats(w, v, meta):
    rows = v.get("rows") or []
    if not rows:
        return None
    r = rows[0]
    metric = meta.get("metric")
    if metric not in r or r[metric] is None:
        return None
    who = w.name(r.get("player", meta.get("player")))
    scope = w.scope(v, (meta.get("player"), r.get("player"))) or w.filter_scope(v) or "all cricket"
    first = f"{who}'s {meta['label']}: **{num(r[metric])}** ({scope})."
    lines = [first if metric == "highest" else w.pick(first, f"{who}: **{num(r[metric])}** {meta['label']} ({scope}).")]
    ctx = [f"{num(r[k])} {k.replace('_', ' ')}" for k in ("runs", "balls", "average", "strike_rate", "matches")
           if k in r and k != metric and r[k] is not None][:3]
    if ctx:
        lines.append("- Alongside: " + ", ".join(ctx))
    for c in w.caveats(v):
        lines.append(f"\n_{c}._")
    return "\n".join(lines)


def matchup(w, v, meta):
    rows = v.get("rows") or []
    if not rows:
        return None
    r = rows[0]
    a, b = w.name(meta["batter"]), w.name(meta["bowler"])
    outs = r.get("dismissals", 0)
    lines = [f"{a} has scored **{num(r.get('runs'))}** off **{num(r.get('balls'))}** balls against {b} "
             f"(strike rate **{num(r.get('strike_rate'))}**), out **{num(outs)}** time{'s' if outs != 1 else ''}."]
    if r.get("average") is not None and outs:
        lines.append(f"- Average against him: {num(r['average'])}")
    if r.get("dot_pct") is not None:
        lines.append(f"- Dot balls: {num(r['dot_pct'])}%")
    if r.get("balls", 0) < 30:
        lines.append("\n_A small sample: read it with care._")
    return "\n".join(lines)


def team(w, v, meta):
    """A team's record: one row of totals (overall or head to head), or a split (season, opponent...)."""
    rows = v.get("rows") or []
    if not rows:
        return None
    if v.get("highlights") or len(rows) > 1:
        who = w.name(meta["team"])
        return table_summary(w, v, meta, f"{who}'s results ({w.filter_scope(v) or 'all cricket'}):")
    r = rows[0]
    if r.get("matches") is None:
        return None
    vs = f" v {w.name(meta['opposition'])}" if meta.get("opposition") else ""
    scope = w.filter_scope(v)
    nr = f", {num(r['no_result'])} no result" if r.get("no_result") else ""
    lines = [f"{w.name(meta['team'])}{vs}{f' ({scope})' if scope else ''}: **{num(r['matches'])}** matches, "
             f"**{num(r['won'])}** won, **{num(r['lost'])}** lost{nr} (win rate **{num(r['win_pct'])}%**)."]
    if r.get("batted_first"):
        lines.append(f"- Batting first: won {num(r['won_batting_first'])} of {num(r['batted_first'])}")
    if r.get("chased"):
        lines.append(f"- Chasing: won {num(r['won_chasing'])} of {num(r['chased'])}")
    if r.get("tosses_won") is not None:
        lines.append(f"- Tosses won: {num(r['tosses_won'])}")
    return "\n".join(lines)


def coverage(w, v, meta):
    rows = v.get("rows") or []
    if not rows:
        return None
    if "known_missing" in rows[0] or "coverage_pct" in rows[0]:          # the summary view
        want = {"Test": "Tests", "ODI": "ODIs"}.get(meta.get("format"))
        rows = [r for r in rows if not want or r.get("format") == want] or rows
        lines = ["What the data holds:"]
        for r in rows[:6]:
            cov = f", **{num(r['coverage_pct'])}%** complete ({num(r['known_missing'])} known missing)" if r.get("coverage_pct") is not None else ""
            lines.append(f"- {r.get('format')} ({'women' if r.get('gender') == 'female' else 'men'}): "
                         f"**{num(r.get('matches'))}** matches, {str(r.get('first', ''))[:4]}-{str(r.get('last', ''))[:4]}{cov}")
        return "\n".join(lines)
    n = next((x.split()[0] for x in v.get("notes") or [] if "known-missing" in x), None)
    lines = [f"**{n}** known-missing matches:" if n else "Known-missing matches:"]
    for r in rows[:4]:
        lines.append(f"- {r.get('date')}: {r.get('team1')} v {r.get('team2')}")
    return "\n".join(lines)


def compare(w, v, meta):
    return table_summary(w, v, meta, w.pick("Side by side ({s}):", "How they compare ({s}):").format(s=w.scope(v) or "all cricket"))


def venue(w, v, meta):
    rows = v.get("rows") or []
    if not rows:
        return None
    lines = [f"**{w.scope(v) or meta['venue']}**:"]
    for r in rows[:3]:
        lines.append(f"- {r.get('format')}: {num(r.get('matches'))} matches, average first innings "
                     f"**{num(r.get('avg_first_innings'))}**; won batting first {num(r.get('won_batting_first'))}, chasing "
                     f"{num(r.get('won_chasing'))} ({r.get('favours')})")
    return "\n".join(lines)


def records(w, v, meta):
    rows = v.get("rows") or []
    if not rows:
        return None
    lines = [f"Top of the list ({w.scope(v)}):"]
    for r in rows[:4]:
        who = r.get("player") or r.get("team")
        what = r.get("score") or r.get("runs") or r.get("figures") or r.get("total")
        if not what:
            return None
        lines.append(f"- {w.name(who)}: **{what}** v {r.get('opposition', '?')}, {str(r.get('date', ''))[:4]}")
    return "\n".join(lines)


def replay(w, v, meta):
    rows = v.get("rows") or []
    title = v.get("title", "")
    if not rows:
        return None
    lines = [f"{title}.", "The moments that swung it:"]
    for r in rows[:3]:
        lines.append(f"- {r.get('Moment')} ({r.get('Score')}): **{r.get('Helped')}** {r.get('Chance before %')}% → "
                     f"**{r.get('Chance after %')}%**")
    lines.append("\nYou can watch it ball by ball in the Matches view.")
    return "\n".join(lines)


def metrics(w, v, meta):
    ms = v.get("metrics") or []
    if not ms:
        return None
    lines = ["These metrics fit:"]
    for m in ms[:4]:
        lines.append(f"- **{m['label']}** (`{m['id']}`, {m['role']}): {m['definition']}")
    return "\n".join(lines)


def lookup(w, v, meta):
    c = v.get("candidates") or []
    if not c:
        return None
    lines = [f"'{v.get('query')}' matches:"]
    for x in c[:4]:
        lines.append(f"- **{x['name']}** ({', '.join(x.get('teams', [])[:2])}; {x.get('matches')} matches, {x.get('span')})")
    return "\n".join(lines)


def sql(w, v, meta):
    rows = v.get("rows") or []
    if not rows:
        return None
    if len(rows) == 1 and len(rows[0]) == 1:
        k, val = next(iter(rows[0].items()))
        if not val:
            return None
        return f"**{num(val)}** {meta.get('what') or k.replace('_', ' ') + ' in the data'}."
    keys = list(rows[0])
    return "From the database:\n" + "\n".join(f"- {r[keys[0]]}: **{num(r[keys[1]])}**" for r in rows[:5])


def chart(w, v, meta):
    if len(v.get("rows") or []) < 3:
        return None                          # a "trend" over one or two seasons isn't worth charting
    base = table_summary(w, v, meta, f"{w.name(meta['player'])}, season by season ({w.scope(v)}):")
    return base and base + f"\n\nThe chart shows the {meta['label']} over time."


def two_scopes(w, views, meta):
    lines = [f"{w.name(meta['player'])} in both:"]
    for v, scope in zip(views, meta["scopes"]):
        r = (v.get("rows") or [{}])[0]
        if "runs" not in r:
            return None
        lines.append(f"- {scope}: **{num(r['runs'])}** runs, average **{num(r.get('average'))}**, strike rate "
                     f"**{num(r.get('strike_rate'))}** ({num(r.get('matches'))} matches)")
    return "\n".join(lines)


def open_view(w, v, meta):
    return f"Done: I've opened {meta['what']} in the app."


WRITERS = {"leaderboard": leaderboard, "profile": profile, "stats": stats, "matchup": matchup, "compare": compare,
           "team": team, "coverage": coverage,
           "venue": venue, "records": records, "replay": replay, "metrics": metrics, "lookup": lookup, "sql": sql,
           "open": open_view}
INTROS = {"split": "{n}, broken down ({s}):", "form": "{n}'s form ({s}):", "arc": "The careers side by side ({s}):",
          "percentiles": "Where {n} ranks ({s}):", "matrix": "Who stands out ({s}):", "entry": "Where {n} comes in ({s}):",
          "similar": "Players with a profile most like {n}'s ({s}):", "team": "{n}'s results ({s}):",
          "luck": "Luck, by this measure ({s}):", "fibs": "How much of each stat is skill ({s}):",
          "coverage": "What the data has ({s}):"}


def write(rng: random.Random, kind: str, views: list, meta: dict) -> str | None:
    w = Writer(rng, views)
    data = [v for v in views if isinstance(v, dict) and "status" not in v and "error" not in v]
    if meta.get("use_last"):         # a recovery: the answer comes from the call that fixed the first
        data = data[-1:]
    if kind == "two_scopes":
        return two_scopes(w, data, meta)
    if kind == "chart":
        return chart(w, data[0], meta)
    if kind in WRITERS:
        return WRITERS[kind](w, data[0] if data else {}, meta)
    if kind == "luck":
        base = table_summary(w, data[0], meta, INTROS["luck"].format(s=w.scope(data[0]) or "all cricket"))
        return base and base + ("\n\nLuck is the part of their figures their measured skill doesn't explain: negative = "
                                "unluckier than their play deserved, positive = luckier.")
    if kind in INTROS:
        v = data[0]
        who = meta.get("player") or meta.get("team") or ""
        return table_summary(w, v, meta, INTROS[kind].format(n=w.name(who) if who else "", s=w.scope(v) or "all cricket"))
    return None
