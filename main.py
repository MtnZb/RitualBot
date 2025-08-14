import os
import re
import json
import random
import asyncio
import cv2
from urllib.parse import urlparse, parse_qs
from aiogram.dispatcher.handler import CancelHandler, SkipHandler
from pathlib import Path
from aiogram import Bot, Dispatcher, types
from aiogram.dispatcher import FSMContext
from aiogram.utils import executor
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from dotenv import load_dotenv
from datetime import datetime, timedelta
from aiogram.utils.markdown import quote_html
from aiogram.utils.exceptions import CantInitiateConversation
from photo_tools import ultra_obscured_version
from fbi import create_fbi_cases_for_victim



# Подгружаем переменные окружения из .env
load_dotenv()
API_TOKEN = os.getenv("API_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CULT_CHANNEL_ID = int(os.getenv("CULT_CHANNEL_ID"))
CONTROL_CHAT_ID = int(os.getenv("CONTROL_CHAT_ID"))
FBI_CHANNEL_ID = int(os.getenv("FBI_CHANNEL_ID"))

RITUAL_INTERVAL = 150
TZ_OFFSET = timedelta(hours=3)

bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)
async def on_startup(dp):
    # снимаем вебхук, чтобы не было конфликта с polling
    await bot.delete_webhook(drop_pending_updates=True)

INVIS_RE = re.compile(r'[\u200B-\u200D\uFEFF]')  # zero-width & BOM

# 👉 Подключаем хендлеры ФБР
from fbi import register_fbi_handlers
register_fbi_handlers(dp)

ritual_loop_task = None



# Пути к данным
DATA_DIR = Path("data")
EVENT_FILE = Path("current_event.json")
REPORT_FILE = Path("ritual_reports.json")
SCORES_FILE = Path("scores.json")
PENDING_FILE = Path("pending_reports.json")
PLAYERS_FILE = Path("players.json")
WEAPONS_FILE = DATA_DIR / "weapons.json"
IDENTITIES_FILE = Path("data") / "cultist_identities.json"
MAX_REPORTS = 3

# Флаг работы авто-ритуала
auto_ritual_active = False

# Загрузка JSON

def load_json(filename):
    with open(DATA_DIR / filename, encoding="utf-8") as f:
        return json.load(f)

#def load_reports():
 #   if REPORT_FILE.exists():
  #      with open(REPORT_FILE, encoding="utf-8") as f:
   #         return json.load(f)
    #return {"victim_id": None, "reports": []}

#def save_reports(data):
 #   with open(REPORT_FILE, "w", encoding="utf-8") as f:
  #      json.dump(data, f, ensure_ascii=False, indent=2)


WEAPON_ALLOWED = re.compile(r"^[A-Z0-9\-]{2,32}$")

def is_valid_weapon_id(wid: str) -> bool:
    return bool(WEAPON_ALLOWED.match(wid))

def cult_onboarding_message() -> str:
    return (
        "🧭 <b>Как участвовать в ритуале</b>\n"
        "1) Дождись задания в канале культа (жертва, ритуал, место, оружие).\n"
        "2) Отправь в ЛС ID оружия: <code>weapon:QW34</code>\n"
        "   • кириллица не подойдёт: <code>Т≠T</code>, <code>Х≠X</code>\n"
        "   • можно просто прислать фото QR — я распознаю сам.\n"
        "3) Пришли <b>одно</b> фото ритуала в ЛС — оно уйдёт на проверку.\n"
        "⚠️ В группах/канале фото не принимаю — только в личке."
    )


def normalize_weapon_id(text):
    mapping = {
        "А":"A","В":"B","С":"C","Е":"E","Н":"H","К":"K","М":"M","О":"O","Р":"P","Т":"T","Х":"X","У":"Y",
        "Ё":"E","Й":"I","І":"I","Ї":"I"
    }
    if not text:
        return ""
    # приводим NBSP к обычному пробелу, убираем zero-width
    text = str(text).replace("\xa0", " ")
    text = INVIS_RE.sub("", text)
    # обрезаем края и в верхний регистр
    text = text.strip().upper()
    # убираем ВСЕ пробелы внутри (дефисы оставляем)
    text = text.replace(" ", "")
    # кириллицу -> латиница
    return "".join(mapping.get(ch, ch) for ch in text)

def safe_get_weapon_id(text):
    if not text or "weapon:" not in text:
        return None  # Вместо ошибки возвращаем None

    weapon_id = text.split("weapon:", 1)[-1].strip()
    if len(weapon_id) < 2:  # Проверяем минимальную длину
        return None

    return weapon_id
    
