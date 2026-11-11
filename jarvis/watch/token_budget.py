"""
Weekly token budget: "Diese Woche schon 80 Prozent der Tokens".

Jarvis runs on the Claude subscription, so tokens cost no money per call — but the subscription has
usage limits, and a runaway loop (a background job retrying, a long tool chain) used to show only in the
weekly tile. This counts the week's tokens from logs/usage.jsonl (Monday 00:00 to now, everything that
went to a model: conversations and background jobs, the same figure as the token tile) against
JARVIS_TOKEN_BUDGET_WEEK and says it once at each threshold per week.

  JARVIS_TOKEN_BUDGET_WEEK   tokens per week (2000000); 0 = no budget, no warnings
  JARVIS_TOKEN_BUDGET_WARN   thresholds in percent, comma-separated (80,100)

  week(now)       {"tokens", "budget", "pct", "week"} — for the tile and the spoken report
  check(now)      watchers.py, every full round: announces a newly crossed threshold once
  spoken(now)     one sentence for "Wie viele Tokens diese Woche?"
"""
import datetime as dt
import os

import commands
import events

BUDGET = int(os.environ.get("JARVIS_TOKEN_BUDGET_WEEK", "2000000") or 0)
WARN = sorted({int(x) for x in os.environ.get("JARVIS_TOKEN_BUDGET_WARN", "80,100").split(",") if x.strip().isdigit()})
LOCAL = ("local", "cache", "briefing-local")


def _monday(now):
    return (now - dt.timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


def week(now=None):
    import learning
    now = now or dt.datetime.now()
    start = _monday(now).strftime("%Y-%m-%d %H:%M:%S")
    tokens = sum(r.get("total_in", 0) + r.get("output", 0) for r in learning.rows(8)
                 if r.get("at", "") >= start and not str(r.get("route", "")).startswith(LOCAL))
    iso = now.isocalendar()
    return {"tokens": tokens, "budget": BUDGET, "week": f"{iso[0]}-W{iso[1]:02d}",
            "pct": round(100 * tokens / BUDGET) if BUDGET else None}


def _thousands(n):
    return f"{n / 1e6:.1f}".replace(".", ",") + " Millionen" if n >= 1e6 else f"{round(n / 1000)} tausend"


def check(now=None):
    """Announces the highest newly crossed threshold of this week. Returns the text or None."""
    if not BUDGET or not WARN:
        return None
    w = week(now)
    crossed = [t for t in WARN if w["pct"] >= t]
    if not crossed:
        return None
    top = crossed[-1]

    def mut(d):
        seen = d.get("token_budget") or {}
        if seen.get("week") == w["week"] and seen.get("warned", 0) >= top:
            return False
        d["token_budget"] = {"week": w["week"], "warned": top}
        return True
    if not commands.update_state(mut):
        return None
    if top >= 100:
        text = (f"Das Token-Budget der Woche ist aufgebraucht: {_thousands(w['tokens'])} von "
                f"{_thousands(BUDGET)}. Ich antworte weiter, aber schauen Sie bei Gelegenheit, was so viel braucht.")
    else:
        text = (f"Diese Woche sind schon {w['pct']} Prozent des Token-Budgets weg, "
                f"{_thousands(w['tokens'])} von {_thousands(BUDGET)}.")
    events.publish("say", text, kind="usage")
    return text


def spoken(now=None):
    if not BUDGET:
        return ""
    w = week(now)
    return f"Seit Montag {_thousands(w['tokens'])} Tokens, {w['pct']} Prozent des Wochenbudgets."
