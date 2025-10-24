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
TRACK = [
    {"name": "Никита Кучеров",   "id": 8476453, "type": "points", "target": 1000},
    {"name": "Брэд Маршан",      "id": 8473419, "type": "points", "target": 1000},
    {"name": "Джейми Бэнн",      "id": 8473994, "type": "points", "target": 1000},
    {"name": "Леон Драйзайтль",  "id": 8477934, "type": "points", "target": 1000},

    {"name": "Александр Овечкин","id": 8471214, "type": "games",  "target": 1500},
    {"name": "Александр Овечкин","id": 8471214, "type": "goals",  "target": 900},

    {"name": "Джон Таварес",     "id": 8475166, "type": "goals",  "target": 500},
    {"name": "Патрик Кейн",      "id": 8474141, "type": "goals",  "target": 500},

    {"name": "Андрей Василевский","id": 8476883,"type": "wins",   "target": 350},
    {"name": "Сергей Бобровский","id": 8475683, "type": "wins",   "target": 450},

    {"name": "Стивен Стэмкос",   "id": 8474564, "type": "goals",  "target": 600},
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
    s.headers.update({"User-Agent": "NHL-MilestonesBot/REST-only/1.2"})
    return s

SESSION = make_session()

# ====== Тоталы карьеры (регулярка) из api.nhle.com ======
def rest_skater_totals(player_id: int) -> dict:
    """
    Возвращает карьерные тоталы (регулярка) для полевого игрока.
    """
    url = "https://api.nhle.com/stats/rest/en/skater/summary"
    params = {
        "isAggregate": "true",
        "isGame": "false",
        "cayenneExp": f"playerId={player_id} and gameTypeId=2",
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
    """
    Возвращает карьерные тоталы (регулярка) для вратаря.
    """
    url = "https://api.nhle.com/stats/rest/en/goalie/summary"
    params = {
        "isAggregate": "true",
        "isGame": "false",
        "cayenneExp": f"playerId={player_id} and gameTypeId=2",
    }
    r = SESSION.get(url, params=params, timeout=25)
    r.raise_for_status()
    row = (r.json().get("data") or [{}])[0]
    return {
        "games": int(row.get("gamesPlayed") or row.get("gp") or 0),
        "wins": int(row.get("wins") or row.get("w") or 0),
    }

def get_career_stat(player_id: int, metric_type: str) -> dict:
    """
    Унифицированный доступ к тоталам по нужной метрике.
    """
    try:
        if metric_type == "wins":
            return rest_goalie_totals(player_id)
        return rest_skater_totals(player_id)
    except Exception:
        return {}

# ====== Данные по последнему матчу для дельты (+N) ======
def _get_json(url: str, params: dict) -> dict:
    try:
        r = SESSION.get(url, params=params, timeout=25)
        if r.status_code != 200:
            return {}
        return r.json()
    except Exception:
        return {}

def skater_last_game_row(player_id: int) -> dict | None:
    """
    Возвращает строку по последнему сыгранному матчу (регулярка) для полевого игрока.
    """
    url = "https://api.nhle.com/stats/rest/en/skater/summary"
    params = {
        "isAggregate": "false",
        "isGame": "true",
        "cayenneExp": f"playerId={player_id} and gameTypeId=2",
        "sort": '[{"property":"gameDate","direction":"DESC"}]',
        "limit": "1",
    }
    j = _get_json(url, params)
    rows = j.get("data") or []
    return rows[0] if rows else None

def goalie_last_game_row(player_id: int) -> dict | None:
    """
    Возвращает строку по последнему сыгранному матчу (регулярка) для вратаря.
    """
    url = "https://api.nhle.com/stats/rest/en/goalie/summary"
    params = {
        "isAggregate": "false",
        "isGame": "true",
        "cayenneExp": f"playerId={player_id} and gameTypeId=2",
        "sort": '[{"property":"gameDate","direction":"DESC"}]',
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

def last_game_delta(player_id: int, metric_type: str) -> int:
    """
    Возвращает +N для строки:
      - points: голы + передачи в последнем матче,
      - goals: голы в последнем матче,
      - games: 1 если играл в последнем матче, иначе 0,
      - wins: 1 если вратарь победил в последнем выходе, иначе 0.
    """
    try:
        if metric_type == "wins":
            row = goalie_last_game_row(player_id)
            if not row:
                return 0
            # Пытаемся определить победу
            decision = (row.get("decision") or row.get("gameOutcome") or "").strip().upper()
            if decision == "W":
                return 1
            # иногда кладут явное число побед в матче
            w = _get_int(row, ("wins", "w"), 0)
            return 1 if w > 0 else 0

        # Полевые игроки
        row = skater_last_game_row(player_id)
        if not row:
            return 0

        if metric_type == "goals":
            return _get_int(row, ("goals", "g"), 0)

        if metric_type == "points":
            g = _get_int(row, ("goals", "g"), 0)
            a = _get_int(row, ("assists", "a"), 0)
            return g + a

        if metric_type == "games":
            # если есть запись о последнем матче — значит, игрок выходил
            return 1

    except Exception:
        pass

    return 0

# ====== Формирование сообщения ======
def fmt_line(name: str, current: int, target: int, metric_ru: str, delta: int) -> str:
    left = max(target - current, 0)
    status = "✅" if left == 0 else ("🔥" if left <= 10 else "🧊")
    total = max(target, current) or 1
    filled = 10 if left == 0 else max(1, round(10 * current / total))
    bar = "█" * filled + "░" * (10 - filled)
    return f"<b>{escape(name)}</b> — {current} {metric_ru} · осталось {left} (+{delta})\n{bar} {status}"

def build_message() -> str:
    today = dt.datetime.now(tz=ZoneInfo("Europe/London")).strftime("%d %b %Y")
    lines = [f"<b>НХЛ · Утреннее обновление по вехам</b> — {today}", ""]
    for it in TRACK:
        stat = get_career_stat(it["id"], it["type"])
        current = int(stat.get(it["type"], 0))
        delta = last_game_delta(it["id"], it["type"])
        metric_ru = {"points": "очк.", "goals": "гол.", "games": "матч.", "wins": "побед"}[it["type"]]
        lines.append(fmt_line(it["name"], current, it["target"], metric_ru, delta))
    lines += ["", "ℹ️ Источник данных: api.nhle.com (REST). Обновление раз в сутки."]
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
