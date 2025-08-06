import json
import hashlib
import random
from pathlib import Path
from aiogram import types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import Dispatcher
from shared import load_players, load_all_reports, load_victims, load_cultists, load_rituals
from hashlib import sha256
from datetime import datetime

REPORT_FILE = Path("ritual_reports.json")
VICTIMS_FILE = Path("victims.json")

class FBIReport(StatesGroup):
    choosing_case = State()
    choosing_victim = State()
    entering_weapon = State()
    choosing_mask = State()
    choosing_ritual = State()
    confirming = State()


def normalize_weapon_id(text):
    mapping = {
        "А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H", "К": "K",
        "М": "M", "О": "O", "Р": "P", "Т": "T", "Х": "X", "У": "Y"
    }
    text = text.strip().upper()
    return "".join(mapping.get(ch, ch) for ch in text)

def generate_case_code(victim_id: int) -> str:
    hash_bytes = hashlib.sha256(str(victim_id).encode()).digest()
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    letters = ''.join(alphabet[b % len(alphabet)] for b in hash_bytes[:4])
    return f"RIT-{letters}"

def get_open_cases():
    all_reports = load_all_reports()
    open_cases = []

    for victim_id_str, data in all_reports.items():
        victim_id = int(victim_id_str)
        victim_name = data.get("victim_name")
        place = data.get("place")
        ritual = data.get("ritual")
        reports = data.get("reports", [])

        for idx, report in enumerate(reports):
            report_key = f"{victim_id}:{idx}"  # уникальный ключ

            # если уже есть fbi_report по этому конкретному репорту, пропускаем
            if report.get("fbi_report"):
                continue

            timestamp = report.get("timestamp")
            dt = datetime.fromisoformat(timestamp)
            short_time = dt.strftime("%H:%M")
            short_place = place.strip().split()[-1]

            open_cases.append({
                "report_key": report_key,
                "victim_id": victim_id,
                "report_index": idx,
                "case_code": generate_case_code(victim_id),
                "victim_name": victim_name,
                "place": short_place,
                "ritual": ritual,
                "time": short_time
            })

    return open_cases
def register_fbi_handlers(dp: Dispatcher):
    @dp.message_handler(commands=["расследовать"], state="*")
    async def start_fbi_report(message: types.Message, state: FSMContext):
        user_id = message.from_user.id
        players = load_players()

        if players.get(str(user_id), {}).get("team") != "fbi":
            await message.reply("⛔ Эта команда доступна только агентам ФБР.")
            return

        cases = get_open_cases()
        if not cases:
            await message.reply("✅ Нет открытых дел для расследования.")
            return

        keyboard = InlineKeyboardMarkup()

        for case in cases:
            short_place = case["place"].strip().split()[-1]
            time_str = case["time"]

            label = f"{case['case_code']} · R{case['report_index']} · {short_place} · {time_str}"
            keyboard.add(
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"fbi_case:{case['report_key']}"
                )
            )

        await message.reply("🕵️ Выберите дело для расследования:", reply_markup=keyboard)
        await state.set_state(FBIReport.choosing_case.state)
