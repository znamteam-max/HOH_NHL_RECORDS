import os
import sys
import datetime as dt
from zoneinfo import ZoneInfo
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from html import escape

# ====== Настройки Telegram из переменных окружения ======
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ====== Слежение за вехами (метрики: points | goals | games | wins) ======
# Убраны: Кучеров→1000 очков, Овечкин→1500 матчей
# Добавлены: Джейми Бэнн → 400 голов, Коннор Хеллебак → 350 побед
TRACK = [
    {"name": "Брэд Маршан",        "id": 8473419, "type": "points", "target": 1000},
    {"name": "Джейми Бэнн",        "id": 8473994, "type": "points", "target": 1000},
    {"name": "Леон Драйзайтль",    "id": 8477934, "type": "points", "target": 1000},

    {"name": "Александр Овечкин",  "id": 8471214, "type": "goals",  "target": 900},

    {"name": "Джон Таварес",       "id": 8475166, "type": "goals",  "target": 500},
    {"name": "Патрик Кейн",        "id": 8474141, "type": "goals",  "target": 500},
    {"name": "Джейми Бэнн",        "id": 8473994, "type": "goals",  "target": 400},  # NEW

    {"name": "Андрей Василевский", "id": 8476883, "type": "wins",   "target": 350},
    {"name": "Сергей Бобровский",  "id": 8475683, "type": "wins",   "target": 450},
    {"name": "Коннор Хеллебак",    "id": 8476945, "type": "wins",   "target": 350},  # NEW

    {"name": "Стивен Стэмкос",     "id": 8474564, "type": "goals",  "target": 600},
]

# ====== HTTP с ретраями ======
def make_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=6, connect=6, read=6,
        backoff_factor=0.7,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.headers.update({"User-Agent": "NHL-MilestonesBot/REST-only/1.4"})
    return s

SESSION = make_session()

# ====== Вспомогательная дата: "вчера по американскому времени (ET)" ======
def target_us_date() -> dt.date:
    """
    Целевая дата = 'вчера' по America/New_York (ET).
    """
    now_us = dt.datetime.now(tz=ZoneInfo("America/New_York"))
    return (now_us - dt.timedelta(days=1)).date()

def target_us_date_str() -> str:
    return target_us_date().isoformat()  # YYYY-MM-DD

# ====== Тоталы карьеры (регулярка) из api.nhle.com ======
def rest_skater_totals(player_id: int) -> dict:
    url = "https://api.nhle.com/stats/rest/en/skater/summary"
    params = {
        "isAggregate": "true",
        "isGame": "false",
        "cayenneExp": f'playerId={player_id} and gameTypeId=2',
    }
    r = SESSION.get(url, params=params, timeout=25)
    r.raise_for_status()
    row = (r.json().get("data") or [{}])[0]
    return {
        "games": int(row.get("gamesPlayed") or row.get("gp") or 0),
        "goals": int(row.get("goals") or 0),
        "assists": int(row.get("assists") or 0),
        "points": int(row.get("points") or 0),
    }

def rest_goalie_totals(player_id: int) -> dict:
    url = "https://api.nhle.com/stats/rest/en/goalie/summary"
    params = {
        "isAggregate": "true",
        "isGame": "false",
        "cayenneExp": f'playerId={player_id} and gameTypeId=2',
    }
    r = SESSION.get(url, params=params, timeout=25)
    r.raise_for_status()
    row = (r.json().get("data") or [{}])[0]
    return {
        "games": int(row.get("gamesPlayed") or row.get("gp") or 0),
        "wins": int(row.get("wins") or row.get("w") or 0),
    }

def get_career_stat(player_id: int, metric_type: str) -> dict:
    try:
        if metric_type == "wins":
            return rest_goalie_totals(player_id)
        return rest_skater_totals(player_id)
    except Exception:
        return {}

# ====== Данные ИМЕННО за целевую дату (ET) ======
def _get_json(url: str, params: dict) -> dict:
    try:
        r = SESSION.get(url, params=params, timeout=25)
        if r.status_code != 200:
            return {}
        return r.json()
    except Exception:
        return {}

def skater_game_row_on_date(player_id: int, ymd: str) -> dict | None:
    """
    Строка статистики полевого игрока за конкретную дату (ET).
    """
    url = "https://api.nhle.com/stats/rest/en/skater/summary"
    # В cayenneExp строки должны быть в кавычках
    exp = f'playerId={player_id} and gameTypeId=2 and gameDate="{ymd}"'
    params = {
        "isAggregate": "false",
        "isGame": "true",
        "cayenneExp": exp,
        "limit": "1",
    }
    j = _get_json(url, params)
    rows = j.get("data") or []
    return rows[0] if rows else None

