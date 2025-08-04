import json
import hashlib
import random
from pathlib import Path
from aiogram import types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import Dispatcher
from shared import load_players, load_all_reports, load_victims
from hashlib import sha256

REPORT_FILE = Path("ritual_reports.json")
VICTIMS_FILE = Path("victims.json")

class FBIReport(StatesGroup):
    choosing_case = State()
    choosing_victim = State()
    entering_weapon = State()
    choosing_mask = State()
    choosing_ritual = State()
    confirming = State()


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
        if "fbi_report" not in data:
            open_cases.append({
                "victim_id": victim_id,
                "case_code": generate_case_code(victim_id),
                "victim_name": data.get("victim_name"),
                "place": data.get("place"),
                "ritual": data.get("ritual")
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
            keyboard.add(
                InlineKeyboardButton(
                    text=case["case_code"],
                    callback_data=f"fbi_case:{case['victim_id']}"
                )
            )

        await message.reply("🕵️ Выберите дело для расследования:", reply_markup=keyboard)
        await state.set_state(FBIReport.choosing_case.state)

    @dp.callback_query_handler(lambda c: c.data.startswith("fbi_case:"), state=FBIReport.choosing_case)
    async def select_case(callback: types.CallbackQuery, state: FSMContext):
        victim_id = int(callback.data.split(":")[1])
        await state.update_data(victim_id=victim_id)
        await callback.message.edit_text("✅ Дело выбрано. Теперь выберите жертву по описанию...")
        await state.set_state(FBIReport.choosing_victim.state)

# Выбор жертвы по описанию из 4 вариантов
    @dp.message_handler(state=FBIReport.choosing_victim)
    async def choose_victim(message: types.Message, state: FSMContext):
        victims = load_victims()
        data = await state.get_data()
        victim_id = int(data.get("victim_id"))
        
        if victim_id not in victims:
            await message.reply("⚠️ Ошибка: данные жертвы не найдены.")
            return

        correct_victim = victims[victim_id]
        other_victims = [v for vid, v in victims.items() if vid != victim_id]
        random.shuffle(other_victims)

        options = [correct_victim] + other_victims[:3]
        random.shuffle(options)

        keyboard = InlineKeyboardMarkup()
        for v in options:
            keyboard.add(InlineKeyboardButton(
                text=v["victim_name"],
                callback_data=f"victim_choice:{v['id']}"
            ))

        await state.update_data(correct_victim_id=victim_id)
        await message.reply(
            f"👤 <b>Описание жертвы:</b>\n{correct_victim['victim_description']}\n\nВыберите имя жертвы:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

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
