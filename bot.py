"""Telegram-бот: раз в сутки сообщает глобальные и американские дни.

Локальные праздники отдельных стран (Россия, Казахстан и другие) не включаются.
Бесплатный Nager.Date используется только для официальных праздников США.
"""

from __future__ import annotations

import json
import os
import time
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

BASE = Path(__file__).resolve().parent
DAYS_PATH = BASE / "days.json"
STATE_PATH = BASE / "state.json"

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
TIMEZONE = os.environ.get("TIMEZONE", "Europe/Riga")
SEND_HOUR = int(os.environ.get("SEND_HOUR", "10"))
SEND_MINUTE = int(os.environ.get("SEND_MINUTE", "0"))
US_HOLIDAYS_API = "https://date.nager.at/api/v3/PublicHolidays/{year}/US"
TG = f"https://api.telegram.org/bot{BOT_TOKEN}"

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS = [
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]
SCOPE_LABELS = {"global": "глобальный", "us": "США"}


def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def now() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE))


def catalog_days(day: datetime) -> list[str]:
    catalog = load_json(DAYS_PATH, {})
    lines = []
    seen = set()
    for item in catalog.get(day.strftime("%m-%d"), []):
        if isinstance(item, str):
            name, scope = item, "global"
        else:
            name = item.get("name")
            scope = item.get("scope", "global")
        if not name or scope not in SCOPE_LABELS or name in seen:
            continue
        seen.add(name)
        lines.append(f"• {name} ({SCOPE_LABELS[scope]})")
    return lines


def us_public_holidays(day: datetime) -> list[str]:
    iso = day.strftime("%Y-%m-%d")
    try:
        response = requests.get(US_HOLIDAYS_API.format(year=day.year), timeout=20)
        if response.status_code == 204 or not response.content:
            return []
        response.raise_for_status()
        holidays = response.json()
    except Exception as exc:
        return [f"• Не удалось получить официальные праздники США ({exc.__class__.__name__})"]
    lines = []
    for item in holidays:
        if item.get("date") != iso:
            continue
        name = item.get("localName") or item.get("name")
        lines.append(f"• {name} (официальный праздник США)")
    return lines


def build_message(day: datetime | None = None) -> str:
    day = day or now()
    title = f"Сегодня {WEEKDAYS[day.weekday()]}, {day.day} {MONTHS[day.month]} {day.year}"
    days = catalog_days(day)
    official = us_public_holidays(day)
    known = {line.split(" (")[0] for line in days}
    official = [line for line in official if line.split(" (")[0] not in known]

    parts = [f"<b>{title}</b>", "", "<b>Глобальные и американские дни</b>"]
    if days or official:
        parts.extend(days)
        parts.extend(official)
    else:
        parts.append("• На эту дату в базе нет глобального или американского дня.")
    parts.append("")
    parts.append("Локальные праздники отдельных стран не показываются. База правится в days.json.")
    return "\n".join(parts)


def send(chat_id: int | str, text: str) -> None:
    response = requests.post(
        f"{TG}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(payload)


def ensure_state() -> dict:
    state = load_json(STATE_PATH, {"subscribers": [], "last_sent": None})
    state.setdefault("subscribers", [])
    state.setdefault("last_sent", None)
    return state


def subscribe(chat_id: int) -> str:
    state = ensure_state()
    if chat_id not in state["subscribers"]:
        state["subscribers"].append(chat_id)
        save_json(STATE_PATH, state)
    return (
        "Подписал этот чат на ежедневную сводку.\n"
        f"Пришлю в {SEND_HOUR:02d}:{SEND_MINUTE:02d} ({TIMEZONE}).\n"
        "Только глобальные и американские дни."
    )


def unsubscribe(chat_id: int) -> str:
    state = ensure_state()
    if chat_id in state["subscribers"]:
        state["subscribers"].remove(chat_id)
        save_json(STATE_PATH, state)
        return "Отписал чат. Ежедневные сообщения больше не придут."
    return "Этот чат и так не был подписан."


def status() -> str:
    state = ensure_state()
    return (
        f"Часовой пояс: {TIMEZONE}\n"
        f"Время рассылки: {SEND_HOUR:02d}:{SEND_MINUTE:02d}\n"
        "Состав: глобальные дни и дни США\n"
        f"Подписчиков: {len(state['subscribers'])}\n"
        f"Последняя рассылка: {state.get('last_sent') or 'ещё не было'}"
    )


def handle(update: dict) -> None:
    message = update.get("message") or {}
    text = (message.get("text") or "").strip()
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None or not text:
        return
    command = text.split()[0].split("@")[0].lower()
    if command in {"/start", "/subscribe"}:
        reply = subscribe(chat_id)
    elif command == "/stop":
        reply = unsubscribe(chat_id)
    elif command == "/status":
        reply = status()
    elif command == "/help":
        reply = (
            "Бот раз в сутки пишет глобальные и американские дни.\n"
            "Праздники, которые отмечают только в одной стране вроде России или Казахстана, не присылает.\n\n"
            "/start — подписаться\n"
            "/stop — отписаться\n"
            "/status — расписание"
        )
    else:
        return
    send(chat_id, reply)


def broadcast_if_due() -> None:
    current = now()
    if (current.hour, current.minute) < (SEND_HOUR, SEND_MINUTE):
        return
    stamp = current.strftime("%Y-%m-%d")
    state = ensure_state()
    if state.get("last_sent") == stamp:
        return
    state["last_sent"] = stamp
    save_json(STATE_PATH, state)
    if not state["subscribers"]:
        return
    text = build_message(current)
    for chat_id in list(state["subscribers"]):
        try:
            send(chat_id, text)
        except Exception:
            traceback.print_exc()
    print(f"sent {stamp}")


def poll() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Задайте BOT_TOKEN от @BotFather")
    offset = 0
    print(f"bot started, daily at {SEND_HOUR:02d}:{SEND_MINUTE:02d} {TIMEZONE}")
    while True:
        try:
            broadcast_if_due()
            response = requests.get(
                f"{TG}/getUpdates",
                params={"timeout": 25, "offset": offset, "allowed_updates": json.dumps(["message"])},
                timeout=35,
            )
            response.raise_for_status()
            for update in response.json().get("result", []):
                offset = update["update_id"] + 1
                handle(update)
        except Exception:
            traceback.print_exc()
            time.sleep(5)


def send_once() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Задайте BOT_TOKEN")
    raw = os.environ.get("CHAT_ID", "").strip()
    if not raw:
        raise SystemExit("Задайте CHAT_ID")
    chat_ids = [part.strip() for part in raw.split(",") if part.strip()]
    text = build_message()
    for chat_id in chat_ids:
        send(chat_id, text)
        print(f"sent to {chat_id}")


if __name__ == "__main__":
    if "--send" in os.sys.argv:
        send_once()
    else:
        poll()
