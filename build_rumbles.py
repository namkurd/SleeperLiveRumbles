#!/usr/bin/env python3
"""
build_rumbles.py

Computes the DTF Club "Rumbles" standings for the CURRENT season and writes
rumbles_history.json, which rumbles.html loads for everything already
finished before it takes over with live, client-side computation for the
current in-progress week.

Rumbles formula (verified against the "2026 TRUE STANDINGS" Google Sheet,
Week 1, exact match for all 12 managers):
    +9 Rumbles for winning your scheduled head-to-head matchup
    +1 Rumble for every OTHER team in the league you outscore that week
In a 12-team league that's up to 9 + 11 = 20 Rumbles in a single week.

Rumbles is a single-SEASON stat and resets each year, so this script only
ever scores weeks from the current season, and only weeks that are fully
complete -- the live/in-progress week is handled entirely in the browser
by rumbles.html (which also references Sleeper's own live-updating,
in-game player projections while games are underway, not just pregame
projections -- see rumbles.html for that logic).

This is a fully standalone project -- a separate repo from the career H2H
matrix project, with no dependency on it. League discovery and manager
display names are both derived at runtime from Sleeper's API (see
discover_current_league_id / build_manager_map below); edit
FALLBACK_LEAGUE_ID / FALLBACK_USERNAME / DISPLAY_NAME_OVERRIDES for your
own league.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

import requests

API_BASE = "https://api.sleeper.app/v1"
STATS_BASE = "https://api.sleeper.app/stats/nfl"
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rumbles_history.json")

# Used if auto-discovery can't find the league by name (see
# discover_current_league_id below).
FALLBACK_LEAGUE_ID = "1389416556617801728"  # 2026 season DTF Club league
FALLBACK_USERNAME = "namkurd"  # Ben's Sleeper username, used to auto-discover the league each year
DISPLAY_NAME_OVERRIDES = {
    # Sleeper display_name (lowercase) -> preferred display name. Add an
    # entry here for anyone whose Sleeper display name isn't what you want
    # shown on the standings page.
    #
    # NOTE: these keys must match each person's REAL Sleeper display_name
    # exactly (case-insensitively) -- Sleeper usernames often carry extra
    # digits/suffixes (e.g. a taken short name becomes "kohagan18"), so a
    # shortened guess silently fails to match and that manager's raw
    # Sleeper username leaks through onto the page instead. Confirmed
    # against the live league's real /users response -- if anyone's
    # Sleeper username ever changes, update the key here to match.
    "namkurd": "Ben",
    "kohagan18": "Kaitlyn",
    "stevster77": "Steven",
    "haanrolo": "Haan",
    "lalu101": "Ankit",
    "hellerch": "Christian",
    "slondon1": "Stephanie",
    "rrakower": "Ryan",
    "greenbayblay": "Joe",
    "thehebrewhammer24": "Jake",
    "ilovelamp917": "Alex",
    "legendaly": "Aidan",
}

RUMBLES_PER_WIN = 9
MAX_RUMBLES_PER_WEEK = 20  # 9 for the H2H win + up to 11 for outscoring the field (12-team league)

session = requests.Session()
session.headers.update({"User-Agent": "dtf-club-rumbles-builder/1.0"})


def get_json(url: str, retries: int = 3, backoff: float = 1.5) -> Any:
    last_err = None
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=20)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:  # noqa: BLE001 - we want to retry on anything transient
            last_err = e
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"Failed to GET {url} after {retries} attempts: {last_err}")


# ---------------------------------------------------------------------------
# League discovery + manager names
# ---------------------------------------------------------------------------


def discover_current_league_id(season: str) -> str:
    """Find this season's DTF Club league_id.

    Walks FALLBACK_USERNAME's leagues for the season and matches on the
    league name; falls back to a hardcoded id if that fails for any reason
    (renamed league, API hiccup, etc).
    """
    try:
        user = get_json(f"{API_BASE}/user/{FALLBACK_USERNAME}")
        user_id = user["user_id"]
        leagues = get_json(f"{API_BASE}/user/{user_id}/leagues/nfl/{season}")
        for league in leagues:
            if "dtf" in league.get("name", "").lower():
                return league["league_id"]
        if leagues:
            # Only one league for this user this season -- good enough odds.
            return leagues[0]["league_id"]
    except Exception as e:  # noqa: BLE001
        print(f"[warn] league auto-discovery failed ({e}); using FALLBACK_LEAGUE_ID", file=sys.stderr)

    return FALLBACK_LEAGUE_ID


def build_manager_map(league_id: str) -> dict[int, str]:
    """roster_id -> display name, for the CURRENT active rosters only.

    Names come straight from Sleeper's /users endpoint, with cosmetic
    overrides from DISPLAY_NAME_OVERRIDES above.
    """
    users = get_json(f"{API_BASE}/league/{league_id}/users")
    rosters = get_json(f"{API_BASE}/league/{league_id}/rosters")
    user_by_id = {u["user_id"]: u for u in users}
    out = {}
    for r in rosters:
        owner_id = r.get("owner_id")
        u = user_by_id.get(owner_id, {})
        raw_name = u.get("display_name", f"Roster {r['roster_id']}")
        name = DISPLAY_NAME_OVERRIDES.get(raw_name.lower(), raw_name)
        out[r["roster_id"]] = name
    return out


# ---------------------------------------------------------------------------
# Rumbles math
# ---------------------------------------------------------------------------


def get_completed_weeks(state: dict) -> list[int]:
    """Weeks of the CURRENT season that are fully finished.

    The in-progress/current week is deliberately excluded -- rumbles.html
    computes that one live, in the browser. We treat every week strictly
    before state['week'] during/after the regular season as complete; if
    we're in the preseason there are no completed weeks yet.
    """
    if state.get("season_type") == "pre":
        return []
    current_week = state.get("week") or 1
    return list(range(1, current_week))


def official_points(m: dict) -> float:
    """A roster's OFFICIAL final score for a matchup entry.

    Sleeper's matchup objects carry a `custom_points` field, null unless
    the commissioner manually overrode that roster's score for that week
    (a house-rule bonus/penalty, a corrected stat, etc) -- when set, it's
    the number Sleeper itself treats as final and displays, superseding
    the plain stat-calculated `points` field. Confirmed directly against
    a real override in this league (Week 1: `points` 140.61, `custom_points`
    160.08 -- a +19.47 commissioner adjustment) -- silently using `points`
    alone would understate that roster's PF, understate their opponent's
    PA by the same amount, and could even misstate how many other teams
    they outscored that week (the vs.-field "all play" comparison).
    """
    custom = m.get("custom_points")
    if custom is not None:
        return custom
    return m.get("points") or 0.0


def score_week(matchups: list[dict], manager_map: dict[int, str]) -> dict[int, dict]:
    """Given raw /matchups/{week} data, compute each roster's Rumbles etc.

    Returns roster_id -> {points, opponent_roster_id, opponent_points,
    h2h_win, rumbles, teams_outscored, teams_outscored_by}
    """
    by_roster = {m["roster_id"]: m for m in matchups}
    # Pair up rosters that share a matchup_id (the scheduled H2H game).
    pairs: dict[int, list[dict]] = {}
    for m in matchups:
        pairs.setdefault(m["matchup_id"], []).append(m)

    result: dict[int, dict] = {}
    all_scores = {rid: official_points(by_roster[rid]) for rid in by_roster}

    for _, pair in pairs.items():
        if len(pair) != 2:
            # Bye week or malformed data -- no H2H opponent to score against.
            for m in pair:
                result[m["roster_id"]] = {
                    "points": official_points(m),
                    "opponent_roster_id": None,
                    "opponent_points": None,
                    "h2h_win": None,
                }
            continue
        a, b = pair
        pa, pb = official_points(a), official_points(b)
        a_win = pa > pb
        b_win = pb > pa
        result[a["roster_id"]] = {
            "points": pa,
            "opponent_roster_id": b["roster_id"],
            "opponent_points": pb,
            "h2h_win": a_win,
        }
        result[b["roster_id"]] = {
            "points": pb,
            "opponent_roster_id": a["roster_id"],
            "opponent_points": pa,
            "h2h_win": b_win,
        }

    # Vs-the-field: for each roster, how many of the OTHER rosters did it outscore?
    for rid, info in result.items():
        my_score = info["points"]
        outscored = sum(1 for other_rid, s in all_scores.items() if other_rid != rid and my_score > s)
        outscored_by = sum(1 for other_rid, s in all_scores.items() if other_rid != rid and s > my_score)
        rumbles = outscored  # +1 per team outscored
        if info["h2h_win"]:
            rumbles += RUMBLES_PER_WIN
        info["teams_outscored"] = outscored
        info["teams_outscored_by"] = outscored_by
        info["rumbles"] = rumbles

    return result


# ---------------------------------------------------------------------------
# Custom rule: QB-injury backup-points adjustment
#
# House rule: if a manager's STARTED quarterback is ruled out mid-game and a
# backup QB from the same NFL team comes in and scores, the manager is
# credited with the COMBINED points of every QB from that NFL team who
# played in that game -- not just their own starter's. This cascades (a
# 3rd-string QB coming in after the backup also goes down adds their points
# too). Confirmed against a real example: league roster_id 6 (Alex), Week 1
# -- the commissioner's +19.47 `custom_points` override is exactly explained
# by a backup QB's individual score that game.
#
# This is meant to be a running, permanent LOG of every time this rule has
# actually gone into effect -- not a speculative "might this have
# happened" feed -- so it only ever logs an entry under one of two tiers:
#   "confirmed" -- the commissioner has already keyed in a `custom_points`
#     override for that roster/week. The strongest possible signal (a
#     human confirmed it), and this ALWAYS produces a log entry once an
#     override exists -- independent of whether the stats-based backup
#     detection below can identify exactly which backup(s) account for it
#     (it's best-effort for the display details, never a gate on whether
#     the event gets logged at all).
#   "likely"    -- no override yet, but at the moment this was checked
#     (live, mid-week -- or the first time the nightly job finalizes that
#     week, before the next week's practice reports reset the field) the
#     started QB's live `injury_status` read "Out"/"IR"/"PUP" AND a
#     same-team backup QB was found to have recorded real action that
#     game. `injury_status` is a live, current-only snapshot with no
#     historical record, so this is only ever captured FRESH (see
#     is_fresh below) and then carried forward permanently once captured
#     -- never re-derived from a now-stale current snapshot for an old
#     week.
# A same-team backup QB recording action with NEITHER of those two signals
# (no override, and not fresh / not ruled Out) is NOT logged at all -- it's
# exactly as likely to be a garbage-time benching as a real injury, and
# this log is meant to only contain confirmed-or-well-corroborated cases.
# KEY_ALIASES was previously {"kr_yd": "def_kr_yd"} -- reversed after a
# live check against Sleeper's own displayed numbers showed it was
# inflating every defense's actual score by its opponent's return
# yardage; see the full explanation on KEY_ALIASES in rumbles.html.
KEY_ALIASES: dict[str, str] = {}
TIER_SUM_KEYS = {"fgmiss": True}


def dot_product(stats_obj: dict | None, scoring_settings: dict) -> float:
    """Mirrors rumbles.html's dotProduct() -- always in "actual" mode here,
    since this feature only ever scores real post-game stat lines, never
    projections."""
    if not stats_obj or not scoring_settings:
        return 0.0
    total = 0.0
    for key, weight in scoring_settings.items():
        if not isinstance(weight, (int, float)):
            continue
        val = stats_obj.get(key)
        if not isinstance(val, (int, float)):
            alias_key = KEY_ALIASES.get(key)
            if alias_key and isinstance(stats_obj.get(alias_key), (int, float)):
                val = stats_obj[alias_key]
            elif TIER_SUM_KEYS.get(key):
                prefix = key + "_"
                tier_total = 0.0
                saw_tier = False
                for sk, sv in stats_obj.items():
                    if sk.startswith(prefix) and isinstance(sv, (int, float)):
                        tier_total += sv
                        saw_tier = True
                if saw_tier:
                    val = tier_total
        if isinstance(val, (int, float)):
            total += weight * val
    return total


def array_to_player_map(arr: list | None) -> dict[str, dict]:
    """Sleeper's bulk stats endpoint returns a JSON array of per-player
    entries ({player_id, stats: {...}, ...}) -- convert to a player_id-keyed
    lookup, same shape rumbles.html's arrayToPlayerMap() produces."""
    out: dict[str, dict] = {}
    if not arr:
        return out
    for entry in arr:
        pid = entry.get("player_id") if isinstance(entry, dict) else None
        if pid:
            out[pid] = entry.get("stats") or {}
    return out


