import os
import json
import random
import asyncio
from pathlib import Path
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from dotenv import load_dotenv
from datetime import datetime
from aiogram.utils.markdown import quote_html
from aiogram.utils.exceptions import CantInitiateConversation
from photo_tools import ultra_obscured_version
from fbi import get_open_cases


# Подгружаем переменные окружения из .env
load_dotenv()
API_TOKEN = os.getenv("API_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CULT_CHANNEL_ID = int(os.getenv("CULT_CHANNEL_ID"))
CONTROL_CHAT_ID = int(os.getenv("CONTROL_CHAT_ID"))
FBI_CHANNEL_ID = int(os.getenv("FBI_CHANNEL_ID"))

bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

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

def normalize_weapon_id(text):
    mapping = {
        "А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H", "К": "K",
        "М": "M", "О": "O", "Р": "P", "Т": "T", "Х": "X", "У": "Y"
    }
    text = text.strip().upper()
    return "".join(mapping.get(ch, ch) for ch in text)

def safe_get_weapon_id(text):
    if not text or "weapon:" not in text:
        return None  # Вместо ошибки возвращаем None

    weapon_id = text.split("weapon:", 1)[-1].strip()
    if len(weapon_id) < 2:  # Проверяем минимальную длину
        return None

    return weapon_id
    

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

    all_reports[victim_key]["reports"].append(report)
    save_all_reports(all_reports)

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
    return current if (team is None or current == team) else None

def assign_team(user_id, team):
    players = load_players()
    players[str(user_id)] = team
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
            await asyncio.sleep(150)  # 2,5 минут

    ritual_loop_task = asyncio.create_task(auto_ritual_loop())
    await message.reply("🔮 Цикл ритуалов запущен. Каждые 15 минут будет новая жертва.")

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
    if not scores:
        await message.reply("Пока никто не пролил кровь.")
        return

    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_10 = sorted_scores[:10]

    text = "🏆 <b>Топ-10 культистов</b>:\n"

    for i, (user_id, score) in enumerate(top_10, 1):
        mention = f"<a href='tg://user?id={user_id}'>Культист</a>"
        text += f"{i}. {mention}: {score} очков\n"

    await message.reply(text, parse_mode="HTML")

@dp.message_handler(commands=["дела"])
async def show_open_cases(message: types.Message):
    user_id = message.from_user.id
    players = load_players()

    if players.get(str(user_id), {}).get("team") != "fbi":
        await message.reply("⛔ Эта команда доступна только агентам ФБР.")
        return

    open_cases = get_open_cases()
    if not open_cases:
        await message.reply("✅ Все дела закрыты. Ждите следующего преступления.")
        return

    text = "<b>🕵️ Активные дела:</b>\n\n"

    for case in open_cases:
        text += (
            f"📁 <b>{case['case_code']}</b>\n"
            f"📍 {case['place']}\n\n"
        )

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

# ==== ПРИЁМ ОТЧЁТОВ ====

@dp.message_handler(content_types=types.ContentType.PHOTO)
async def handle_report(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or f"id:{user_id}"
    print(f"[DEBUG] Получено фото от {username} в чате {message.chat.id}")

    if str(message.chat.id) != str(CULT_CHANNEL_ID):
        print(f"[DEBUG] ❌ Сообщение не из CULT_CHANNEL_ID ({CULT_CHANNEL_ID})")
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
        await message.reply("⛔ Ты не указал ID оружия.\nСначала отправь сообщение вида: <code>weapon:QW34</code>", parse_mode="HTML")
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

    if entry:
        pending.remove(entry)
    else:
        print(f"[DEBUG] ⚠️ Не найден отчёт с msg_id={msg_id} в pending.")
    save_pending(pending)

    if not entry:
        await call.answer("⛔ Отчёт уже обработан.", show_alert=True)
        return

    user_id = entry["user_id"]
    username = entry["username"]

    if action == "accept":
        # 1. Очки
        scores = load_scores()
        scores[str(user_id)] = scores.get(str(user_id), 0) + 1
        save_scores(scores)

        # 2. Данные из pending
        ritual = entry.get("ritual")
        place = entry.get("place")
        weapon_name = entry.get("weapon")   # Название, не ID
        weapon_id = entry.get("weapon_id")
        victim_id = entry.get("victim_id")
        

        players = load_players()
        player = players.get(str(user_id), {})
        identity_id = player.get("identity_id")

        
        photo_file_id = call.message.photo[-1].file_id if call.message.photo else None
        timestamp = datetime.utcnow().isoformat()

        report_entry = {
            "user_id": user_id,
            "identity_id": identity_id,
            "weapon_id": weapon_id,
            "weapon_name": weapon_name,
            "photo_file_id": photo_file_id,
            "timestamp": timestamp
        }

        add_report_entry(victim_id, {
            "victim_name": entry.get("victim_name"),
            "ritual": ritual,
            "place": place
        }, report_entry)

        # 3. Ответы
        old_caption = call.message.caption or ""
        new_caption = old_caption + f"\n✅ Очки начислены ({scores[str(user_id)]})"
        await call.message.edit_caption(new_caption)
        await bot.send_message(CULT_CHANNEL_ID, f"✅ @{username}, отчёт принят. У него {scores[str(user_id)]} очков.")

    elif action == "reject":
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
        players[str(user_id)] = {"team": "fbi"}
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
        f"<i>{identity['description']}</i>",
        parse_mode="HTML"
    )

#кнопка старт для игроков
@dp.message_handler(commands=["start"])
async def start_handler(message: types.Message):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🔮 Вступить в Культ", callback_data="join_cult"),
        InlineKeyboardButton("🕵️ Присоединиться к ФБР", callback_data="join_fbi")
    )
    await message.answer("Выбери свою сторону:", reply_markup=kb)
    
 ## ==== ОБРАБОТКА ОРУЖИЯ ====   
