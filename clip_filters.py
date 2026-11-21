"""Which highlight uploads are not a play at all: press conferences, post-game interviews, locker-room chats, film breakdowns and
the like, the channels that mostly make them, and (in audit_wp_clips.py) videos that have been taken down.

Used by build_wp_clips.py when it matches uploads to plays, and by audit_wp_clips.py to clean up clips already saved.
"""
from __future__ import annotations

import re
import unicodedata


def _flat(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s.replace("’", "'").replace("-", " ")).strip()


# A title that is a person talking about a play (or the team around it) rather than the play. "on the run to ... TD" is a play; "Walker on his
# 76-yard run" is an interview, so "on his/her/their" (except "on his way") and "on the <noun>" of a play are caught, not "on the run".
INTERVIEW = re.compile(
    r"\b(press conferences?|news conferences?|presser|post ?game|media (availability|session|day)|locker room|inside the|interviews?|interviewed|"
    r"mic'?d up|sits? down|joins (the )?(show|podcast|us)|podcast|film (room|study|session)|analysis|analy[sz]es?|breaks? down|breaking down|explains?|explained|"
    r"discusses?|discussed|reacts?|reaction|talks?|speaks?|speaking|says|said|asked|answers (questions|reporters)|responds to|reflects?|recaps?|weighs in|opens up|"
    r"why (is|did|does)|what did|hc|head coach|coordinator|"
    r"on (his|her|their|that|this)(?! way\b)|on the (play|touchdown|interception|catch|win|loss|game|injury|moment|pick|sack|fumble))\b|"
    r"[:\-] ?['\"“‘]", re.I)

# Channels that post almost nothing but talk about plays (a few beat-writer sites and commentary channels; the Dan Patrick Show is a radio show, its clips have no video of the play). Everything else is judged by the title.
TALK_CHANNEL = re.compile(r"gazette|kmbc|digest|mile high huddle|drift theory|dan patrick", re.I)


def is_interview(title: str) -> bool:
    t = _flat(title)
    return bool(INTERVIEW.search(t) or INTERVIEW.search((title or "").lower()))


def is_talk_channel(channel: str | None) -> bool:
    return bool(TALK_CHANNEL.search(channel or ""))


def not_a_clip(title: str, channel: str | None = None) -> bool:
    return is_interview(title) or is_talk_channel(channel)