def is_played(stats: dict | None) -> bool:
    """Did this player record real on-field action in this specific game's
    stat line? Used to detect "this backup QB actually came in and played",
    not just "is rostered somewhere"."""
    if not stats:
        return False
    for k in ("gp", "pass_att", "rush_att"):
        v = stats.get(k)
        if isinstance(v, (int, float)) and v >= 1:
            return True
    return False


def player_name(meta: dict | None) -> str:
    if not meta:
        return "Unknown"
    full = meta.get("full_name")
    if full:
        return full
    name = f"{meta.get('first_name') or ''} {meta.get('last_name') or ''}".strip()
    return name or str(meta.get("player_id") or "Unknown")


def build_team_qb_index(players_meta: dict[str, dict]) -> dict[str, list[str]]:
    """NFL team abbreviation -> every player_id on that team with
    position == "QB" (per Sleeper's current player metadata), regardless of
    whether they're rostered in this fantasy league -- the backup credited
    under this rule is very often a free agent from the affected manager's
    perspective (confirmed on the real Alex example: the backup's points
    don't appear anywhere in his own roster's players_points map)."""
    index: dict[str, list[str]] = {}
    for pid, meta in players_meta.items():
        if not isinstance(meta, dict) or meta.get("position") != "QB":
            continue
        team = meta.get("team")
        if not team:
            continue
        index.setdefault(team, []).append(pid)
    return index


