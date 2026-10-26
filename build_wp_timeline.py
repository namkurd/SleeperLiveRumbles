"""Builds wp_timeline/week_N.json: a play-by-play fantasy-points timeline for every completed week, so the
page can draw a win-probability chart for any past Rumble.

Sleeper only reports running totals, but nflverse publishes every play (the day after the game). This turns
each play into fantasy-point events for the players who were in a league lineup that week, scored with the
league's own scoring_settings, and places them on a real wall-clock axis (nflverse stamps the start of every
drive with the real time). It then reconciles to Sleeper's real final points (epa_weekly.json "lineups"): any
difference becomes a labeled "adjustment" event at the end of that player's game, so a chart never ends
anywhere but on the true final score.

The page computes win probability from these events with the same model it uses live; nothing about
probability is computed here.

  wp_timeline/week_N.json
  { "v": 1, "season": 2026, "week": 7, "src": "pbp", "t0": <ms of the week's first kickoff>,
    "q": 0.97,                       # share of the week's points the play-by-play explained on its own
    "approx": false,                 # true when q is low enough that the chart is only a rough guide
    "g":  { "ARI@LAC": { "ko": 0, "end": 11000, "clk": [[sec, remaining_permille], ...] } },
    "tg": { "ARI": "ARI@LAC", "LAC": "ARI@LAC" },            # team -> game
    "tm": { "<sleeper_id>": "ARI" },                          # lineup player -> team (a D/ST is its own team)
    "ev": [[sec, "<sleeper_id>", points, "label"], ...],      # time-ordered; sec is seconds after t0
    "pre": { "<sleeper_id>": pregame_projection },
    "preSrc": "sleeper" | "avg" }
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import os
import sys
from datetime import datetime, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "wp_timeline")
EPA_PATH = os.path.join(HERE, "epa_weekly.json")
HISTORY_PATH = os.path.join(HERE, "rumbles_history.json")
SCORING_CACHE = os.path.join(OUT_DIR, "scoring.json")
PBP_URL = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.csv.gz"
PLAYER_IDS_URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv"
SLEEPER_API = "https://api.sleeper.app/v1"
SLEEPER_STATE = "https://api.sleeper.app/v1/state/nfl"
SLEEPER_PROJ = "https://api.sleeper.app/projections/nfl/{season}/{week}?season_type=regular"
TEAM_FIX = {"LA": "LAR"}          # nflverse -> Sleeper team abbreviations
REAL_SECONDS_PER_GAME_SECOND = 3.2   # only used past the last drive anchor of a game
APPROX_BELOW = 0.9

# Used only when the league's scoring_settings cannot be fetched and none is cached.
DEFAULT_SCORING = {
    "pass_yd": 0.04, "pass_td": 4, "pass_int": -2, "pass_2pt": 2,
    "rush_yd": 0.1, "rush_td": 6, "rush_2pt": 2,
    "rec": 1, "rec_yd": 0.1, "rec_td": 6, "rec_2pt": 2,
    "fum_lost": -2, "xpm": 1, "xpmiss": 0,
    "bonus_fd_qb": 0.2, "bonus_fd_rb": 0.5, "bonus_fd_wr": 0.5, "bonus_fd_te": 0.5,
    "fgm_0_19": 3, "fgm_20_29": 3, "fgm_30_39": 3, "fgm_40_49": 3, "fgm_50p": 3, "fgm_yds_over_30": 0.1,
    "fgmiss": -1,
    "sack": 1, "int": 2, "fum_rec": 2, "safe": 2, "def_td": 6, "blk_kick": 2, "def_st_td": 6,
    "pts_allow_0": 10, "pts_allow_1_6": 7, "pts_allow_7_13": 4, "pts_allow_14_20": 1,
    "pts_allow_21_27": 0, "pts_allow_28_34": -1, "pts_allow_35p": -4,
    "pass_sack": -0.25, "fum": -0.25, "tkl_loss": 0.35, "qb_hit": 0.2,
    "yds_allow_0_100": 5, "yds_allow_100_199": 3, "yds_allow_200_299": 2, "yds_allow_300_349": 0,
    "yds_allow_350_399": -3, "yds_allow_400_449": -3, "yds_allow_450_499": -4, "yds_allow_500_549": -5,
    "yds_allow_550p": -6,
}


def num(row: dict, key: str) -> float:
    try:
        return float(row.get(key) or 0)
    except ValueError:
        return 0.0


def sc(scoring: dict, key: str, default: float = 0.0) -> float:
    v = scoring.get(key)
    if v is None and key == "fgm_60p":
        v = scoring.get("fgm_60_plus")
    if v is None and key == "fgm_50p":
        v = scoring.get("fgm_50_plus")
    if v is None and key == "pts_allow_35p":
        v = scoring.get("pts_allow_35_plus")
    return float(v if v is not None else default)


def team_of(abbr: str) -> str:
    return TEAM_FIX.get(abbr, abbr)


def fg_points(scoring: dict, dist: float) -> float:
    if dist < 20:
        base = sc(scoring, "fgm_0_19", 3)
    elif dist < 30:
        base = sc(scoring, "fgm_20_29", 3)
    elif dist < 40:
        base = sc(scoring, "fgm_30_39", 3)
    elif dist < 50:
        base = sc(scoring, "fgm_40_49", 4)
    elif "fgm_50p" in scoring:
        base = sc(scoring, "fgm_50p")
    elif dist < 60:
        base = sc(scoring, "fgm_50_59", 5)
    else:
        base = sc(scoring, "fgm_60p", 5)
    return base + max(0.0, dist - 30) * sc(scoring, "fgm_yds_over_30") if dist > 30 else base


def pts_allowed_points(scoring: dict, allowed: int) -> float:
    if allowed <= 0:
        return sc(scoring, "pts_allow_0")
    if allowed <= 6:
        return sc(scoring, "pts_allow_1_6")
    if allowed <= 13:
        return sc(scoring, "pts_allow_7_13")
    if allowed <= 20:
        return sc(scoring, "pts_allow_14_20")
    if allowed <= 27:
        return sc(scoring, "pts_allow_21_27")
    if allowed <= 34:
        return sc(scoring, "pts_allow_28_34")
    return sc(scoring, "pts_allow_35p")


def fd_points(scoring: dict, pos: str, generic: str) -> float:
    return sc(scoring, generic) + sc(scoring, "bonus_fd_" + pos.lower())


def yds_allowed_points(scoring: dict, yds: float) -> float:
    for hi, key in ((100, "yds_allow_0_100"), (200, "yds_allow_100_199"), (300, "yds_allow_200_299"), (350, "yds_allow_300_349"),
                    (400, "yds_allow_350_399"), (450, "yds_allow_400_449"), (500, "yds_allow_450_499"), (550, "yds_allow_500_549")):
        if yds < hi:
            return sc(scoring, key)
    return sc(scoring, "yds_allow_550p")


def play_components(row: dict, scoring: dict, g2s: dict[str, str], pos_of: dict[str, str]) -> list[tuple[str, float, str, str]]:
    """(sleeper id, points, label, team) for one play, one entry per player. A D/ST is keyed by its team abbreviation."""
    merged: dict[str, list] = {}
    for sid, pts, label, team in _components(row, scoring, g2s, pos_of):
        m = merged.setdefault(sid, [0.0, [], team])
        m[0] += pts
        m[1].append((abs(pts), label))
    out = []
    for sid, (pts, parts, team) in merged.items():
        parts.sort(key=lambda x: -x[0])
        label = parts[0][1] + (", " + parts[1][1] if len(parts) > 1 and parts[1][0] >= 1 else "")
        out.append((sid, round(pts, 4), label, team))
    return out


def _components(row: dict, scoring: dict, g2s: dict[str, str], pos_of: dict[str, str]) -> list[tuple[str, float, str, str]]:
    out: list[tuple[str, float, str, str]] = []
    ptype = row.get("play_type") or ""
    if ptype == "no_play":
        return out
    pos, deft = team_of(row.get("posteam") or ""), team_of(row.get("defteam") or "")

    def add(gsis: str, pts: float, label: str, fd: str = "", team: str = "") -> None:
        sid = g2s.get(gsis or "")
        if sid:
            if fd and row.get("touchdown") != "1":
                pts += fd_points(scoring, pos_of.get(sid, ""), fd)
            if pts:
                out.append((sid, pts, label, team or pos))

    def add_def(team: str, pts: float, label: str) -> None:
        if team and pts:
            out.append((team, pts, label, team))

    two_pt = row.get("two_point_attempt") == "1"
    td = row.get("touchdown") == "1"
    td_team = team_of(row.get("td_team") or "")
    yds = num(row, "yards_gained")

    if two_pt:
        if row.get("two_point_conv_result") == "success":
            if ptype == "pass":
                add(row.get("passer_player_id"), sc(scoring, "pass_2pt", 2), "2-pt pass")
                add(row.get("receiver_player_id"), sc(scoring, "rec_2pt", 2), "2-pt catch")
            elif ptype == "run":
                add(row.get("rusher_player_id"), sc(scoring, "rush_2pt", 2), "2-pt run")
        return out

    if ptype == "pass":
        if row.get("sack") == "1":
            add_def(deft, sc(scoring, "sack", 1), "sack")
            add(row.get("passer_player_id"), sc(scoring, "pass_sack"), "sacked")
        else:
            complete = row.get("complete_pass") == "1"
            py = num(row, "passing_yards") if complete else 0.0
            ptd = row.get("pass_touchdown") == "1"
            add(row.get("passer_player_id"), py * sc(scoring, "pass_yd") + (sc(scoring, "pass_td") if ptd else 0),
                (f"{int(py)} yd TD pass" if ptd else f"{int(py)} yd pass") if complete else "",
                "pass_fd" if row.get("first_down_pass") == "1" else "")
            if complete:
                ry = num(row, "receiving_yards")
                add(row.get("receiver_player_id"), sc(scoring, "rec") + ry * sc(scoring, "rec_yd") + (sc(scoring, "rec_td") if ptd else 0),
                    f"{int(ry)} yd TD catch" if ptd else f"{int(ry)} yd catch",
                    "rec_fd" if row.get("first_down_pass") == "1" else "")
            if row.get("interception") == "1":
                add(row.get("passer_player_id"), sc(scoring, "pass_int", -2), "interception")
                add_def(deft, sc(scoring, "int", 2), "interception")
                if td and td_team == deft:
                    add_def(deft, sc(scoring, "def_td", 6), "interception return TD")
    elif ptype == "run":
        ry = num(row, "rushing_yards")
        rtd = row.get("rush_touchdown") == "1"
        add(row.get("rusher_player_id"), ry * sc(scoring, "rush_yd") + (sc(scoring, "rush_td") if rtd else 0),
            f"{int(ry)} yd TD run" if rtd else f"{int(ry)} yd run",
            "rush_fd" if row.get("first_down_rush") == "1" else "")
    elif ptype == "field_goal":
        res = row.get("field_goal_result")
        dist = num(row, "kick_distance")
        if res == "made":
            add(row.get("kicker_player_id"), fg_points(scoring, dist), f"{int(dist)} yd field goal")
        else:
            add(row.get("kicker_player_id"), sc(scoring, "fgmiss"), f"missed {int(dist)} yd field goal")
            if res == "blocked":
                add_def(deft, sc(scoring, "blk_kick", 2), "blocked kick")
    elif ptype == "extra_point":
        if row.get("extra_point_result") == "good":
            add(row.get("kicker_player_id"), sc(scoring, "xpm", 1), "extra point")
        else:
            add(row.get("kicker_player_id"), sc(scoring, "xpmiss"), "missed extra point")
            if row.get("extra_point_result") == "blocked":
                add_def(deft, sc(scoring, "blk_kick", 2), "blocked kick")
    elif ptype == "punt":
        if row.get("punt_blocked") == "1":
            add_def(deft, sc(scoring, "blk_kick", 2), "blocked punt")

    if ptype in ("pass", "run"):
        if row.get("tackled_for_loss") == "1":
            add_def(deft, sc(scoring, "tkl_loss"), "tackle for loss")
        if row.get("qb_hit") == "1" and ptype == "pass":
            add_def(deft, sc(scoring, "qb_hit"), "QB hit")
        if row.get("fumble") == "1":
            add(row.get("fumbled_1_player_id"), sc(scoring, "fum"), "fumble")
    # Fumbles and safeties can happen on any scrimmage play; returns on kicks.
    if ptype in ("pass", "run") and row.get("fumble_lost") == "1":
        add(row.get("fumbled_1_player_id"), sc(scoring, "fum_lost", -2), "fumble lost")
        if team_of(row.get("fumble_recovery_1_team") or "") == deft:
            add_def(deft, sc(scoring, "fum_rec", 2), "fumble recovery")
            if td and td_team == deft:
                add_def(deft, sc(scoring, "def_td", 6), "fumble return TD")
    if row.get("safety") == "1":
        add_def(deft, sc(scoring, "safe", 2), "safety")
    if ptype in ("kickoff", "punt") and td and row.get("return_touchdown") == "1":
        rt = row.get("punt_returner_player_id") or row.get("kickoff_returner_player_id")
        add(rt, sc(scoring, "def_st_td", 6), "return TD", team=td_team)
        add_def(td_team, sc(scoring, "def_st_td", 6), "return TD")
    return out


def elapsed_of(row: dict) -> float | None:
    """Seconds of game clock used so far (overtime continues past 3600)."""
    try:
        gsr = float(row["game_seconds_remaining"])
        q = int(float(row["qtr"]))
    except (KeyError, ValueError):
        return None
    return 3600 + (600 - gsr) if q >= 5 else 3600 - gsr


def parse_iso_ms(s: str) -> int | None:
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


class GameClock:
    """Maps a game's elapsed clock to real wall-clock ms, anchored on the real start time nflverse stamps on each drive."""

    def __init__(self, rows: list[dict]):
        anchors: list[tuple[float, int]] = []
        seen = set()
        for r in rows:
            d = r.get("drive")
            if not d or d in seen:
                continue
            ms = parse_iso_ms(r.get("drive_real_start_time") or "")
            el = elapsed_of(r)
            if ms is None or el is None:
                continue
            seen.add(d)
            anchors.append((el, ms))
        anchors.sort()
        mono: list[tuple[float, int]] = []
        for el, ms in anchors:
            if mono and (ms <= mono[-1][1] or el <= mono[-1][0]):
                continue
            mono.append((el, ms))
        self.anchors = mono
        self.ok = len(mono) >= 2

    def ms(self, el: float) -> int | None:
        a = self.anchors
        if not a:
            return None
        if el <= a[0][0]:
            return int(a[0][1] - (a[0][0] - el) * 1000 * 2.5)
        for i in range(1, len(a)):
            if el <= a[i][0]:
                (e0, m0), (e1, m1) = a[i - 1], a[i]
                return int(m0 + (m1 - m0) * (el - e0) / (e1 - e0))
        e, m = a[-1]
        return int(m + (el - e) * 1000 * REAL_SECONDS_PER_GAME_SECOND)


