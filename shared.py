
import json
from pathlib import Path

PLAYERS_FILE = Path("players.json")
REPORT_FILE = Path("ritual_reports.json")
VICTIMS_FILE = Path("data") /"victims.json"
WEAPONS_FILE = Path("data") / "weapons.json"
IDENTITIES_FILE = Path("data") / "cultist_identities.json"
RITUALS_FILE = Path("data/rituals.json")
TEXTS_FILE = Path("data") / "texts.json"

def load_cultists():
    if IDENTITIES_FILE.exists():
        with open(IDENTITIES_FILE, encoding="utf-8") as f:
            return json.load(f)  # ⬅️ Должен возвращать список [{...}, {...}]
    return []

def load_rituals():
    if RITUALS_FILE.exists():
        with open(RITUALS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []

def load_players():
    if PLAYERS_FILE.exists():
        with open(PLAYERS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def load_all_reports():
    if REPORT_FILE.exists():
        with open(REPORT_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def load_victims():
    if VICTIMS_FILE.exists():
        with open(VICTIMS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
            return {int(k): v for k, v in raw.items()}  # 👈 важная правка
    return {}

def load_texts():
    try:
        with open(TEXTS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def user_team(load_players, user_id: int) -> str:
    """Безопасно вернуть 'fbi' / 'cult' / '' """
    players = load_players()
    return players.get(str(user_id), {}).get("team", "") or ""

async def send_info(message, load_players):
    """Показать /инфо в ЛС: разный текст для FBI и Культа из texts.json"""
    if message.chat.type != "private":
        await message.reply("ℹ️ Открой личку с ботом: напиши ему /инфо.")
        return
    team = user_team(load_players, message.from_user.id)
    texts = load_texts()
    if team == "fbi":
        await message.reply(texts.get("info_fbi", "Инструкция ФБР недоступна."), parse_mode="HTML")
    elif team == "cult":
        await message.reply(texts.get("info_cult", "Памятка культа недоступна."), parse_mode="HTML")
    else:
        await message.reply("ℹ️ Сначала выбери сторону у администратора.", parse_mode="HTML")