def find_started_qbs(m: dict, players_meta: dict[str, dict]) -> list[tuple[str, dict]]:
    """EVERY player in this roster's `starters` whose position is QB (per
    Sleeper's player metadata) -- plural, because this league's
    roster_positions has TWO SUPER_FLEX slots and no dedicated single QB
    slot, so a manager can (and regularly does) start two QBs at once.
    Returning only the first one found silently ignored any injury/backup
    situation involving whichever started QB didn't happen to come first in
    `starters` -- the real case that caught this: a manager who started
    both Carson Wentz (MIN) and Caleb Williams (CHI) in the same week,
    where Williams got hurt and Tyson Bagent came in to relieve him, went
    completely undetected because Wentz -- listed earlier in `starters` --
    was the only one ever checked. Identifying WHO was started doesn't
    depend at all on whether a backup can also be found, so this is kept
    separate from the backup-detection below."""
    result = []
    for pid in m.get("starters") or []:
        if not pid or pid == "0":
            continue
        meta = players_meta.get(pid)
        if meta and meta.get("position") == "QB":
            result.append((pid, meta))
    return result


def find_backup_qbs(
    started_pid: str,
    meta: dict,
    players_meta: dict[str, dict],
    team_qb_index: dict[str, list[str]],
    stats_map: dict[str, dict],
    scoring_settings: dict,
    other_started_pids: set[str] | None = None,
) -> list[dict]:
    """Every OTHER quarterback on the started QB's NFL team who recorded
    real action (per is_played) in this week's actual stats -- sorted
    highest-scoring first. Empty if nobody else on that team played.
    `other_started_pids` excludes this roster's OTHER started QB(s) too (see
    find_started_qbs) -- in a SUPER_FLEX league a manager can deliberately
    start two QBs from the same NFL team at once, and that's the manager's
    own lineup choice, not a backup coming off the bench to relieve an
    injury."""
    team = meta.get("team")
    if not team:
        return []
    excluded = other_started_pids or set()
    played_ids = [
        cid for cid in team_qb_index.get(team, [])
        if cid != started_pid and cid not in excluded and is_played(stats_map.get(cid))
    ]
    backup_entries = [
        {"player_id": cid, "name": player_name(players_meta.get(cid)), "points": round(dot_product(stats_map.get(cid), scoring_settings), 2)}
        for cid in played_ids
    ]
    backup_entries.sort(key=lambda b: -b["points"])
    return backup_entries


