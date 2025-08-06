
import json
from pathlib import Path

PLAYERS_FILE = Path("players.json")
REPORT_FILE = Path("ritual_reports.json")
VICTIMS_FILE = Path("data") /"victims.json"
WEAPONS_FILE = Path("data") / "weapons.json"
IDENTITIES_FILE = Path("data") / "cultist_identities.json"
RITUALS_FILE = Path("data/rituals.json")

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
