import os, sys, datetime as dt
from zoneinfo import ZoneInfo
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from html import escape

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

TRACK = [
    {"name":"Никита Кучеров","id":8476453,"type":"points","target":1000},
    {"name":"Брэд Маршан","id":8473419,"type":"points","target":1000},
    {"name":"Джейми Бэнн","id":8473994,"type":"points","target":1000},
    {"name":"Леон Драйзайтль","id":8477934,"type":"points","target":1000},
    {"name":"Александр Овечкин","id":8471214,"type":"games","target":1500},
    {"name":"Александр Овечкин","id":8471214,"type":"goals","target":900},
    {"name":"Джон Таварес","id":8475166,"type":"goals","target":500},
    {"name":"Патрик Кейн","id":8474141,"type":"goals","target":500},
    {"name":"Андрей Василевский","id":8476883,"type":"wins","target":350},
    {"name":"Сергей Бобровский","id":8475683,"type":"wins","target":450},
    {"name":"Стивен Стэмкос","id":8474564,"type":"goals","target":600},
]

COACH = {"name": "Пол Морис", "target": 2000}

def make_session():
    s = requests.Session()
    retries = Retry(
        total=6, connect=6, read=6, backoff_factor=0.7,
        status_forcelist=[429,500,502,503,504],
        allowed_methods=["GET","POST"],
        raise_on_status=False
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.headers.update({"User-Agent": "NHL-MilestonesBot/REST-only/1.0"})
    return s

SESSION = make_session()

def rest_skater_totals(player_id: int) -> dict:
    url = ("https://api.nhle.com/stats/rest/en/skater/summary"
           f"?isAggregate=true&isGame=false&cayenneExp=playerId={player_id}%20and%20gameTypeId=2")
    r = SESSION.get(url, timeout=25); r.raise_for_status()
    row = (r.json().get("data") or [{}])[0]
    return {
        "games": int(row.get("gamesPlayed") or row.get("gp") or 0),
        "goals": int(row.get("goals") or 0),
        "assists": int(row.get("assists") or 0),
        "points": int(row.get("points") or 0),
    }

def rest_goalie_totals(player_id: int) -> dict:
    url = ("https://api.nhle.com/stats/rest/en/goalie/summary"
           f"?isAggregate=true&isGame=false&cayenneExp=playerId={player_id}%20and%20gameTypeId=2")
    r = SESSION.get(url, timeout=25); r.raise_for_status()
    row = (r.json().get("data") or [{}])[0]
    return {"games": int(row.get("gamesPlayed") or row.get("gp") or 0),
            "wins": int(row.get("wins") or row.get("w") or 0)}

def get_career_stat(player_id: int, metric_type: str) -> dict:
    try:
        return rest_goalie_totals(player_id) if metric_type == "wins" else rest_skater_totals(player_id)
    except Exception:
        return {}

def coach_games_paul_maurice() -> int | None:
    url = ("https://api.nhle.com/stats/rest/en/coach/summary"
           "?isAggregate=true&isGame=false&cayenneExp=fullName=%22Paul%20Maurice%22")
    try:
        r = SESSION.get(url, timeout=25)
        if r.status_code != 200:
            return None
        rows = r.json().get("data") or []
        if not rows: return None
        for k in ("gamesCoached","g","games"):
            if k in rows[0]:
                return int(rows[0][k])
    except Exception:
        return None
    return None

def fmt_line(name: str, current: int, target: int, metric_ru: str) -> str:
    left = max(target - current, 0)
    status = "✅" if left == 0 else ("🔥" if left <= 10 else "🧊")
    total = max(target, current) or 1
    filled = 10 if left == 0 else max(1, round(10 * current / total))
    bar = "█" * filled + "░" * (10 - filled)
    return f"<b>{escape(name)}</b> — {current} {metric_ru} · осталось {left}\n{bar} {status}"

def build_message() -> str:
    today = dt.datetime.now(tz=ZoneInfo("Europe/London")).strftime("%d %b %Y")
    lines = [f"<b>НХЛ · Утреннее обновление по вехам</b> — {today}", ""]
    for it in TRACK:
        stat = get_career_stat(it["id"], it["type"])
        current = int(stat.get(it["type"], 0))
        metric_ru = {"points":"очк.","goals":"гол.","games":"матч.","wins":"побед"}[it["type"]]
        lines.append(fmt_line(it["name"], current, it["target"], metric_ru))
    g = coach_games_paul_maurice()
    if g is not None:
        lines.append(fmt_line("Пол Морис", g, COACH["target"], "матч."))
    else:
        lines.append("<b>Пол Морис</b> — источник недоступен.")
    lines += ["", "ℹ️ Источник данных: api.nhle.com (REST). Обновление раз в сутки."]
    return "\n".join(lines)

def send_telegram(text: str):
    if not (BOT_TOKEN and CHAT_ID):
        print("No TELEGRAM_BOT_TOKEN/CHAT_ID in env", file=sys.stderr)
        return
    r = SESSION.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=25
    )
    r.raise_for_status()

if __name__ == "__main__":
    try:
        msg = build_message()
        send_telegram(msg)
        print("OK")
    except Exception as e:
        print("ERROR:", repr(e), file=sys.stderr)
        sys.exit(1)