def compute_qb_adjustments_for_week(
    week: int,
    matchups: list[dict],
    players_meta: dict[str, dict],
    team_qb_index: dict[str, list[str]],
    stats_map: dict[str, dict],
    scoring_settings: dict,
    manager_map: dict[int, str],
    is_fresh: bool,
    carried_by_roster: dict[int, list[dict]],
) -> list[dict]:
    entries: list[dict] = []
    for m in matchups:
        roster_id = m["roster_id"]
        manager = manager_map.get(roster_id, f"Roster {roster_id}")
        custom_points = m.get("custom_points")
        has_override = custom_points is not None

        if has_override:
            # A commissioner override ALWAYS produces a log entry -- that's
            # the whole point of this being a running log, not a
            # speculative feed. Identifying the specific injured/backup
            # QBs via the stats heuristic is best-effort on top of that,
            # never a gate on whether the override itself gets logged.
            #
            # This roster may have started more than one QB at once (see
            # find_started_qbs), but custom_points is a single override for
            # the whole roster/week, so at most one "confirmed" entry can
            # ever be logged here. Compute each started QB's own would-be
            # backup credit and pick whichever one the override is most
            # plausibly about: the one started QB with a real backup this
            # week if only one has one; if more than one does (two separate
            # in-game situations for the same roster in the same week),
            # whichever backup total is numerically closest to the override
            # itself; if none has an identifiable backup at all, fall back
            # to the first started QB with the override amount as "Backup
            # QB Points" and no names, same as this always did in a
            # single-QB league.
            started_list = find_started_qbs(m, players_meta)
            started_pids = {pid for pid, _ in started_list}
            override_delta = round(official_points(m) - (m.get("points") or 0.0), 2)
            candidates = []
            for pid, meta in started_list:
                backups = find_backup_qbs(pid, meta, players_meta, team_qb_index, stats_map, scoring_settings, started_pids)
                backup_total = round(sum(b["points"] for b in backups), 2) if backups else 0.0
                candidates.append({"pid": pid, "meta": meta, "backups": backups, "backup_total": backup_total})
            with_backups = [c for c in candidates if c["backups"]]
            if len(with_backups) == 1:
                chosen = with_backups[0]
            elif len(with_backups) > 1:
                chosen = min(with_backups, key=lambda c: abs(c["backup_total"] - override_delta))
            elif candidates:
                chosen = candidates[0]
            else:
                chosen = None

            if chosen:
                pid = chosen["pid"]
                injured_points = round(dot_product(stats_map.get(pid), scoring_settings), 2)
                injured_name = player_name(chosen["meta"])
                backup_entries = chosen["backups"]
            else:
                # Couldn't even identify a started QB (unexpected, but
                # don't let that swallow a real commissioner override) --
                # log it with the override amount as the best-available
                # number and no names.
                pid = None
                injured_points = 0.0
                injured_name = "Unknown"
                backup_entries = []
            backup_total = round(sum(b["points"] for b in backup_entries), 2) if backup_entries else override_delta
            entries.append(
                {
                    "week": week,
                    "roster_id": roster_id,
                    "manager": manager,
                    "injured_qb": {"player_id": pid, "name": injured_name, "points": injured_points},
                    "backup_qbs": backup_entries,
                    "backup_points_total": backup_total,
                    "confidence": "confirmed",
                    "injury_status_at_capture": None,
                    "custom_points_delta": override_delta,
                }
            )
            continue

        # No override -- while this week's injury_status snapshot is still
        # fresh (i.e. this IS the current week being scored live), log
        # every same-team backup QB who recorded real action: "likely" when
        # the starter's own injury_status actually corroborates them being
        # out, or the weaker "possible" tier when nothing corroborates that
        # yet -- a normal in-game substitution (a banged-up starter resting
        # a series, a blowout, etc) looks identical to a real injury from
        # the box score alone, so this is purely an awareness flag: it
        # never gets a custom_points_delta and the live page never folds
        # its points into anyone's total (see rumbles.html's
        # applyQbAdjustmentsToScores). Once the week is no longer fresh
        # (see the "carried" fallback below), an unconfirmed "possible"
        # entry is intentionally NOT carried forward -- most of these
        # resolve themselves as non-events, so only "likely" (a real,
        # corroborated injury) and "confirmed" (a commissioner decided it
        # WAS a real case, handled unconditionally above regardless of
        # freshness) persist in history.
        #
        # This roster may have started more than one QB at once (see
        # find_started_qbs), so every started QB is checked independently,
        # and a roster CAN log more than one separate entry in the same
        # week if more than one of its started QBs has its own same-team
        # backup who recorded action -- each one excludes this roster's
        # OTHER started QB(s) from counting as "a backup" (see
        # find_backup_qbs).
        started_list = find_started_qbs(m, players_meta)
        started_pids = {pid for pid, _ in started_list}
        if is_fresh and started_list:
            for pid, meta in started_list:
                backup_entries = find_backup_qbs(pid, meta, players_meta, team_qb_index, stats_map, scoring_settings, started_pids)
                if not backup_entries:
                    continue
                status = (meta.get("injury_status") or "").strip().lower()
                confidence = "likely" if status in ("out", "ir", "pup") else "possible"
                entries.append(
                    {
                        "week": week,
                        "roster_id": roster_id,
                        "manager": manager,
                        "injured_qb": {"player_id": pid, "name": player_name(meta), "points": round(dot_product(stats_map.get(pid), scoring_settings), 2)},
                        "backup_qbs": backup_entries,
                        "backup_points_total": round(sum(b["points"] for b in backup_entries), 2),
                        "confidence": confidence,
                        "injury_status_at_capture": meta.get("injury_status"),
                        "custom_points_delta": None,
                    }
                )
            continue

        # Not fresh -- carry forward every previously-captured "likely"
        # entry for this roster/week whose injured QB is still one this
        # roster has started (or couldn't be freshly re-checked at all).
        # carried_by_roster maps roster_id -> a LIST now, not a single
        # entry, for the same multi-QB reason as everywhere else above.
        for carried in carried_by_roster.get(roster_id, []):
            if carried.get("confidence") == "likely" and (not started_pids or carried.get("injured_qb", {}).get("player_id") in started_pids):
                entries.append(dict(carried, week=week, manager=manager))
    return entries