# Выбор жертвы по описанию из 4 вариантов
    @dp.callback_query_handler(lambda c: c.data.startswith("fbi_case:"), state=FBIReport.choosing_case)
    async def select_case(callback: types.CallbackQuery, state: FSMContext):
        report_key = callback.data.split(":", 1)[1]
        victim_id_str, report_index_str = report_key.split(":")
        victim_id = int(victim_id_str)
        report_index = int(report_index_str)

        await state.update_data(victim_id=victim_id, report_index=report_index)


        victims = load_victims()
        if victim_id not in victims:
            await callback.message.edit_text("⚠️ Жертва не найдена.")
            return

        correct_victim = victims[victim_id]
        other_victims = [v for vid, v in victims.items() if vid != victim_id]
        random.shuffle(other_victims)
        options = [correct_victim] + other_victims[:3]
        random.shuffle(options)

        keyboard = InlineKeyboardMarkup()
        for v in options:
            keyboard.add(InlineKeyboardButton(
                text=v["name"],
                callback_data=f"victim_choice:{v['id']}"
            ))

        await state.update_data(correct_victim_id=victim_id)
        await callback.message.edit_text(
                f"✅ Дело выбрано.\n\n👤 <b>Описание жертвы:</b>\n{correct_victim['description']}\n\nВыберите имя жертвы:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        await state.set_state(FBIReport.choosing_victim.state)

# Обработка выбора жертвы
    @dp.callback_query_handler(lambda c: c.data.startswith("victim_choice:"), state=FBIReport.choosing_victim)
    async def victim_chosen(callback: types.CallbackQuery, state: FSMContext):
        chosen_id = callback.data.split(":")[1]
        data = await state.get_data()
        correct_id_str = data.get("correct_victim_id")
        if correct_id_str is None:
            await callback.message.edit_text("⚠️ Ошибка: Не удалось получить ID верной жертвы. Пожалуйста, начните заново.")
            await state.finish()
            return

        correct_id = int(correct_id_str)

        await state.update_data(selected_victim_id=chosen_id)

        chosen_id = int(chosen_id)
        if chosen_id == correct_id:
            await callback.message.edit_text("✅ Имя жертвы выбрано верно.")
        else:
            await callback.message.edit_text("⚠️ Имя выбрано, но оно может быть неверным.")

        await state.set_state(FBIReport.entering_weapon.state)
        await callback.message.answer("🔍 Теперь отправьте ID найденного оружия (QR/код).")

    @dp.message_handler(state=FBIReport.entering_weapon)
    async def enter_weapon_id(message: types.Message, state: FSMContext):
        raw_input = message.text.strip()
        weapon_id = normalize_weapon_id(raw_input)

        if not weapon_id:
            await message.reply("❌ Введите корректный ID оружия.")
            return

        await state.update_data(weapon_id=weapon_id)

        # Вызовем следующий шаг сразу: генерация кнопок
        await ask_mask_choice(message, state)
        
   

    async def ask_mask_choice(message: types.Message, state: FSMContext):
        data = await state.get_data()
        victim_id = data.get("victim_id")

        cultists = load_cultists()
        reports = load_all_reports()
        report = reports.get(str(victim_id))
        if not report or not report.get("reports"):
            await message.reply("⚠️ Данные по делу не найдены.")
            await state.finish()
            return

        report_index = data.get("report_index")
        selected_report = report["reports"][report_index] # Берём последний репорт
        identity_id = selected_report.get("identity_id")
        print(f"[DEBUG] identity_id из report: {identity_id}")
        print(f"[DEBUG] Все id из cultists: {[c['id'] for c in cultists]}")
        identity = next((c for c in cultists if c["id"] == str(identity_id)), None)
        if not identity:
            await message.reply("⚠️ Не удалось найти культлиста.")
            await state.finish()
            return

        correct_symbol = identity.get("mask_symbol")
        print(f"[DEBUG] victim_id={victim_id} identity_id={identity_id} correct_symbol={correct_symbol}")
        other_symbols = [c["mask_symbol"] for c in cultists if c["mask_symbol"] != correct_symbol]
        random.shuffle(other_symbols)
        options = list({correct_symbol, *other_symbols[:4]})
        random.shuffle(options)

        keyboard = InlineKeyboardMarkup()
        for symbol in options:
            keyboard.add(InlineKeyboardButton(
                text=symbol,
                callback_data=f"mask_choice:{symbol}"
            ))

        await state.update_data(correct_mask_symbol=correct_symbol)
        await message.reply(
            "🎭 Выберите символ маски, который был на культлисте:",
            reply_markup=keyboard
        )
        await state.set_state(FBIReport.choosing_mask.state)

    @dp.callback_query_handler(lambda c: c.data.startswith("mask_choice:"), state=FBIReport.choosing_mask)
    async def mask_chosen(callback: types.CallbackQuery, state: FSMContext):
        chosen_symbol = callback.data.split(":", 1)[1]
        data = await state.get_data()
        correct_symbol = data.get("correct_mask_symbol")

        if chosen_symbol == correct_symbol:
            await callback.message.edit_text("✅ Маска выбрана верно.")
        else:
            await callback.message.edit_text("⚠️ Символ маски, возможно, неверен.")

        await state.set_state(FBIReport.choosing_ritual.state)
        await callback.message.answer("🔮 Теперь введите предполагаемый тип ритуала.")
        rituals = load_rituals()
        if not rituals:
            await callback.message.answer("⚠️ Не удалось загрузить список ритуалов.")
            await state.finish()
            return

        keyboard = InlineKeyboardMarkup(row_width=2)
        buttons = [InlineKeyboardButton(text=rit, callback_data=f"ritual_choice:{rit}") for rit in rituals]
        keyboard.add(*buttons)

        await callback.message.answer("🔮 Выберите предполагаемый тип ритуала:", reply_markup=keyboard)
        await state.set_state(FBIReport.choosing_ritual.state) 

    @dp.callback_query_handler(lambda c: c.data.startswith("ritual_choice:"), state=FBIReport.choosing_ritual)
    async def ritual_chosen(callback: types.CallbackQuery, state: FSMContext):
        ritual = callback.data.split(":", 1)[1]
        await state.update_data(ritual_guess=ritual)

        data = await state.get_data()
        case_code = generate_case_code(data["victim_id"])

        summary = (
            f"📝 Подтверждение:\n\n"
            f"📁 Дело: {case_code}\n"
            f"🔪 Оружие: <code>{data['weapon_id']}</code>\n"
            f"🎭 Маска: {data['correct_mask_symbol']}\n"
            f"🔮 Ритуал: {ritual}\n\n"
            f"Нажмите кнопку, чтобы подтвердить отправку отчёта."
        )

        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton(text="✅ Отправить отчёт", callback_data="fbi_submit_report"))

        await callback.message.edit_text(summary, reply_markup=keyboard, parse_mode="HTML")
        await state.set_state(FBIReport.confirming.state)