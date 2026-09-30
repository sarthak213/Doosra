"""
Match Replay: find a limited-overs match, then replay it ball by ball with the win-probability
model (analytics.winprob): the worm, the scorecard, and the moments that swung it most.
"""

from __future__ import annotations

from analytics import db, winprob
from analytics.catalog import ResolutionError
from analytics.scope import build_scope, lit

LIMITED = ("T20", "IT20", "ODI", "ODM")


def list_matches(filters: dict | None = None, limit: int = 300) -> dict:
    """Matches within the filters (the shared filter bar's), newest first."""
    filters = {k: v for k, v in (filters or {}).items() if v not in (None, "")}
    scope = build_scope(**filters)
    where = " AND ".join(scope.match_clauses() + [f"m.match_type IN ({', '.join(lit(t) for t in LIMITED)})"])
    rows = db.query(f"""
        SELECT m.match_id, m.date, m.match_type, m.gender, m.event_name, m.match_number, m.season,
               m.team1, m.team2, split_part(m.venue, ',', 1) AS venue, m.winner, m.result, m.method,
               m.win_by_runs, m.win_by_wickets, COALESCE(m.balls_per_over, 6) = 6 AS replayable
        FROM matches m WHERE {where}
        ORDER BY m.date DESC, m.match_id DESC LIMIT {int(limit) + 1}""")
    more = len(rows) > limit
    return {"matches": [{**r, "summary": result_text(r)} for r in rows[:limit]], "more": more,
            "applied": scope.applied, "notes": scope.notes}


def result_text(m: dict) -> str:
    if m.get("result") == "no result":
        return "No result"
    if m.get("result") == "tie":
        return f"Tied{' (' + m['winner'] + ' won the super over)' if m.get('winner') else ''}"
    if not m.get("winner"):
        return "No result"
    how = (f"by {m['win_by_runs']} runs" if m.get("win_by_runs") else
           f"by {m['win_by_wickets']} wickets" if m.get("win_by_wickets") else "")
    rain = " (DLS)" if m.get("method") == "D/L" else ""
    return f"{m['winner']} won {how}{rain}".strip()


def _describe(r: dict) -> str:
    ball = f"{r['over_num']}.{r['ball_in_over']}"
    if r["is_wicket"]:
        who = r["player_dismissed"] or r["batter"]
        kind = r["wicket_kind"] or "out"
        by = f", {r['bowler']}" if kind not in ("run out", "retired hurt", "obstructing the field") else ""
        return f"{ball}: {who} {kind}{by}"
    if r["runs_batter"] in (4, 6):
        return f"{ball}: {r['batter']} hits {r['bowler']} for {r['runs_batter']}"
    return f"{ball}: {r['batter']} {r['runs_total']} off {r['bowler']}"


def _over_text(balls: list[dict]) -> str:
    runs = sum(b["runs_total"] for b in balls)
    wkts = sum(1 for b in balls if b["is_wicket"])
    bowlers = sorted({b["bowler"] for b in balls})
    w = f", {wkts} wicket{'s' if wkts > 1 else ''}" if wkts else ""
    return f"Over {balls[0]['over_num'] + 1} from {' and '.join(bowlers)}: {runs} run{'s' if runs != 1 else ''}{w}"


def key_moments(rows: list[dict], team1: str, team2: str, n: int = 6) -> list[dict]:
    """What moved the game most: single wickets and boundaries, and whole overs (a tight over at the death
    can swing a chase more than any one ball). Largest swings first, then shown in match order; an over
    and a ball inside it aren't both picked. Ordinary single balls are left out: the model moves a little
    on every ball, and a big move is only a story with an event or an over behind it."""
    candidates = []                                  # (size, delta, before_row, after_row, text, first_seq)
    for prev, cur in zip(rows, rows[1:]):
        if cur["innings"] != prev["innings"]:
            continue                                                  # the change of innings isn't a moment
        if cur["is_wicket"] or cur["runs_batter"] in (4, 6):
            d = cur["wp_team1"] - prev["wp_team1"]
            candidates.append((abs(d), d, prev, cur, _describe(cur), cur["seq"]))
    overs: dict[tuple, list] = {}
    for r in rows:
        overs.setdefault((r["innings"], r["over_num"]), []).append(r)
    by_seq = {(r["innings"], r["seq"]): r for r in rows}
    for (inn, _), balls in overs.items():
        before = by_seq.get((inn, balls[0]["seq"] - 1))
        if not before:
            continue                                                  # the innings' first over has no "before"
        d = balls[-1]["wp_team1"] - before["wp_team1"]
        candidates.append((abs(d), d, before, balls[-1], _over_text(balls), balls[0]["seq"]))
    candidates.sort(key=lambda c: -c[0])
    picked: list[tuple] = []
    for c in candidates:
        if c[0] < 0.05 or len(picked) == n:
            break
        span = (c[3]["innings"], c[5], c[3]["seq"])
        if any(p[3]["innings"] == span[0] and not (span[2] < p[5] or span[1] > p[3]["seq"]) for p in picked):
            continue                                                  # overlaps a moment already picked
        picked.append(c)
    moments = []
    for size, delta, prev, cur, text, first in sorted(picked, key=lambda c: (c[3]["innings"], c[3]["seq"])):
        gainer = team1 if delta > 0 else team2
        before = prev["wp_team1"] if gainer == team1 else 1 - prev["wp_team1"]
        after = cur["wp_team1"] if gainer == team1 else 1 - cur["wp_team1"]
        moments.append({"innings": cur["innings"], "seq": cur["seq"], "over": cur["over_num"], "ball": cur["ball_in_over"],
                        "text": text, "kind": "over" if text.startswith("Over ") else "ball", "team": gainer,
                        "before": round(before, 3), "after": round(after, 3),
                        "score": f"{cur['score']}/{cur['wickets']}", "batting_team": cur["batting_team"]})
    return moments