@dp.message_handler(lambda message: message.text and message.text.startswith("weapon:"))
async def handle_weapon_qr(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or f"id:{user_id}"
    weapon_id_raw = safe_get_weapon_id(message.text)

    if not weapon_id_raw:  # ✅ ИСПРАВЛЕНИЕ 3: проверяем ДО нормализации
        await message.reply(
            "❌ Неверный формат. Отправьте сообщение в виде:\n"
            "<code>weapon:ABC123</code>", 
            parse_mode="HTML"
        )
        print(f"[DEBUG] Неверный формат weapon_id от {username}: '{message.text}'")
        return
    weapon_id = normalize_weapon_id(weapon_id_raw)

    if len(weapon_id) < 2:
        await message.reply("❌ ID оружия слишком короткий. Минимум 2 символа.")
        print(f"[DEBUG] Слишком короткий weapon_id от {username}: '{weapon_id}'")
        return

    if not EVENT_FILE.exists():
        await message.reply("❌ Сейчас нет активного ритуала.")
        return

    try:
        with open(EVENT_FILE, encoding="utf-8") as f:
            event = json.load(f)
    except Exception as e:
        await message.reply("⚠️ Не удалось прочитать активный ритуал.")
        return

    try:
        with open(WEAPONS_FILE, encoding="utf-8") as f:
            weapons = json.load(f)
    except Exception as e:
        await message.reply("⚠️ Не удалось загрузить weapons.json.")
        return

    # Найти подходящий набор id для текущего оружия
    weapon_entry = next((w for w in weapons if w["name"] == event["weapon"]), None)

    if not weapon_entry:
        await message.reply("❌ Оружие не найдено в базе.")
        return

    if weapon_id not in weapon_entry["ids"]:
        await message.reply("❌ Неверный ID. Это не то оружие.")
        return

    # Проверить, подавал ли уже этот пользователь
    # Проверить, подавал ли уже этот пользователь
    reports = load_all_reports()
    victim_key = str(event["victim_id"])

    if victim_key in reports:
        if any(r.get("user_id") == user_id for r in reports[victim_key]["reports"]):
            await message.reply("⛔ Ты уже сообщил о своём оружии.")
            return

   
    # Сохраняем weapon_id за этим пользователем в текущем ритуале
    event.setdefault("assigned_weapons", [])
    # Удаляем предыдущую запись, если она была
    event["assigned_weapons"] = [w for w in event["assigned_weapons"] if w["user_id"] != user_id]

    event["assigned_weapons"].append({
        "user_id": user_id,
        "weapon_id": weapon_id
    })

    with open(EVENT_FILE, "w", encoding="utf-8") as f:
        json.dump(event, f, ensure_ascii=False, indent=2)

    await message.reply(f"🔐 Твой ID оружия (<code>{weapon_id}</code>) принят. Ждём фото ритуала.", parse_mode="HTML")

# ==== ЗАПУСК ====

if __name__ == "__main__":
    print("Бот запущен.")
    loop = asyncio.get_event_loop()
    loop.create_task(auto_ritual_loop())
    executor.start_polling(
        dp,
        skip_updates=False,
        allowed_updates=[
            "message",
            "callback_query",
            "chat_member",
            "chat_join_request"
        ]
    )
