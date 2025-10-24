import os, sys, json, time, datetime as dt
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup
from html import escape

# --- Настройки окружения (Telegram) ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

# --- Игроки и цели ---
# type: one of ["points","goals","games","wins"]
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

COACH = {
    "name": "Пол Морис",
    "target": 2000,
    # первичный источник для парсинга, если REST не даст
    "records_page": "https://records.nhl.com/coaches/paul-maurice-73"
}

# --- helpers ---
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "MilestonesBot/1.0 (+github actions)"})


def get_career_stat(person_id: int) -> dict:
    """Возвращает карьерные тоталы с учётом текущего сезона."""
    url = f"https://statsapi.web.nhl.com/api/v1/people/{person_id}?expand=person.stats&stats=careerRegularSeason"
    r = SESSION.get(url, timeout=25)
    r.raise_for_status()
    data = r.json()
    try:
        stat = data["people"][0]["stats"][0]["splits"][0]["stat"]
    except Exception:
        return {}
    # нормализуем доступные поля
    return {
        "games": int(stat.get("games", 0)),
        "goals": int(stat.get("goals", 0)),
        "assists": int(stat.get("assists", 0)),
        "points": int(stat.get("points", 0)),
        "wins": int(stat.get("wins", 0)),  # для вратарей
        # на будущее: "losses", "ot", "savePercentage" и т.п.
    }


def try_coach_rest_fullname(fullname: str) -> int | None:
    """Пробуем найти игры тренера через REST stats (недокументировано, но часто работает)."""
    endpoints = [
        f"https://api.nhle.com/stats/rest/en/coach/summary?isAggregate=true&isGame=false&cayenneExp=fullName=%22{requests.utils.quote(fullname)}%22",
        f"https://api.nhle.com/stats/rest/en/coach/summary?isAggregate=false&isGame=false&cayenneExp=fullName=%22{requests.utils.quote(fullname)}%22",
        f"https://api.nhle.com/stats/rest/en/coach/summary?cayenneExp=coachName=%22{requests.utils.quote(fullname)}%22",
    ]
    for url in endpoints:
        try:
            r = SESSION.get(url, timeout=25)
            if r.status_code != 200:
                continue
            j = r.json()
            rows = j.get("data") or j.get("rows") or []
            if not rows:
                continue
            # В агрегате обычно есть field gamesCoached или g (в зависимости от отчёта)
            keys = ["gamesCoached", "g", "games"]
            for k in keys:
                if k in rows[0]:
                    val = rows[0][k]
                    try:
                        return int(val)
                    except Exception:
                        pass
        except Exception:
            continue
    return None


def scrape_maurice_records_page(url: str) -> int | None:
    """Парсим официальную страницу рекордов НХЛ по Полу Морису и вытаскиваем total Games (регулярка)."""
    try:
        r = SESSION.get(url, timeout=25)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        # Ищем таблицу Regular Season и строку Totals
        # Подстраиваемся под разметку: ищем все таблицы и строку, где есть 'Totals'/'Total' и столбец 'G'
        tables = soup.find_all("table")
        best_guess = None
        for tbl in tables:
            head = [th.get_text(strip=True) for th in tbl.find_all("th")]
            if not head:
                continue
            if "G" in head or "GP" in head:
                for tr in tbl.find_all("tr"):
                    cells = [td.get_text(strip=True) for td in tr.find_all(["td","th"])]
                    row_text = " ".join(cells).lower()
                    if "total" in row_text or "totals" in row_text:
                        # найдём индекс колонки игр
                        col = None
                        for key in ("G","GP","Games"):
                            if key in head:
                                col = head.index(key)
                                break
                        if col is not None and len(cells) > col:
                            # иногда в Totals идёт несколько подсекций; берём число
                            val = "".join([c for c in cells[col] if c.isdigit()])
                            if val:
                                best_guess = int(val)
        return best_guess
    except Exception:
        return None


def get_coach_games_current() -> int | None:
    # 1) сначала пробуем REST по имени
    n = try_coach_rest_fullname("Paul Maurice")
    if isinstance(n, int) and n > 0:
        return n
    # 2) бэкап — парсим records.nhl.com
    return scrape_maurice_records_page(COACH["records_page"])


def fmt_line(name: str, current: int, target: int, metric_ru: str) -> str:
    left = max(target - current, 0)
    status_emoji = "✅" if left == 0 else ("🔥" if left <= 10 else "🧊")
    # мини-прогресс-бар на 10 делений
    total = max(target, current)
    filled = 10 if left == 0 else max(1, round(10 * current / total))
    bar = "█" * filled + "░" * (10 - filled)
    return f"<b>{escape(name)}</b> — {current} {metric_ru} · осталось {left}\n{bar} {status_emoji}"


def build_message() -> str:
    london = ZoneInfo("Europe/London")
    today = dt.datetime.now(tz=london).strftime("%d %b %Y")
    lines = [f"<b>НХЛ · Утреннее обновление по вехам</b>  — {today}", ""]

    # Игроки/вратари
    for item in TRACK:
        stat = get_career_stat(item["id"])
        metric = item["type"]
        current = int(stat.get(metric, 0))
        metric_ru = {
            "points":"очк.",
            "goals":"гол.",
            "games":"матч.",
            "wins":"побед"
        }[metric]
        lines.append(fmt_line(item["name"], current, item["target"], metric_ru))

    # Тренер
    coach_games = get_coach_games_current()
    if coach_games:
        lines.append(fmt_line(COACH["name"], int(coach_games), COACH["target"], "матч."))  # матчи как метрика
    else:
        lines.append(f"<b>{escape(COACH['name'])}</b> — не удалось получить текущее число матчей (источник недоступен).")

    lines.append("")
    lines.append("ℹ️ Данные: официальные публичные NHL API/страницы. Обновляется раз в сутки.")
    return "\n".join(lines)


def send_telegram(text: str):
    if not (BOT_TOKEN and CHAT_ID):
        print("No TELEGRAM_BOT_TOKEN/CHAT_ID in env", file=sys.stderr)
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    r = SESSION.post(url, json=payload, timeout=25)
    r.raise_for_status()


if __name__ == "__main__":
    try:
        msg = build_message()
        send_telegram(msg)
        print("OK")
    except Exception as e:
        # чтобы Action не падал навсегда — логируем и выходим с ошибкой
        print("ERROR:", repr(e), file=sys.stderr)
        sys.exit(1)