def build_history(
    league_id: str,
    season: str,
    season_type: str,
    completed_weeks: list[int],
    manager_map: dict[int, str],
    players_meta: dict[str, dict] | None = None,
    team_qb_index: dict[str, list[str]] | None = None,
    scoring_settings: dict | None = None,
    old_qb_by_week: dict[int, dict[int, list[dict]]] | None = None,
) -> dict:
    weekly: dict[str, dict] = {}
    qb_adjustments: list[dict] = []
    cumulative: dict[int, dict] = {
        rid: {"rumbles": 0, "pf": 0.0, "pa": 0.0, "h2h_w": 0, "h2h_l": 0, "vs_field_w": 0, "vs_field_l": 0}
        for rid in manager_map
    }
    old_qb_by_week = old_qb_by_week or {}
    freshest_week = max(completed_weeks) if completed_weeks else None
    can_detect_qb_adjustments = bool(players_meta and team_qb_index and scoring_settings)

    for week in completed_weeks:
        matchups = get_json(f"{API_BASE}/league/{league_id}/matchups/{week}")
        if not matchups:
            continue
        week_result = score_week(matchups, manager_map)
        weekly[str(week)] = week_result

        if can_detect_qb_adjustments:
            try:
                stats_arr = get_json(f"{STATS_BASE}/{season}/{week}?season_type={season_type}")
                stats_map = array_to_player_map(stats_arr)
                qb_adjustments.extend(
                    compute_qb_adjustments_for_week(
                        week,
                        matchups,
                        players_meta,
                        team_qb_index,
                        stats_map,
                        scoring_settings,
                        manager_map,
                        is_fresh=(week == freshest_week),
                        carried_by_roster=old_qb_by_week.get(week, {}),
                    )
                )
            except Exception as e:  # noqa: BLE001 - this is a nice-to-have overlay, never fatal to the main standings
                print(f"[warn] QB-adjustment detection failed for week {week} ({e}); skipping", file=sys.stderr)

        for rid, info in week_result.items():
            if rid not in cumulative:
                continue
            c = cumulative[rid]
            c["rumbles"] += info["rumbles"]
            c["pf"] += info["points"]
            if info["opponent_points"] is not None:
                c["pa"] += info["opponent_points"]
            if info["h2h_win"] is True:
                c["h2h_w"] += 1
            elif info["h2h_win"] is False:
                c["h2h_l"] += 1
            c["vs_field_w"] += info["teams_outscored"]
            c["vs_field_l"] += info["teams_outscored_by"]

    weeks_played = len(completed_weeks)
    max_possible = weeks_played * MAX_RUMBLES_PER_WEEK

    standings = []
    for rid, c in cumulative.items():
        last_week_key = str(completed_weeks[-1]) if completed_weeks else None
        last_week_info = weekly.get(last_week_key, {}).get(rid, {}) if last_week_key else {}
        this_week_rumbles = last_week_info.get("rumbles", 0)
        this_week_points = last_week_info.get("points", 0.0)
        standings.append(
            {
                "roster_id": rid,
                "manager": manager_map.get(rid, f"Roster {rid}"),
                "rumbles": c["rumbles"],
                "rumble_pct": round((c["rumbles"] / max_possible) * 100, 1) if max_possible else 0.0,
                "last_completed_week_rumbles": this_week_rumbles,
                "last_completed_week_points": round(this_week_points, 2),
                "pf": round(c["pf"], 2),
                "pa": round(c["pa"], 2),
                "h2h_w": c["h2h_w"],
                "h2h_l": c["h2h_l"],
                "vs_field_w": c["vs_field_w"],
                "vs_field_l": c["vs_field_l"],
            }
        )

    standings.sort(key=lambda s: (-s["rumbles"], -s["pf"]))
    for i, s in enumerate(standings, start=1):
        s["rank"] = i

    return {
        "season": season,
        "league_id": league_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "weeks_completed": completed_weeks,
        "max_rumbles_per_week": MAX_RUMBLES_PER_WEEK,
        "rumbles_per_h2h_win": RUMBLES_PER_WIN,
        "standings": standings,
        "weekly": weekly,
        "qb_adjustments": qb_adjustments,
    }