def yards_allowed(rows: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in rows:
        if r.get("play_type") in ("pass", "run") and r.get("two_point_attempt") != "1" and r.get("defteam"):
            d = team_of(r["defteam"])
            out[d] = out.get(d, 0.0) + num(r, "yards_gained")
    return out


def points_allowed(rows: list[dict]) -> dict[str, int]:
    """Points each defense allowed: the other team's final score minus its own defensive / special-teams touchdowns."""
    last = None
    for r in rows:
        if r.get("total_home_score") not in (None, ""):
            last = r
    if not last:
        return {}
    home, away = team_of(last.get("home_team") or ""), team_of(last.get("away_team") or "")
    hs, aws = int(float(last["total_home_score"])), int(float(last["total_away_score"]))
    extra = {home: 0, away: 0}
    for r in rows:
        if r.get("touchdown") == "1" and r.get("play_type") != "no_play":
            tdt, pos = team_of(r.get("td_team") or ""), team_of(r.get("posteam") or "")
            if tdt and pos and tdt != pos:
                extra[tdt] = extra.get(tdt, 0) + 6
    return {home: max(0, aws - extra.get(away, 0)), away: max(0, hs - extra.get(home, 0))}


def build_week(week: int, rows: list[dict], lineup: dict[str, list], g2s: dict[str, str], scoring: dict,
               prior_points: dict[str, list[float]], proj: dict[str, float] | None, season: int,
               pos_of: dict[str, str], partial: bool = False) -> dict | None:
    """One week's timeline. `lineup` is {roster_id: [[sleeper_id, points], ...]} (final, from Sleeper).

    partial=True is for a week still in progress: only the games already in the play-by-play are built, and only the
    players in those games are reconciled (everyone else has simply not scored yet, as far as the plays know)."""
    want = {pid: float(p) for starters in lineup.values() for pid, p in starters}
    by_game: dict[str, list[dict]] = {}
    for r in rows:
        if int(float(r["week"])) == week and (r.get("season_type") or "REG") == "REG":
            by_game.setdefault(r["game_id"], []).append(r)
    if not by_game:
        return None

    clocks: dict[str, GameClock] = {}
    gmeta: dict[str, dict] = {}
    raw: list[tuple[int, int, str, float, str, str]] = []   # (ms, order, sid, pts, label, gkey)
    teams: dict[str, str] = {}
    order = 0
    for gid, grows in by_game.items():
        grows.sort(key=lambda r: float(r.get("play_id") or 0))
        away, home = team_of(grows[0].get("away_team") or ""), team_of(grows[0].get("home_team") or "")
        gkey = f"{away}@{home}"
        clk = GameClock(grows)
        clocks[gkey] = clk
        if not clk.anchors:
            continue
        gmeta[gkey] = {"away": away, "home": home, "rows": grows}
        end_el = max((elapsed_of(r) or 0) for r in grows)
        for r in grows:
            el = elapsed_of(r)
            if el is None:
                continue
            ms = clk.ms(el)
            for sid, pts, label, team in play_components(r, scoring, g2s, pos_of):
                if sid not in want:
                    continue
                order += 1
                raw.append((ms, order, sid, pts, label, gkey))
                teams.setdefault(sid, team)
        gmeta[gkey]["end_ms"] = clk.ms(end_el)
        gmeta[gkey]["ko_ms"] = clk.ms(0.0)
    if not gmeta:
        return None

    # D/ST points allowed resolve when the game ends.
    for gkey, m in gmeta.items():
        allowed, yds = points_allowed(m["rows"]), yards_allowed(m["rows"])
        for tm, a in allowed.items():
            if tm in want:
                order += 1
                raw.append((m["end_ms"], order, tm, pts_allowed_points(scoring, a) + yds_allowed_points(scoring, yds.get(tm, 0.0)),
                            f"{a} points, {int(yds.get(tm, 0))} yards allowed", gkey))

    # A kicker / skill player's team: from the play he appeared in; a D/ST is its own team.
    for sid in want:
        if pos_of.get(sid) == "DEF":
            teams[sid] = sid
    team_game = {}
    for gkey, m in gmeta.items():
        team_game[m["away"]] = gkey
        team_game[m["home"]] = gkey

    # Reconcile to Sleeper's real final points.
    totals: dict[str, float] = {}
    for _, _, sid, pts, _, _ in raw:
        totals[sid] = totals.get(sid, 0.0) + pts
    explained = 0.0
    scored = 0.0
    week_end = max(m["end_ms"] for m in gmeta.values())
    for sid, final in want.items():
        tm = teams.get(sid)
        gkey = team_game.get(tm) if tm else None
        if partial and not gkey:
            continue
        diff = round(final - totals.get(sid, 0.0), 2)
        if pos_of.get(sid) != "DEF":
            scored += abs(final)
            explained += max(0.0, abs(final) - abs(diff))
        if abs(diff) >= 0.01:
            # A player whose game is not in the play-by-play yet settles when the last known game ends.
            order += 1
            raw.append((gmeta[gkey]["end_ms"] if gkey else week_end, order, sid, diff, "scoring adjustment", gkey or ""))
    q = round(explained / scored, 3) if scored else 1.0

    raw.sort()
    t0 = min(m["ko_ms"] for m in gmeta.values())
    sec = lambda ms: int(round((ms - t0) / 1000))  # noqa: E731

    games = {}
    for gkey, m in gmeta.items():
        clk, rows_g = clocks[gkey], m["rows"]
        pts: list[list[int]] = []
        last = -999
        for r in rows_g:
            el = elapsed_of(r)
            if el is None:
                continue
            s = sec(clk.ms(el))
            rem = 0 if el >= 3600 else int(round((3600 - el) / 3600 * 1000))
            if s - last >= 45 or rem == 0 or not pts:
                if pts and pts[-1][0] == s:
                    pts[-1][1] = rem
                else:
                    pts.append([s, rem])
                last = s
        if not pts or pts[0][1] != 1000:
            pts.insert(0, [sec(m["ko_ms"]), 1000])
        if pts[-1][1] != 0:
            pts.append([sec(m["end_ms"]), 0])
        games[gkey] = {"ko": sec(m["ko_ms"]), "end": sec(m["end_ms"]), "clk": pts}

    # Pregame projection per lineup player.
    pre, src = {}, "avg"
    for sid in want:
        if proj and sid in proj:
            pre[sid] = round(proj[sid], 2)
            src = "sleeper"
    for sid in want:
        if sid in pre:
            continue
        hist = prior_points.get(sid) or []
        if hist:
            pre[sid] = round(sum(hist) / len(hist), 2)
        else:
            same = [p for s2, h in prior_points.items() if pos_of.get(s2) == pos_of.get(sid) for p in h]
            pre[sid] = round(sum(same) / len(same), 2) if same else 8.0

    return {
        "v": 1, "season": season, "week": week, "src": "pbp", "t0": t0, "q": q, "approx": q < APPROX_BELOW,
        "g": games, "tg": team_game, "tm": {sid: teams[sid] for sid in want if sid in teams},
        "ev": [[sec(ms), sid, round(p, 2), label] for ms, _, sid, p, label, _ in raw],
        "pre": pre, "preSrc": src,
    }


def write_index() -> None:
    """Which weeks have a timeline, so the page asks only for files that exist."""
    weeks = sorted(int(f[5:-5]) for f in os.listdir(OUT_DIR) if f.startswith("week_") and f.endswith(".json"))
    with open(os.path.join(OUT_DIR, "index.json"), "w") as fh:
        json.dump({"weeks": weeks}, fh, separators=(",", ":"))


def already_good(path: str) -> bool:
    """A finished timeline is kept; one flagged approximate is retried, since nflverse fills in a day late."""
    try:
        return not json.load(open(path)).get("approx", True)
    except Exception:  # noqa: BLE001
        return False


def load_scoring() -> dict:
    """The league's scoring_settings (cached after the first fetch; a built-in default as the last resort)."""
    try:
        lid = json.load(open(HISTORY_PATH))["league_id"]
        s = requests.get(f"{SLEEPER_API}/league/{lid}", timeout=30).json().get("scoring_settings")
        if s:
            os.makedirs(OUT_DIR, exist_ok=True)
            json.dump(s, open(SCORING_CACHE, "w"), separators=(",", ":"))
            return s
    except Exception as e:  # noqa: BLE001
        print(f"[warn] league scoring unavailable ({e})", file=sys.stderr)
    try:
        return json.load(open(SCORING_CACHE))
    except Exception:  # noqa: BLE001
        print("[warn] using the built-in default scoring", file=sys.stderr)
        return dict(DEFAULT_SCORING)


def fetch_projections(season: int, week: int, scoring: dict) -> dict[str, float] | None:
    """Sleeper's pregame projections for a week, as league fantasy points (None when unavailable)."""
    try:
        arr = requests.get(SLEEPER_PROJ.format(season=season, week=week), timeout=60).json()
        out = {}
        for e in arr or []:
            st = e.get("stats") or {}
            if not st:
                continue
            out[str(e.get("player_id"))] = sum(float(v) * float(scoring.get(k, 0)) for k, v in st.items() if isinstance(v, (int, float)))
        return out or None
    except Exception:  # noqa: BLE001
        return None


def id_crosswalk() -> dict[str, str]:
    out: dict[str, str] = {}
    text = requests.get(PLAYER_IDS_URL, timeout=120).text
    for row in csv.DictReader(io.StringIO(text)):
        g, s = row.get("gsis_id"), row.get("sleeper_id")
        if g and s and g != "NA" and s != "NA":
            out[g] = s
    return out


def load_pbp_rows(season: int) -> list[dict] | None:
    """Every play of the season so far, or None when nflverse has none yet."""
    resp = requests.get(PBP_URL.format(season=season), timeout=300)
    if resp.status_code != 200:
        return None
    return list(csv.DictReader(io.StringIO(gzip.decompress(resp.content).decode("utf-8"))))


def main() -> None:
    epa = json.load(open(EPA_PATH))
    season = int(epa["season"])
    lineups = epa.get("lineups") or {}
    names = epa.get("names") or {}
    if not lineups:
        print("[info] no completed-week lineups yet; nothing to build")
        return
    rows = load_pbp_rows(season)
    if rows is None:
        print("[warn] no play-by-play yet; leaving wp_timeline as is")
        return
    played = {int(float(r["week"])) for r in rows if (r.get("season_type") or "REG") == "REG"}
    scoring = load_scoring()
    g2s = id_crosswalk()
    pos_of = {sid: v[1] for sid, v in names.items()}
    os.makedirs(OUT_DIR, exist_ok=True)
    by_week: dict[int, dict[str, float]] = {}
    for w, lu in lineups.items():
        for starters in lu.values():
            for pid, p in starters:
                by_week.setdefault(int(w), {})[pid] = float(p)
    for wk in sorted(int(w) for w in lineups):
        # Fallback pregame estimate when Sleeper's projections are gone: his average over his other weeks.
        prior: dict[str, list[float]] = {}
        for w2, d in by_week.items():
            if w2 != wk:
                for pid, p in d.items():
                    prior.setdefault(pid, []).append(p)
        path = os.path.join(OUT_DIR, f"week_{wk}.json")
        lineup = lineups[str(wk)]
        if wk in played and not already_good(path):
            tl = build_week(wk, rows, lineup, g2s, scoring, prior, fetch_projections(season, wk, scoring), season, pos_of)
            if tl and tl["q"] >= 0.5:
                with open(path, "w") as fh:
                    json.dump(tl, fh, separators=(",", ":"))
                print(f"[info] week {wk}: {len(tl['ev'])} events, q={tl['q']}, {os.path.getsize(path) // 1024} KB")
            elif tl:
                print(f"[warn] week {wk}: only {tl['q']:.0%} of the points explained so far (play-by-play incomplete?); will retry")
    write_index()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001 - an optional extra; never fail the standings build
        print(f"[warn] win-probability timeline build failed: {e}", file=sys.stderr)
