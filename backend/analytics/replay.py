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


def key_moments(rows: list[dict], team1: str, team2: str, n: int = 6) -> list[dict]:
    """The wickets and boundaries that moved the win probability most (at least an over apart). Ordinary
    balls are left out: the model moves a little on every ball, and a big move is only a story with an event."""
    swings = []
    for prev, cur in zip(rows, rows[1:]):
        if not (cur["is_wicket"] or cur["runs_batter"] in (4, 6)) or cur["innings"] != prev["innings"]:
            continue                                                  # the change of innings isn't a moment
        delta = cur["wp_team1"] - prev["wp_team1"]
        swings.append((abs(delta), delta, prev, cur))
    swings.sort(key=lambda s: -s[0])
    picked: list[tuple] = []
    for size, delta, prev, cur in swings:
        if size < 0.04 or len(picked) == n:
            break
        if any(p[3]["innings"] == cur["innings"] and abs(p[3]["seq"] - cur["seq"]) < 6 for p in picked):
            continue
        picked.append((size, delta, prev, cur))
    moments = []
    for size, delta, prev, cur in sorted(picked, key=lambda s: (s[3]["innings"], s[3]["seq"])):
        gainer = team1 if delta > 0 else team2
        before = prev["wp_team1"] if gainer == team1 else 1 - prev["wp_team1"]
        after = cur["wp_team1"] if gainer == team1 else 1 - cur["wp_team1"]
        moments.append({"innings": cur["innings"], "seq": cur["seq"], "over": cur["over_num"], "ball": cur["ball_in_over"],
                        "text": _describe(cur), "team": gainer, "before": round(before, 3), "after": round(after, 3),
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
