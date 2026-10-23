"""High-impact news check. ForexFactory has no official API, so this uses the widely used
weekly JSON mirror. It flags a red (high-impact) USD event inside a window around now,
which the engine uses to brake size."""
from __future__ import annotations
from datetime import datetime, timezone

import requests

FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


def check_calendar(cfg) -> dict:
    out = {"news_red_near": False, "next_event": None, "notes": []}
    try:
        events = requests.get(FEED, timeout=30).json()
    except Exception as e:  # noqa
        out["notes"].append(f"Calendar: feed unavailable ({e}).")
        return out

    now = datetime.now(timezone.utc)
    window = cfg.NEWS_WINDOW_MIN * 60
    soonest = None
    for ev in events:
        if ev.get("country") != "USD":
            continue
        if str(ev.get("impact", "")).lower() not in ("high", "red"):
            continue
        when = _parse(ev.get("date"))
        if when is None:
            continue
        delta = (when - now).total_seconds()
        if abs(delta) <= window:
            out["news_red_near"] = True
            out["next_event"] = f"{ev.get('title')} @ {when:%H:%M UTC}"
        if delta >= 0 and (soonest is None or when < soonest[0]):
            soonest = (when, ev.get("title"))
    if not out["next_event"] and soonest:
        out["next_event"] = f"{soonest[1]} @ {soonest[0]:%a %H:%M UTC}"
    return out


def _parse(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(s, fmt).astimezone(timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except Exception:  # noqa
        return None