def load_previous_qb_adjustments() -> dict[int, dict[int, list[dict]]]:
    """roster_id-by-week lookup of whatever this script wrote for
    qb_adjustments LAST run (if any) -- used to carry forward a "likely"
    confidence tier captured while a week's injury_status snapshot was
    still fresh, rather than re-deriving it later from a now-stale one.
    Each roster_id maps to a LIST of entries, not a single one, since this
    league's SUPER_FLEX format lets a manager start more than one QB at
    once (see find_started_qbs) -- a roster/week can genuinely have more
    than one adjustment entry, and overwriting by roster_id would silently
    drop all but the last one read. Never fatal: a missing/corrupt previous
    file just means no carry-forward data, same as this feature's very
    first run."""
    if not os.path.exists(OUTPUT_PATH):
        return {}
    try:
        with open(OUTPUT_PATH) as f:
            old = json.load(f)
        out: dict[int, dict[int, list[dict]]] = {}
        for entry in old.get("qb_adjustments") or []:
            out.setdefault(entry["week"], {}).setdefault(entry["roster_id"], []).append(entry)
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[warn] couldn't read previous {OUTPUT_PATH} for QB-adjustment carry-forward ({e})", file=sys.stderr)
        return {}


def main() -> None:
    state = get_json(f"{API_BASE}/state/nfl")
    season = state["season"]
    season_type = state.get("season_type") or "regular"
    league_id = discover_current_league_id(season)
    manager_map = build_manager_map(league_id)
    completed_weeks = get_completed_weeks(state)

    print(f"[info] season={season} league_id={league_id} completed_weeks={completed_weeks}")
    print(f"[info] managers: {list(manager_map.values())}")

    if not completed_weeks:
        print("[info] no fully completed weeks yet this season -- writing an empty-but-valid history file")
        history = {
            "season": season,
            "league_id": league_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "weeks_completed": [],
            "max_rumbles_per_week": MAX_RUMBLES_PER_WEEK,
            "rumbles_per_h2h_win": RUMBLES_PER_WIN,
            "qb_adjustments": [],
            "standings": [
                {
                    "roster_id": rid,
                    "manager": name,
                    "rumbles": 0,
                    "rumble_pct": 0.0,
                    "last_completed_week_rumbles": 0,
                    "last_completed_week_points": 0.0,
                    "pf": 0.0,
                    "pa": 0.0,
                    "h2h_w": 0,
                    "h2h_l": 0,
                    "vs_field_w": 0,
                    "vs_field_l": 0,
                    "rank": i,
                }
                for i, (rid, name) in enumerate(sorted(manager_map.items(), key=lambda kv: kv[1]), start=1)
            ],
            "weekly": {},
        }
    else:
        players_meta: dict[str, dict] = {}
        team_qb_index: dict[str, list[str]] = {}
        scoring_settings: dict = {}
        try:
            league = get_json(f"{API_BASE}/league/{league_id}")
            scoring_settings = league.get("scoring_settings") or {}
            players_meta = get_json(f"{API_BASE}/players/nfl") or {}
            team_qb_index = build_team_qb_index(players_meta)
            print(f"[info] loaded {len(players_meta)} players, {sum(len(v) for v in team_qb_index.values())} QBs across {len(team_qb_index)} teams")
        except Exception as e:  # noqa: BLE001 - the QB-adjustments table is a nice-to-have overlay, never fatal to the main standings
            print(f"[warn] couldn't load player metadata/scoring_settings for QB-adjustment detection ({e}); standings will build without it", file=sys.stderr)

        old_qb_by_week = load_previous_qb_adjustments()
        history = build_history(
            league_id,
            season,
            season_type,
            completed_weeks,
            manager_map,
            players_meta,
            team_qb_index,
            scoring_settings,
            old_qb_by_week,
        )

    with open(OUTPUT_PATH, "w") as f:
        json.dump(history, f, indent=2)
    print(f"[info] wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