def extract_weapon_from_qr(image_path: str):
    """
    Возвращает weapon_id из QR, если на фото есть:
      - https://t.me/<bot>?start=weapon-XXXX
      - tg://resolve?domain=<bot>&start=weapon-XXXX
      - просто текст 'weapon-XXXX' или 'weapon:XXXX'
    Иначе None.
    """
    try:
        img = cv2.imread(str(image_path))
        if img is None:
            return None
        det = cv2.QRCodeDetector()
        data, _, _ = det.detectAndDecode(img)
        if not data:
            return None

        txt = data.strip()
        # Вариант 1: полная ссылка
        if txt.startswith("http://") or txt.startswith("https://") or txt.startswith("tg://"):
            try:
                u = urlparse(txt)
                qs = parse_qs(u.query)
                start_vals = qs.get("start") or []
                if start_vals:
                    payload = start_vals[0]
                    # ожидаем weapon-XXXX
                    if payload.lower().startswith("weapon-"):
                        code = payload.split("-", 1)[-1]
                        return normalize_weapon_id(code)
            except Exception:
                pass

        # Вариант 2: голый payload
        low = txt.lower()
        if low.startswith("weapon-"):
            return normalize_weapon_id(txt.split("-", 1)[-1])
        if low.startswith("weapon:"):
            return normalize_weapon_id(txt.split(":", 1)[-1])

        return None
    except Exception:
        return None


def load_all_reports():
    if REPORT_FILE.exists():
        with open(REPORT_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}
    
def load_pending_reports():
    if not os.path.exists("pending_reports.json"):
        return []

    with open("pending_reports.json", encoding="utf-8") as f:
        try:
            data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception as e:
            print(f"[DEBUG] ⚠️ Ошибка чтения pending_reports.json: {e}")
            return []

def save_all_reports(data):
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def add_report_entry(victim_id: str, victim_data: dict, report: dict):
    all_reports = load_all_reports()
    victim_key = str(victim_id)

    if victim_key not in all_reports:
        all_reports[victim_key] = {
            "victim_name": victim_data.get("victim_name"),
            "ritual": victim_data.get("ritual"),
            "place": victim_data.get("place"),
            "reports": []
        }
    # 👉 новый блок: финальная защита
    if len(all_reports[victim_key]["reports"]) >= MAX_REPORTS:
        print(f"[add_report_entry] ⚠️ Лимит отчётов для жертвы {victim_key} достигнут")
        return False
        
    all_reports[victim_key]["reports"].append(report)
    save_all_reports(all_reports)
    return True

def load_scores():
    try:
        with open(SCORES_FILE, encoding="utf-8") as f:
            scores = json.load(f)
            print(f"✅ Загружены очки: {scores}")
            return scores
    except Exception as e:
        print(f"⚠️ Ошибка при загрузке scores.json: {e}")
        return {}