def replay(match_id: str) -> dict:
    meta = db.query("""
        SELECT match_id, date, match_type, gender, event_name, match_number, season, team1, team2, venue, city,
               toss_winner, toss_decision, winner, result, method, win_by_runs, win_by_wickets, target_runs,
               target_overs, overs_per_innings, player_of_match
        FROM matches WHERE match_id = ?""", [match_id])
    if not meta:
        raise ResolutionError("match", match_id, "No match with that id.")
    m = meta[0]
    if m["match_type"] not in LIMITED:
        raise ResolutionError("match", match_id, "Match Replay covers limited-overs matches (T20 and ODI).")
    scored = winprob.predict_match(match_id)
    rows = scored["rows"] if scored else []
    balls = [{"innings": r["innings"], "seq": r["seq"], "over": r["over_num"], "ball": r["ball_in_over"],
              "legal": r["legal"], "score": r["score"], "wickets": r["wickets"], "batting_team": r["batting_team"],
              "runs": r["runs_total"], "wicket": bool(r["is_wicket"]),
              "boundary": r["runs_batter"] if r["runs_batter"] in (4, 6) else None,
              "wp": round(r["wp_team1"], 4), "text": _describe(r)} for r in rows]
    innings = []
    for inn in (1, 2):
        mine = [r for r in rows if r["innings"] == inn]
        if not mine:
            continue
        last = mine[-1]
        innings.append({"innings": inn, "team": last["batting_team"], "score": last["score"], "wickets": last["wickets"],
                        "overs": f"{last['legal'] // 6}.{last['legal'] % 6}", "target": last["target"]})
    batting = db.query("""
        SELECT innings_num AS innings, player, team, position, runs, balls, fours, sixes, out, dismissal
        FROM batting_innings WHERE match_id = ? AND innings_num IN (1, 2) AND balls > 0
        ORDER BY innings_num, position""", [match_id])
    bowling = db.query("""
        SELECT innings_num AS innings, player, team, balls, runs, wickets, dots
        FROM bowling_innings WHERE match_id = ? AND innings_num IN (1, 2)
        ORDER BY innings_num, wickets DESC, runs""", [match_id])
    return {
        "match": {**m, "summary": result_text(m)},
        "innings": innings,
        "balls": balls,
        "moments": key_moments(rows, m["team1"], m["team2"]) if rows else [],
        "batting": batting,
        "bowling": bowling,
        "model": scored["model"] if scored else None,
        "note": None if scored else "No win-probability model for this match (it needs 6-ball overs).",
        "rain": m["method"] == "D/L",
    }


def match_story(team: str, opposition: str | None = None, date: str | None = None, **filters) -> dict:
    """The copilot's view of a match: find it (the latest that fits, or the one on `date`), then its result,
    how the win chance moved over by over, and the key moments as a table."""
    from analytics.results import result

    wanted = {**{k: v for k, v in filters.items() if v not in (None, "")}, "team": team}
    if opposition:
        wanted["opposition"] = opposition
    found = list_matches(wanted, limit=40)["matches"]
    if date:
        found = [m for m in found if str(m["date"]).startswith(date)]
    if not found:
        return result(f"No limited-overs match found for {team}{' v ' + opposition if opposition else ''}",
                      [], [], notes=["Match Replay covers T20 and ODI matches. Try a season, competition or date."])
    m = found[0]
    r = replay(m["match_id"])
    moments = [[f"innings {x['innings']}, {x['over']}.{x['ball']}", x["text"].split(": ", 1)[-1], x["score"], x["team"],
                round(100 * x["before"]), round(100 * x["after"])] for x in r["moments"]]
    last_ball: dict[tuple, dict] = {}
    for b in r["balls"]:                                   # the last ball of each over (extras make some overs longer)
        last_ball[(b["innings"], b["over"])] = b
    by_over: dict[int, list] = {}
    for (inn, over), b in last_ball.items():
        by_over.setdefault(inn, []).append([over + 1, round(100 * b["wp"])])
    others = [f"{x['date']} ({x['summary']})" for x in found[1:6]]
    notes = ["'Chance before/after' is the helped side's chance of winning, just before and after that ball. "
             f"win_chance_by_over gives {r['match']['team1']}'s chance at the end of each over. From Doosra's "
             "win-probability model (trained on earlier matches only), not the bookmakers."]
    if others:
        notes.append("Other matches that fit (pass date=YYYY-MM-DD for one of them): " + "; ".join(others))
    if r.get("rain"):
        notes.append("Rain-affected (DLS): the chase uses the revised target.")
    title = (f"{r['match']['team1']} v {r['match']['team2']}, {r['match'].get('event_name') or r['match']['match_type']}, "
             f"{str(r['match']['date'])[:10]}: {r['match']['summary']}")
    return result(title, ["Ball", "Moment", "Score", "Helped", "Chance before %", "Chance after %"], moments,
                  notes=notes, innings=r["innings"], win_chance_by_over={"team": r["match"]["team1"], **by_over},
                  match_id=m["match_id"], replay_link=f"/matches/{m['match_id']}")
