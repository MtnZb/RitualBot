
import json
from pathlib import Path

PLAYERS_FILE = Path("players.json")
REPORT_FILE = Path("ritual_reports.json")
VICTIMS_FILE = Path("data/victims.json")

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
            victims_list = json.load(f)
            # Преобразуем список в словарь с ключами по ID
            return {victim["id"]: victim for victim in victims_list}
    return {}