def save_scores(data):
    with open(SCORES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def save_pending(data):
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        
def load_players():
    if PLAYERS_FILE.exists():
        with open(PLAYERS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_players(players):
    with open(PLAYERS_FILE, "w", encoding="utf-8") as f:
        json.dump(players, f, ensure_ascii=False, indent=2)

def already_in_team(user_id, team=None):
    players = load_players()
    current = players.get(str(user_id))
    if not isinstance(current, dict):
        return None
    if team is None:
        return current
    return current if current.get("team") == team else None

def assign_team(user_id, team):
    players = load_players()
    entry = players.get(str(user_id), {})
    if not isinstance(entry, dict):
        entry = {"team": str(entry)} if entry else {}
    entry["team"] = team
    players[str(user_id)] = entry
    save_players(players)

# ==== ОСНОВНАЯ ЛОГИКА ====

async def run_ritual():
    victims = load_json("victims.json")
    rituals = load_json("rituals.json")
    weapons = load_json("weapons.json")
    places = load_json("places.json")

    auto_ritual_active = True
    
    try:
        with open("used_victims.json", encoding="utf-8") as f:
            content = f.read().strip()
            used_ids = json.loads(content) if content else []
    except FileNotFoundError:
        used_ids = []

    available_ids = [vid for vid in victims if str(vid) not in used_ids]
    if not available_ids:
        await bot.send_message(CULT_CHANNEL_ID, "Все жертвы использованы.")
        return

    try:
        if EVENT_FILE.exists():
            with open(EVENT_FILE, encoding="utf-8") as f:
                prev_event = json.load(f)
            prev_victim_id = prev_event.get("victim_id")
            if prev_victim_id is not None:
                # создаём дела для ФБР по всем принятым отчётам R1..R3, постим в канал ФБР
                created = await create_fbi_cases_for_victim(prev_victim_id, bot, FBI_CHANNEL_ID)
                if created:
                    print(f"[FBI] Создано дел по жертве {prev_victim_id}: {created}")
    except Exception as e:
        print(f"[FBI] Ошибка финализации предыдущего ивента: {e}")

    victim_id = random.choice(available_ids)
    victim = victims[victim_id]

    # Добавляем именно ключ (victim_id), а не victim["id"]
    used_ids.append(victim_id)
    with open("used_victims.json", "w", encoding="utf-8") as f:
        json.dump(used_ids, f, ensure_ascii=False, indent=2)

    ritual = random.choice(rituals)
    weapon_entry = random.choice(weapons)
    weapon = weapon_entry["name"]
    place = random.choice(places)

    event = {
        "victim_id": victim_id,
        "victim_name": victim["name"],
        "victim_description": victim["description"],
        "victim_photo": victim["photo"],
        "ritual": ritual,
        "weapon": weapon,
        "place": place,
        "assigned_weapons": []
    }

    with open(EVENT_FILE, "w", encoding="utf-8") as f:
        json.dump(event, f, ensure_ascii=False, indent=2)

    text = (
        f"\U0001F52E <b>НОВЫЙ РИТУАЛ</b>\n"
        f"Жертва: {event['victim_name']} ({event['victim_description']})\n"
        f"Орудие: {event['weapon']}\n"
        f"Ритуал: {event['ritual']}\n"
        f"Место: {event['place']}"
    )

    with open(event['victim_photo'], "rb") as photo:
        await bot.send_photo(
            chat_id=CULT_CHANNEL_ID,
            photo=photo,
            caption=text,
            parse_mode="HTML"
        )

# ==== РУЧНЫЕ КОМАНДЫ ====

@dp.message_handler(commands=["ритуал"])
async def start_ritual_loop(message: types.Message):
    global ritual_loop_task

    if message.from_user.id != ADMIN_ID:
        return  # Только админ может

    if ritual_loop_task and not ritual_loop_task.done():
        await message.reply("🔄 Цикл ритуалов уже запущен.")
        return

    async def auto_ritual_loop():
        while True:
            await run_ritual()
            await asyncio.sleep(RITUAL_INTERVAL)  # 2,5 минут

    ritual_loop_task = asyncio.create_task(auto_ritual_loop())
    pretty = f"{RITUAL_INTERVAL//60} мин" if RITUAL_INTERVAL % 60 == 0 else f"{RITUAL_INTERVAL} сек"
    await message.reply(f"🔮 Цикл ритуалов запущен. Каждые {pretty} будет новая жертва.")

@dp.message_handler(commands=["стоп"])
async def stop_ritual_loop(message: types.Message):
    global ritual_loop_task

    if message.from_user.id != ADMIN_ID:
        return  # Только админ может

    if not ritual_loop_task or ritual_loop_task.done():
        await message.reply("⛔ Цикл ритуалов уже остановлен.")
        return

    ritual_loop_task.cancel()
    ritual_loop_task = None
    await message.reply("🛑 Цикл ритуалов остановлен.")


@dp.message_handler(commands=["очки"])
async def show_scores(message: types.Message):
    scores = load_scores()
    players = load_players()
    user_id = str(message.from_user.id)

    # Если пусто — быстрое сообщение и выход
    if not scores:
        if message.chat.id == FBI_CHANNEL_ID:
            await message.reply("Пока ни у кого из ФБР нет очков.")
        else:
            await message.reply("Пока никто не пролил кровь.")
        return

    # === Режим ФБР-канала ===
    if message.chat.id == FBI_CHANNEL_ID:
        # Мои очки
        my_score = scores.get(user_id, 0)

        # Соберём список только агентов ФБР
        fbi_ids = [
            uid for uid, pdata in players.items()
            if isinstance(pdata, dict) and pdata.get("team") == "fbi"
        ]
        fbi_scores = [(uid, scores.get(uid, 0)) for uid in fbi_ids]

        if not fbi_scores:
            await message.reply(
                f"🕵️ Твои очки: {my_score}\n"
                f"Пока нет рейтинга среди ФБР.", parse_mode="HTML"
            )
            return

        # Сортируем по очкам по убыванию
        fbi_scores.sort(key=lambda x: x[1], reverse=True)
        top_10 = fbi_scores[:10]

        # Рисуем топ
        lines = [
            f"🕵️ <b>Твои очки:</b> {my_score}",
            "🏆 <b>Топ-10 ФБР</b>:"
        ]
        for i, (uid, sc) in enumerate(top_10, 1):
            mention = f"<a href='tg://user?id={uid}'>Агент</a>"
            lines.append(f"{i}. {mention}: {sc}")

        await message.reply("\n".join(lines), parse_mode="HTML")
        return

    # === Обычный режим (например, канал культа) — как было ===
    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_10 = sorted_scores[:10]

    text = "🏆 <b>Топ-10 культистов</b>:\n"
    for i, (uid, score) in enumerate(top_10, 1):
        mention = f"<a href='tg://user?id={uid}'>Культист</a>"
        text += f"{i}. {mention}: {score} очков\n"

    await message.reply(text, parse_mode="HTML")


# ==== ФОНОВАЯ ЗАДАЧА ====

async def auto_ritual_loop():
    global auto_ritual_active
    while True:
        if auto_ritual_active:
            try:
                await run_ritual()
            except Exception as e:
                print(f"⚠️ Ошибка в авто-ритуале: {e}")
        await asyncio.sleep(15)
        
# ==== ПРИЁМ QR С ОРУЖИЕМ (фото в ЛС) ====
@dp.message_handler(lambda m: m.chat.type == "private", content_types=types.ContentType.PHOTO)
async def handle_weapon_qr_photo(message: types.Message):
    # Скачиваем фото во временный файл
    photo = message.photo[-1]
    os.makedirs("tmp", exist_ok=True)
    tmp_path = Path("tmp") / f"qr_{message.from_user.id}_{photo.file_unique_id}.jpg"
    await photo.download(destination_file=tmp_path)

    wid = extract_weapon_from_qr(str(tmp_path))
    try:
        tmp_path.unlink(missing_ok=True)
    except Exception:
        pass

    if not wid:
        # Дай пройти следующему хендлеру (handle_report)
        raise SkipHandler()

    # Нашли weapon_id — валидируем и проводим через общий пайплайн
    if not is_valid_weapon_id(wid):
        await message.reply("❌ Некорректный ID в QR. Разрешены A–Z, 0–9 и «-», длина 2–32.")
        # Останавливаем дальнейшие хендлеры, чтобы это фото не ушло как отчёт
        raise CancelHandler()

    await process_weapon_submission(message, wid)
    # Останавливаем дальнейшие хендлеры (иначе это фото попадёт в модерацию как отчёт)
    raise CancelHandler()
# ==== ПРИЁМ ОТЧЁТОВ ====

@dp.message_handler(content_types=types.ContentType.PHOTO)
async def handle_report(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or f"id:{user_id}"

    # ✅ Теперь отчёты принимаем только в личке
    if message.chat.type != "private":
        # мягко подскажем, что делать правильно
        await message.reply("⛔ Фото-отчёт присылай **мне в личку**. В канал попадает только принятый отчёт.")
        return
    if not EVENT_FILE.exists():
        await message.reply("❌ Сейчас нет активного ритуала.")
        print(f"[DEBUG] ❌ Файл {EVENT_FILE} не найден.")
        return

    try:
        with open(EVENT_FILE, encoding="utf-8") as f:
            event = json.load(f)
    except Exception as e:
        await message.reply("⚠️ Ошибка при чтении задания.")
        print(f"[DEBUG] ⚠️ Ошибка чтения EVENT_FILE: {e}")
        return



    
    # Проверка: присылал ли пользователь weapon_id
    assigned_weapons = event.get("assigned_weapons", [])
    user_weapon = next((w for w in assigned_weapons if w["user_id"] == user_id), None)

    if not user_weapon:
        try:
            me = await bot.get_me()
            await message.reply(
                "⛔ Сначала укажи ID оружия.\n\n"
                "1) Отправь в ЛС сообщение: <code>weapon:QW34</code>\n"
                "   • кириллица не подойдёт: <code>Т≠T</code>, <code>Х≠X</code>\n"
                "2) Или пришли фото QR с кодом — я распознаю его сам.\n\n"
                f"👉 Если диалог закрыт: <a href='https://t.me/{me.username}'>открыть ЛС со мной</a>",
                parse_mode="HTML"
            )
        except Exception:
            await message.reply(
                "⛔ Сначала укажи ID оружия: <code>weapon:QW34</code>\n"
                "Или пришли фото QR — я вытащу код сам.",
                parse_mode="HTML"
            )
        print(f"[DEBUG] ⛔ Отчёт отклонён — не указан weapon_id от {username}")
        return

    pending = load_pending_reports()

    if any(r.get("user_id") == user_id and r.get("victim_id") == event["victim_id"] for r in pending):
        await message.reply(f"⛔ @{username}, ты уже отправлял отчёт. Ожидается проверка.")
        print(f"[DEBUG] ⛔ Повторный отчёт от {username}")
        return

    try:
        with open(Path("data") / "weapons.json", encoding="utf-8") as f:
            weapons = json.load(f)
        matched = next((w for w in weapons if user_weapon["weapon_id"] in w.get("ids", [])), None)
        weapon_name = matched.get("name") if matched else None

        if not matched:
            print(f"[DEBUG] ❌ Оружие с ID {user_weapon['weapon_id']} не найдено в списке weapons.")
        else:
            print(f"[DEBUG] ✅ Найдено оружие: {weapon_name}")
    except Exception as e:
        print(f"[DEBUG] ⚠️ Не удалось загрузить weapon_name: {e}")

    all_reports = load_all_reports()
    victim_key = str(event["victim_id"])
    # 👉 новый блок: лимит уже принятых отчётов
    if victim_key in all_reports and len(all_reports[victim_key]["reports"]) >= MAX_REPORTS:
        await message.reply("⛔ Лимит отчётов по этой жертве достигнут. Дело закрыто.")
        print(f"[DEBUG] ⛔ Лимит отчётов для {victim_key} достигнут")
        return
        
    if victim_key in all_reports:
        if any(r.get("user_id") == user_id for r in all_reports[victim_key]["reports"]):
            await message.reply("⛔ Ты уже присылал отчёт по этому ритуалу.")
            print(f"[DEBUG] ⛔ {username} уже сдавал отчёт по жертве {victim_key}")
            return

    photo = message.photo[-1]
    filename = f"ritual_{event['victim_id']}_{user_id}.jpg"
    os.makedirs("reports", exist_ok=True)
    destination = Path("reports") / filename
    await photo.download(destination_file=destination)
    print(f"[DEBUG] Фото сохранено: {destination}")

    await message.reply("📸 Отчёт отправлен на проверку. Ожидай подтверждения.")
    

    caption = (
        f"🧾 Отчёт от @{username}\n"
        f"Жертва: {event.get('victim_name')}\n"
        f"Ритуал: {event['ritual']}\n"
        f"Орудие: {user_weapon['weapon_id']}\n"
        f"Место: {event['place']}"
    )

    try:
        with open(destination, "rb") as f:
            # Отправляем фото без клавиатуры, чтобы получить message_id
            sent_message = await bot.send_photo(CONTROL_CHAT_ID, photo=f, caption=caption)

        # Генерируем клавиатуру уже с корректным message_id
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("✅ Принять", callback_data=f"accept:{sent_message.message_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{sent_message.message_id}")
        )

        # Обновляем сообщение, прикрепляем клавиатуру
        await bot.edit_message_reply_markup(
            chat_id=CONTROL_CHAT_ID,
            message_id=sent_message.message_id,
            reply_markup=kb
        )

        # Сохраняем отчёт в pending
        pending.append({
            "user_id": user_id,
            "username": username,
            "weapon_id": user_weapon["weapon_id"],
            "weapon": weapon_name,  # Название оружия
            "victim_id": event["victim_id"],
            "victim_name": event.get("victim_name"),
            "ritual": event.get("ritual"),
            "place": event.get("place"),
            "photo_file": photo.file_id,
            "message_id": sent_message.message_id
        })

        with open("pending_reports.json", "w", encoding="utf-8") as f:
            json.dump(pending, f, ensure_ascii=False, indent=2)

    except Exception as e:
        print(f"[ERROR] ❌ Ошибка отправки фото на проверку: {e}")
        await message.reply("⚠️ Ошибка при отправке на проверку.")
        return


# ==== ОБРАБОТКА КНОПОК ====


@dp.callback_query_handler(lambda c: c.data.startswith("accept") or c.data.startswith("reject"))
async def process_callback(call: CallbackQuery):
    action, msg_id = call.data.split(":")
    pending = load_pending_reports()
    msg_id = call.data.split(":")[1]
    entry = next((r for r in pending if str(r.get("message_id", "")) == msg_id), None)



    if not entry:
        await call.answer("⛔ Отчёт уже обработан.", show_alert=True)
        return

    user_id = entry["user_id"]
    username = entry["username"]

    if action == "accept":
        # данные
        ritual = entry.get("ritual")
        place = entry.get("place")
        weapon_name = entry.get("weapon")
        weapon_id = entry.get("weapon_id")
        victim_id = entry.get("victim_id")

        # лимит перед записью
        all_reports = load_all_reports()
        vk = str(victim_id)
        if vk in all_reports and len(all_reports[vk]["reports"]) >= MAX_REPORTS:
            await call.answer("⛔ Лимит отчётов по этой жертве уже достигнут.", show_alert=True)
            return

        # identity_id, даже если в ФБР
        players = load_players()
        player = players.get(str(user_id), {})
        identity_id = player.get("identity_id")

        # готовим отчёт
        photo_file_id = call.message.photo[-1].file_id if call.message.photo else None
        timestamp = (datetime.utcnow() + TZ_OFFSET).isoformat()
        report_entry = {
            "user_id": user_id,
            "identity_id": identity_id,
            "weapon_id": weapon_id,
            "weapon_name": weapon_name,
            "photo_file_id": photo_file_id,
            "timestamp": timestamp
        }

        # запись в базу
        ok = add_report_entry(victim_id, {
            "victim_name": entry.get("victim_name"),
            "ritual": ritual,
            "place": place
        }, report_entry)

        if not ok:
            await call.answer("⛔ Лимит отчётов по этой жертве уже достигнут.", show_alert=True)
            return

        # успех -> удаляем из pending
        if entry in pending:
            pending.remove(entry)
            save_pending(pending)
        
        # начисляем очки
        scores = load_scores()
        scores[str(user_id)] = scores.get(str(user_id), 0) + 1
        save_scores(scores)

        # обновляем caption
        old_caption = call.message.caption or ""
        new_caption = old_caption + f"\n✅ Очки начислены ({scores[str(user_id)]})"
        await call.message.edit_caption(new_caption)
        await bot.send_message(CULT_CHANNEL_ID, f"✅ @{username}, отчёт принят. У него {scores[str(user_id)]} очков.")

        # ✅ Дублируем подтверждение в личку автору отчёта
        try:
            await bot.send_message(
                user_id,
                (
                    "✅ Твой отчёт принят!\n"
                    f"Жертва: {entry.get('victim_name')}\n"
                    f"Ритуал: {ritual}\n"
                    f"Орудие: {weapon_name or weapon_id}\n"
                    f"Место: {place}\n\n"
                    "🏅 Начислено: +1 очко\n"
                    f"💰 Твой счёт: {scores[str(user_id)]}\n\n"
                    "Следи за каналом культа — новое задание уже близко."
                )
            )
        except Exception as e:
            print(f"[DEBUG] ❌ Не удалось отправить подтверждение в личку: {e}")
        # ✅ Публикуем принятую фотографию в канал культа
        try:
            # берём то же фото, которое пришло на модерацию
            final_caption = (
                f"🧾 Принятый отчёт от @{username}\n"
                f"Жертва: {entry.get('victim_name')}\n"
                f"Ритуал: {ritual}\n"
                f"Орудие: {weapon_name or weapon_id}\n"
                f"Место: {place}"
            )
            # в контрол-чате у нас есть объект с фото; безопаснее переслать по file_id
            if call.message.photo:
                file_id = call.message.photo[-1].file_id
                await bot.send_photo(CULT_CHANNEL_ID, photo=file_id, caption=final_caption)
            else:
                # fallback, если вдруг нет photo в самом сообщении (редкий случай)
                await bot.send_message(CULT_CHANNEL_ID, final_caption)
        except Exception as e:
            print(f"[DEBUG] ⚠️ Не удалось опубликовать фото в канал культа: {e}")

    elif action == "reject":
        if entry in pending:
            pending.remove(entry)
            save_pending(pending)
            
        try:
            new_caption = (call.message.caption or "") + "\n❌ Отчёт отклонён"
            await call.message.edit_caption(new_caption)
        except Exception as e:
            print(f"[DEBUG] ❌ Не удалось обновить caption: {e}")

        # Уведомим игрока
        try:
            await bot.send_message(user_id, "❌ Ритуал не принят. Дерево отвергло твоё подношение.")
        except Exception as e:
            print(f"[DEBUG] ❌ Не удалось отправить личное сообщение: {e}")

        await call.answer("❌ Отчёт отклонён.")

        


#Вступление в команду
# Исправленная логика вступления в ФБР
@dp.callback_query_handler(lambda c: c.data in ["join_cult", "join_fbi"])
async def handle_team_selection(call: types.CallbackQuery):
    user_id = call.from_user.id
    username = call.from_user.username or f"id:{user_id}"
    players = load_players()
    scores = load_scores()

    # Проверяем текущее состояние игрока
    current_player = players.get(str(user_id))

    if call.data == "join_cult":
        # Если игрок уже в ФБР — отклонить
        if current_player and isinstance(current_player, dict) and current_player.get("team") == "fbi":
            await call.message.edit_text("❌ Ты уже в ФБР. Культ отвергает тебя.")
            return

        # Если уже в культе - проверяем участие в канале
        if current_player and isinstance(current_player, dict) and current_player.get("team") == "cult":
            try:
                member = await bot.get_chat_member(CULT_CHANNEL_ID, user_id)
                if member.status not in ("left", "kicked"):
                    await call.message.edit_text("⛔ Ты уже в культе.")
                    return
                else:
                    # Вышел — разрешим повторное вступление
                    pass
            except Exception as e:
                await bot.send_message(ADMIN_ID, f"⚠️ Ошибка проверки участника: {e}")
                await call.message.edit_text("Произошла ошибка при проверке. Обратись к администратору.")
                return

        # Назначаем личность культисту
        try:
            with open(IDENTITIES_FILE, encoding="utf-8") as f:
                identities = json.load(f)
        except Exception as e:
            await bot.send_message(ADMIN_ID, f"⚠️ Ошибка загрузки cultist_identities.json: {e}")
            await call.message.edit_text("❌ Что-то пошло не так. Обряд прервали.")
            return

        identity = random.choice(identities)

        players[str(user_id)] = {
            "team": "cult",
            "identity_id": identity["id"]
        }
        save_players(players)

        # Генерируем инвайт
        try:
            invite = await bot.create_chat_invite_link(chat_id=CULT_CHANNEL_ID, member_limit=1, creates_join_request=False)
            invite_link = invite.invite_link
        except Exception as e:
            await bot.send_message(ADMIN_ID, f"⚠️ Ошибка создания инвайта: {e}")
            invite_link = "Ошибка создания ссылки"

        text = (
            f"🔮 <b>Ты теперь часть нас</b>\n"
            f"<b>Твоя личность:</b> {identity['name']} {identity['mask_symbol']}\n"
            f"<i>{identity['description']}</i>\n\n"
            f"Запомни: ты — маска, а не человек. Носи её. И не говори вслух своё имя.\n\n"
            f"➡️ Вступи в культ: {invite_link}"
        )
        await call.message.edit_text(text, parse_mode="HTML")
        # ➕ Отправляем новичку пошаговую инструкцию в ЛС
        try:
            await bot.send_message(
                user_id,
                cult_onboarding_message(),
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        except CantInitiateConversation:
            # если ЛС закрыт — дадим ссылку-напоминание прямо там, где он нажал кнопку
            try:
                me = await bot.get_me()
                await call.message.answer(
                    "ℹ️ Открой личку со мной, чтобы получить инструкции: "
                    f"<a href='https://t.me/{me.username}'>перейти в ЛС</a>",
                    parse_mode="HTML", disable_web_page_preview=True
                )
            except Exception:
                pass

    else:  # join_fbi
        # ИСПРАВЛЕННАЯ ЛОГИКА ДЛЯ ФБР

        # Проверяем, состоит ли уже в ФБР
        if current_player and isinstance(current_player, dict) and current_player.get("team") == "fbi":
            await call.message.edit_text("⛔ Ты уже в ФБР.")
            return

        # Если игрок был в культе - применяем штраф
        if current_player and isinstance(current_player, dict) and current_player.get("team") == "cult":
            print(f"[DEBUG] Пользователь {username} был в культе, применяем штраф")

            # Минус 10 очков за предательство
            current_score = scores.get(str(user_id), 0)
            new_score = current_score - 10
            scores[str(user_id)] = new_score
            save_scores(scores)

            print(f"[DEBUG] Счет изменен: {current_score} -> {new_score}")

            # 50% шанс, что культ узнает
            if random.random() < 0.5:
                try:
                    await bot.send_message(
                        CULT_CHANNEL_ID,
                        f"😈 @{username} пал ниц и попытался сбежать в ФБР. Культ помнит предателей..."
                    )
                    print(f"[DEBUG] Сообщение о предательстве отправлено в культ")
                except Exception as e:
                    print(f"[DEBUG] Ошибка отправки в культ: {e}")
                    await bot.send_message(ADMIN_ID, f"⚠️ Ошибка отправки в канал культа: {e}")

            # Отправляем сообщение предателю
            try:
                await bot.send_message(
                    user_id,
                    f"❌ Ты был в культе, но ФБР приняло тебя.\n"
                    f"🔻 Штраф за предательство: -10 очков\n"
                    f"💰 Твой счет теперь: {new_score}"
                )
                print(f"[DEBUG] Сообщение о штрафе отправлено пользователю {user_id}")
            except Exception as e:
                print(f"[DEBUG] Ошибка отправки личного сообщения: {e}")
                # Если не удалось отправить в личку, показываем в интерфейсе
                penalty_text = f"❌ Штраф за предательство: -10 очков. Счет: {new_score}\n\n"
            else:
                penalty_text = ""
        else:
            penalty_text = ""

        # Переводим в команду ФБР
        curr = players.get(str(user_id), {})
        players[str(user_id)] = {**curr, "team": "fbi"}
        save_players(players)
        

        # Создаем инвайт в ФБР
        try:
            invite = await bot.create_chat_invite_link(chat_id=FBI_CHANNEL_ID, member_limit=1, creates_join_request=False)
            invite_link = invite.invite_link
        except Exception as e:
            await bot.send_message(ADMIN_ID, f"⚠️ Ошибка создания инвайта ФБР: {e}")
            invite_link = "Ошибка создания ссылки"

        response_text = (
            f"{penalty_text}"
            f"🕵️ Добро пожаловать в ФБР. Улики не ждут.\n"
            f"➡️ {invite_link}"
        )

        await call.message.edit_text(response_text)
        print(f"[DEBUG] Обработка вступления в ФБР завершена для {username}")

# Дополнительная команда для отладки очков
@dp.message_handler(commands=["debug_score"])
async def debug_score(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    user_id = message.from_user.id
    scores = load_scores()
    players = load_players()

    debug_info = f"""
🔍 Отладочная информация:
User ID: {user_id}
Текущие очки: {scores.get(str(user_id), 0)}
Команда: {players.get(str(user_id), "Не назначена")}

Все очки: {scores}
Все игроки: {players}
    """

    await message.reply(debug_info)

# Команда для принудительного изменения очков (для отладки)
@dp.message_handler(commands=["set_score"])
async def set_score(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    try:
        parts = message.text.split()
        if len(parts) < 3:
            await message.reply("Использование: /set_score <user_id> <очки>")
            return

        target_user_id = parts[1]
        new_score = int(parts[2])

        scores = load_scores()
        scores[target_user_id] = new_score
        save_scores(scores)

        await message.reply(f"✅ Установлены очки {new_score} для пользователя {target_user_id}")

    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")

# Функция для безопасной отправки личных сообщений
async def safe_send_private_message(user_id, text):
    """Безопасная отправка личного сообщения с обработкой ошибок"""
    try:
        await bot.send_message(user_id, text)
        return True
    except Exception as e:
        print(f"[DEBUG] Не удалось отправить сообщение пользователю {user_id}: {e}")
        return False


#Приветствие для новых участников
@dp.chat_member_handler()
async def on_chat_member_update(update: types.ChatMemberUpdated):
    print("📥 Событие chat_member пришло!")
    if update.chat.id != CULT_CHANNEL_ID:
        return

    new_status = update.new_chat_member.status
    if new_status != "member":
        return  # Только вступившие

    user_id = update.new_chat_member.user.id
    players = load_players()
    player = players.get(str(user_id))

    if not player or player.get("team") != "cult":
        return

    identity_id = player.get("identity_id")
    if not identity_id:
        return

    try:
        with open(IDENTITIES_FILE, encoding="utf-8") as f:
            identities = json.load(f)
    except Exception as e:
        await bot.send_message(ADMIN_ID, f"⚠️ Не удалось загрузить cultist_identities.json: {e}")
        return

    identity = next((i for i in identities if i["id"] == identity_id), None)
    if not identity:
        return

    mention = update.new_chat_member.user.get_mention(as_html=True)
    await bot.send_message(
        CULT_CHANNEL_ID,
        f"🌒 Новое лицо под маской вступило в культ.\n"
        f"{mention} теперь {identity['name']} {identity['mask_symbol']}\n"
        f"<i>{identity['description']}</i>\n"
        f"ℹ️ Инструкции отправлены ему в личные сообщения.",
        parse_mode="HTML"
    )

#кнопка старт для игроков
@dp.message_handler(commands=["start"])
async def start_handler(message: types.Message, state: FSMContext):
    args = message.get_args() or ""

    # 🔒 Пусть fbi-роутер обрабатывает /start fbi_*
    if args.lower().startswith("fbi_"):
        return
    # 👉 СНАЧАЛА: приём weapon через deeplink
    if args.lower().startswith("weapon-"):
        payload = args.split("-", 1)[-1]
        return await process_weapon_submission(message, payload)


    # Обычное поведение
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🔮 Вступить в Культ", callback_data="join_cult"),
        InlineKeyboardButton("🕵️ Присоединиться к ФБР", callback_data="join_fbi")
    )
    await message.answer("Выбери свою сторону:", reply_markup=kb)
    
async def process_weapon_submission(message: types.Message, weapon_payload: str):
    # 1) Только личка
    if message.chat.type != "private":
        try:
            me = await bot.get_me()
            # нормализуем, чтобы сразу дать удобную ссылку
            wid_hint = normalize_weapon_id(weapon_payload or "XXXX")
            deeplink = f"https://t.me/{me.username}?start=weapon-{wid_hint or 'XXXX'}"
            await message.reply(
                f"⛔ Отправь ID оружия мне в личку.\n"
                f"👉 <a href='{deeplink}'>Открыть диалог</a>",
                parse_mode="HTML"
            )
        except Exception:
            await message.reply("⛔ Отправь ID оружия мне в личку.")
        return

    user_id = message.from_user.id
    username = message.from_user.username or f"id:{user_id}"

    # 2) Нормализация
    weapon_id = normalize_weapon_id(weapon_payload or "")
    if len(weapon_id) < 2:
        await message.reply("❌ Неверный/слишком короткий ID. Пример: <code>weapon:ABC123</code>", parse_mode="HTML")
        return

    # 3) Ивент
    if not EVENT_FILE.exists():
        await message.reply("❌ Сейчас нет активного ритуала.")
        return
    try:
        with open(EVENT_FILE, encoding="utf-8") as f:
            event = json.load(f)
    except Exception:
        await message.reply("⚠️ Не удалось прочитать активный ритуал.")
        return

    # 4) Оружие из базы
    try:
        with open(WEAPONS_FILE, encoding="utf-8") as f:
            weapons = json.load(f)
    except Exception:
        await message.reply("⚠️ Не удалось загрузить weapons.json.")
        return

    weapon_entry = next((w for w in weapons if w.get("name") == event.get("weapon")), None)
    if not weapon_entry:
        await message.reply("❌ Оружие задания не найдено в базе.")
        return

    ids_list = weapon_entry.get("ids", [])
    ids_norm = {normalize_weapon_id(x) for x in ids_list if isinstance(x, str)}
    if weapon_id not in ids_norm:
        await message.reply(
            "❌ Неверный ID — он не относится к текущему оружию.\n"
            f"🔎 Сейчас в задании: <b>{event.get('weapon')}</b>.\n"
            "Проверь QR/раскладку (например, <code>Т≠T</code>, <code>Х≠X</code>) и попробуй ещё раз.",
            parse_mode="HTML"
        )
        return

    # 5) Запрет повторов по отчётам (если уже сдавал по этой жертве)
    reports = load_all_reports()
    victim_key = str(event.get("victim_id"))
    if victim_key in reports:
        if any(r.get("user_id") == user_id for r in reports[victim_key].get("reports", [])):
            await message.reply("⛔ Ты уже сдавал отчёт по этому ритуалу.")
            return

    # 6) Сохраняем weapon за пользователем в текущем ивенте
    event.setdefault("assigned_weapons", [])
    event["assigned_weapons"] = [w for w in event["assigned_weapons"] if w.get("user_id") != user_id]
    event["assigned_weapons"].append({"user_id": user_id, "weapon_id": weapon_id})

    with open(EVENT_FILE, "w", encoding="utf-8") as f:
        json.dump(event, f, ensure_ascii=False, indent=2)

    await message.reply(
        f"🔐 Твой ID оружия (<code>{weapon_id}</code>) принят.\n"
        f"📸 Теперь пришли фото ритуала **одним сообщением** — оно уйдёт на проверку.",
        parse_mode="HTML"
    )


## ==== ОБРАБОТКА ОРУЖИЯ ====   
@dp.message_handler(lambda m: m.text and re.match(r'^\s*weapon\s*:\s*', m.text, re.I))
async def handle_weapon_qr(message: types.Message):
    m = re.match(r'^\s*weapon\s*:\s*(.+)$', message.text, re.I | re.S)
    weapon_id_raw = m.group(1) if m else ""
    await process_weapon_submission(message, weapon_id_raw)

# ==== ЗАПУСК ====

if __name__ == "__main__":
    print("Бот запущен.")
    loop = asyncio.get_event_loop()
    loop.create_task(auto_ritual_loop())
    executor.start_polling(
        dp,
        on_startup=on_startup,          # ← добавили
        skip_updates=False,
        allowed_updates=[
            "message",
            "callback_query",
            "chat_member",
            "chat_join_request"
        ]
    )