def goalie_game_row_on_date(player_id: int, ymd: str) -> dict | None:
    """
    Строка статистики вратаря за конкретную дату (ET).
    """
    url = "https://api.nhle.com/stats/rest/en/goalie/summary"
    exp = f'playerId={player_id} and gameTypeId=2 and gameDate="{ymd}"'
    params = {
        "isAggregate": "false",
        "isGame": "true",
        "cayenneExp": exp,
        "limit": "1",
    }
    j = _get_json(url, params)
    rows = j.get("data") or []
    return rows[0] if rows else None

def _get_int(row: dict, keys: tuple[str, ...], default: int = 0) -> int:
    for k in keys:
        try:
            v = row.get(k)
            if v is None:
                continue
            return int(v)
        except Exception:
            continue
    return default

def delta_for_us_date(player_id: int, metric_type: str, ymd: str) -> tuple[int, bool]:
    """
    Возвращает (delta, played_on_date) для указанной целевой даты (ET).
      - points: голы + передачи в матче этой даты,
      - goals: голы в матче этой даты,
      - games: 1 если играл в эту дату, иначе 0,
      - wins: 1 если вратарь победил в эту дату, иначе 0.
    played_on_date=True, если игрок/вратарь выходил на лёд именно в эту дату (ET).
    """
    try:
        if metric_type == "wins":
            row = goalie_game_row_on_date(player_id, ymd)
            if not row:
                return 0, False
            decision = (row.get("decision") or row.get("gameOutcome") or "").strip().upper()
            if decision == "W":
                return 1, True
            w = _get_int(row, ("wins", "w"), 0)
            return (1 if w > 0 else 0), True

        # Полевые игроки
        row = skater_game_row_on_date(player_id, ymd)
        if not row:
            return 0, False

        if metric_type == "goals":
            return _get_int(row, ("goals", "g"), 0), True

        if metric_type == "points":
            g = _get_int(row, ("goals", "g"), 0)
            a = _get_int(row, ("assists", "a"), 0)
            return g + a, True

        if metric_type == "games":
            return 1, True

    except Exception:
        pass

    return 0, False

# ====== Формирование сообщения ======
def fmt_line(name: str, current: int, target: int, metric_ru: str, delta: int, played_on_date: bool) -> str:
    left = max(target - current, 0)
    status = "✅" if left == 0 else ("🔥" if left <= 10 else "🧊")
    total = max(target, current) or 1
    filled = 10 if left == 0 else max(1, round(10 * current / total))
    bar = "█" * filled + "░" * (10 - filled)
    suffix = f"(+{delta})" if played_on_date else "(=)"
    return f"<b>{escape(name)}</b> — {current} {metric_ru} · осталось {left} {suffix}\n{bar} {status}"

def build_message() -> str:
    london_now = dt.datetime.now(tz=ZoneInfo("Europe/London"))
    us_ymd = target_us_date()
    us_ymd_str = us_ymd.strftime("%d %b %Y")
    header = f"<b>НХЛ · Утреннее обновление по вехам</b> — {london_now.strftime('%d %b %Y')} · отчёт за {us_ymd_str} (ET)"

    lines = [header, ""]
    for it in TRACK:
        stat = get_career_stat(it["id"], it["type"])
        current = int(stat.get(it["type"], 0))
        delta, played = delta_for_us_date(it["id"], it["type"], us_ymd.isoformat())
        metric_ru = {"points": "очк.", "goals": "гол.", "games": "матч.", "wins": "побед"}[it["type"]]
        lines.append(fmt_line(it["name"], current, it["target"], metric_ru, delta, played))
    lines += ["", "ℹ️ Источник данных: api.nhle.com (REST). Отчёт за вчера по ET."]
    return "\n".join(lines)

# ====== Отправка в Telegram ======
def send_telegram(text: str) -> None:
    if not (BOT_TOKEN and CHAT_ID):
        print("No TELEGRAM_BOT_TOKEN/CHAT_ID in env", file=sys.stderr)
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    r = SESSION.post(url, json=payload, timeout=25)
    r.raise_for_status()

# ====== Точка входа ======
if __name__ == "__main__":
    try:
        msg = build_message()
        send_telegram(msg)
        print("OK")
    except Exception as e:
        print("ERROR:", repr(e), file=sys.stderr)
        sys.exit(1)
