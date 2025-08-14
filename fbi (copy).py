import json
import hashlib
import random
import os
import cv2
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from aiogram import types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import Dispatcher
from shared import load_players, load_all_reports, load_victims, load_cultists, load_rituals
from datetime import datetime, timedelta
TZ_OFFSET = timedelta(hours=3)
from photo_tools import ultra_obscured_version

REPORT_FILE = Path("ritual_reports.json")
VICTIMS_FILE = Path("victims.json")
CASES_FILE = Path("fbi_cases.json")
SCORES_FILE = Path("scores.json")
FBI_CHANNEL_ID = int(os.getenv("FBI_CHANNEL_ID", "0"))

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

        txt = (data or "").strip()
        low = txt.lower()

        # Вариант 1: полная ссылка с параметром start
        if low.startswith("http://") or low.startswith("https://") or low.startswith("tg://"):
            try:
                u = urlparse(txt)
                qs = parse_qs(u.query)
                start_vals = qs.get("start") or []
                if start_vals:
                    payload = start_vals[0]
                    if payload.lower().startswith("weapon-"):
                        code = payload.split("-", 1)[-1]
                        return normalize_weapon_id(code)
            except Exception:
                pass

        # Вариант 2: "weapon-XXXX" или "weapon:XXXX"
        if low.startswith("weapon-"):
            return normalize_weapon_id(txt.split("-", 1)[-1])
        if low.startswith("weapon:"):
            return normalize_weapon_id(txt.split(":", 1)[-1])

        return None
    except Exception:
        return None

def generate_case_code(victim_id: int) -> str:
    hash_bytes = hashlib.sha256(str(victim_id).encode()).digest()
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    letters = ''.join(alphabet[b % len(alphabet)] for b in hash_bytes[:4])
    return f"RIT-{letters}"

def load_cases():
    if CASES_FILE.exists():
        with open(CASES_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []

def load_scores():
    try:
        with open(SCORES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_scores(data):
    with open(SCORES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def save_cases(cases):
    with open(CASES_FILE, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=2)

def get_open_cases():
    cases = load_cases()
    victims = load_victims()

    open_cases = []
    for c in cases:
        if c.get("status") != "open":
            continue
        victim_id = int(c.get("victim_id"))
        victim_name = c.get("victim_name") or victims.get(victim_id, {}).get("name")
        place = c.get("place")
        ritual = c.get("ritual")
        report_index = int(c.get("report_index", 0))
        created_at = c.get("created_at")
        try:
            short_time = (datetime.fromisoformat(created_at) + TZ_OFFSET).strftime("%H:%M")
        except Exception:
            short_time = "??:??"

        open_cases.append({
            # сохраняем старый формат, чтобы UI и FSM работали без переделок
            "report_key": f"{victim_id}:{report_index}",
            "victim_id": victim_id,
            "report_index": report_index,
            "case_code": generate_case_code(victim_id),
            "victim_name": victim_name,
            "place": (place or "").strip().split()[-1] if place else "",
            "ritual": ritual,
            "time": short_time
        })
    return open_cases
def register_fbi_handlers(dp: Dispatcher):
    # /дела — список дел только в ЛС (+кнопка deeplink из групп)
    @dp.message_handler(commands=["дела"])
    async def show_open_cases(message: types.Message, state: FSMContext):
        if message.chat.type != "private":
            me = await message.bot.get_me()
            kb = InlineKeyboardMarkup().add(
                InlineKeyboardButton(
                    text="Открыть личку с ботом",
                    url=f"https://t.me/{me.username}?start=fbi_cases"
                )
            )
            await message.reply("⛔ Команда доступна в личке боту.", reply_markup=kb)
            return

        user_id = message.from_user.id
        players = load_players()
        if players.get(str(user_id), {}).get("team") != "fbi":
            await message.reply("⛔ Эта команда доступна только агентам ФБР.")
            return

        open_cases = get_open_cases() or []
        if not open_cases:
            await message.reply("✅ Все дела закрыты. Ждите следующего преступления.")
            return

        await message.reply(
            "🛡️ <b>Брифинг ФБР</b>\n"
            "Перед вами — открытые дела, сформированные по принятым отчётам культа. "
            "Каждое дело привязано к конкретному эпизоду (R1–R3). "
            "Ваша задача — подтвердить четыре пункта: жертва, оружие, маска, ритуал.\n\n"
            "Выберите карточку для работы.", parse_mode="HTML"
        )

        seen = set()
        kb = InlineKeyboardMarkup(row_width=1)
        for c in open_cases:
            rk = c.get("report_key")
            if not rk or rk in seen:
                continue
            seen.add(rk)
            label = f"📁 {c.get('case_code','RIT-?')} · {c.get('place','?')} · R{int(c.get('report_index',0))+1}"
            kb.add(InlineKeyboardButton(text=label, callback_data=f"fbi_case:{rk}"))

        await message.reply("🕵️ Активные дела:", reply_markup=kb)
        await state.set_state(FBIReport.choosing_case.state)

    # /расследовать — старт сценария только в ЛС (+deeplink из групп)
    @dp.message_handler(commands=["расследовать"], state="*")
    async def start_fbi_report(message: types.Message, state: FSMContext):
        if message.chat.type != "private":
            me = await message.bot.get_me()
            kb = InlineKeyboardMarkup().add(
                InlineKeyboardButton(
                    text="Открыть личку с ботом",
                    url=f"https://t.me/{me.username}?start=fbi_investigate"
                )
            )
            await message.reply("⛔ Расследование ведётся в личке боту.", reply_markup=kb)
            return

        user_id = message.from_user.id
        players = load_players()
        if players.get(str(user_id), {}).get("team") != "fbi":
            await message.reply("⛔ Эта команда доступна только агентам ФБР.")
            return

        cases = get_open_cases()
        if not cases:
            await message.reply("✅ Нет открытых дел для расследования.")
            return

        kb = InlineKeyboardMarkup(row_width=1)
        for case in cases:
            label = f"{case['case_code']} · R{case['report_index']+1} · {case['place']} · {case['time']}"
            kb.add(InlineKeyboardButton(text=label, callback_data=f"fbi_case:{case['report_key']}"))

        await message.reply(
            "🎯 <b>Протокол 12.3: запуск расследования</b>\n"
            "Выберите одно дело. После выбора карта будет закреплена за вами на время сессии. "
            "Один агент — одна попытка по делу. Работайте точно.", parse_mode="HTML"
        )
        await message.reply("🕵️ Выберите дело для расследования:", reply_markup=kb)
        await state.set_state(FBIReport.choosing_case.state)

    # Выбор дела — ловим из любого состояния и только в ЛС
    @dp.callback_query_handler(lambda c: c.data.startswith("fbi_case:"), state="*")
    async def select_case(callback: types.CallbackQuery, state: FSMContext):
        if callback.message.chat.type != "private":
            await callback.answer("⛔ Только в личке.", show_alert=True)
            return

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
        other_ids = [vid for vid in victims.keys() if vid != victim_id]
        random.shuffle(other_ids)
        option_ids = [victim_id] + other_ids[:3]
        random.shuffle(option_ids)

        kb = InlineKeyboardMarkup(row_width=1)
        for vid in option_ids:
            kb.add(InlineKeyboardButton(text=victims[vid]["name"], callback_data=f"victim_choice:{vid}"))

        await state.update_data(correct_victim_id=victim_id)
        await callback.message.edit_text(
            "📌 <b>Досье закреплено.</b>\n\n"
            f"👤 <b>Описание жертвы:</b>\n{correct_victim['description']}\n\n"
            "Подтвердите личность жертвы по описанию. Выберите имя из списка ниже.\n"
            "⚠️ Ошибка на этом шаге фиксируется в отчёте и снижает доверие к вашей гипотезе.",
            reply_markup=kb,
            parse_mode="HTML"
        )
        await state.set_state(FBIReport.choosing_victim.state)

    # Выбор жертвы — только в ЛС
    @dp.callback_query_handler(lambda c: c.data.startswith("victim_choice:"), state=FBIReport.choosing_victim)
    async def victim_chosen(callback: types.CallbackQuery, state: FSMContext):
        if callback.message.chat.type != "private":
            await callback.answer("⛔ Только в личке.", show_alert=True)
            return

        chosen_id = int(callback.data.split(":")[1])
        data = await state.get_data()
        correct_id = int(data.get("correct_victim_id"))

        await state.update_data(selected_victim_id=chosen_id)

        if chosen_id == correct_id:
            await callback.message.edit_text(
                "✅ Личность подтверждена.\n"
                "Переходим к идентификатору орудия.",
                parse_mode="HTML"
            )
        else:
            await callback.message.edit_text(
                "⚠️ Личность <i>возможно</i> указана неверно.\n"
                "Тем не менее, продолжаем — несоответствие отразится в проверке.",
                parse_mode="HTML"
            )

        await state.set_state(FBIReport.entering_weapon.state)
        await callback.message.answer(
            "🔍 <b>Этап 2: идентификатор орудия</b>\n"
            "Отправьте код орудия (пример: <code>AB12</code> или <code>weapon:AB12</code>), "
            "либо пришлите фото с QR — система попытается распознать автоматически.\n"
            "Требования: латиница A–Z, цифры 0–9, дефис допускается. Ошибки в раскладке (Т≠T, Х≠X) недопустимы.",
            parse_mode="HTML"
        )

    # Ввод ID оружия — только в ЛС
    @dp.message_handler(state=FBIReport.entering_weapon)
    async def enter_weapon_id(message: types.Message, state: FSMContext):
        if message.chat.type != "private":
            await message.reply("⛔ Отправь ID оружия в личку боту.")
            return

        raw = (message.text or "").strip()
        low = raw.lower()
        if low.startswith("weapon:"):
            raw = raw.split(":", 1)[-1]
        elif low.startswith("weapon-"):
            raw = raw.split("-", 1)[-1]

        weapon_id = normalize_weapon_id(raw)
        if not weapon_id:
            await message.reply("❌ Введите корректный ID оружия.")
            return

        await message.reply(
            f"🗂️ Идентификатор зафиксирован: <code>{weapon_id}</code>\n"
            "Продолжаем.", parse_mode="HTML"
        )
        await state.update_data(weapon_id=weapon_id)
        await ask_mask_choice(message, state)

    @dp.message_handler(content_types=types.ContentType.PHOTO, state=FBIReport.entering_weapon)
    async def enter_weapon_by_qr_photo(message: types.Message, state: FSMContext):
        # Только в ЛС
        if message.chat.type != "private":
            await message.reply("⛔ Отправь фото с QR в личку боту.")
            return

        # Скачиваем фото во временный файл
        photo = message.photo[-1]
        os.makedirs("tmp", exist_ok=True)
        tmp_path = Path("tmp") / f"fbi_qr_{message.from_user.id}_{photo.file_unique_id}.jpg"
        await photo.download(destination_file=tmp_path)

        try:
            wid = extract_weapon_from_qr(str(tmp_path))
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

        if not wid:
            await message.reply(
                "❌ Не удалось прочитать QR. Пришлите более чёткое фото\n"
                "или введите код вручную (например: <code>weapon:AB12</code> или просто <code>AB12</code>).",
                parse_mode="HTML"
            )
            return

        # Приняли ID — продолжаем сценарий, как и при текстовом вводе
        await state.update_data(weapon_id=wid)
        await message.reply(f"🔐 ID оружия распознан: <code>{wid}</code>", parse_mode="HTML")
        await ask_mask_choice(message, state)
    # Выбор маски — с фолбэком, если identity_id нет
    async def ask_mask_choice(message: types.Message, state: FSMContext):
        data = await state.get_data()
        victim_id = data.get("victim_id")

        reports = load_all_reports()
        record = reports.get(str(victim_id))
        if not record or not record.get("reports"):
            await message.reply("⚠️ Данные по делу не найдены.")
            await state.finish()
            return

        report_index = data.get("report_index")
        selected_report = record["reports"][report_index]
        identity_id = selected_report.get("identity_id")

        cultists = load_cultists()
        identity = next((c for c in cultists if str(c.get("id")) == str(identity_id)), None)

        kb = InlineKeyboardMarkup(row_width=1)
        if identity:
            correct_symbol = identity.get("mask_symbol")
            pool = [c["mask_symbol"] for c in cultists if c.get("mask_symbol")]
            other = [s for s in pool if s != correct_symbol]
            random.shuffle(other)
            options = [correct_symbol, *other[:4]]
            await state.update_data(correct_mask_symbol=correct_symbol)
            prompt = (
                "🎭 <b>Этап 3: символ маски</b>\n"
                "Выберите символ, который фигурировал у причастного культиста. "
                "Символ может встречаться в допросах.\n"
                "Выбор фиксируется и будет проверен."
            )
        else:
            pool = list({c["mask_symbol"] for c in cultists if c.get("mask_symbol")})
            random.shuffle(pool)
            options = pool[:5]
            await state.update_data(correct_mask_symbol=None)
            prompt = (
                         "🎭 <b>Этап 3: символ маски</b>\n"
                         "В отчёте символ не указан. Выберите наиболее вероятный вариант по сопутствующим признакам.\n"
                         "Решение фиксируется и будет проверено."
                     )

        random.shuffle(options)
        for symbol in options:
            kb.add(InlineKeyboardButton(text=symbol, callback_data=f"mask_choice:{symbol}"))

        await message.reply(prompt, reply_markup=kb)
        await state.set_state(FBIReport.choosing_mask.state)

    @dp.callback_query_handler(lambda c: c.data.startswith("mask_choice:"), state=FBIReport.choosing_mask)
    async def mask_chosen(callback: types.CallbackQuery, state: FSMContext):
        if callback.message.chat.type != "private":
            await callback.answer("⛔ Только в личке.", show_alert=True)
            return

        chosen_symbol = callback.data.split(":", 1)[1]
        await state.update_data(chosen_mask_symbol=chosen_symbol)  # ← добавили

        data = await state.get_data()
        correct_symbol = data.get("correct_mask_symbol")

        if correct_symbol is None:
            await callback.message.edit_text(f"✅ Маска выбрана: {chosen_symbol}")
        else:
            await callback.message.edit_text(
                "✅ Маска выбрана верно." if chosen_symbol == correct_symbol
                else "⚠️ Символ маски, возможно, неверен."
            )

        rituals = load_rituals()
        if not rituals:
            await callback.message.answer("⚠️ Не удалось загрузить список ритуалов.")
            await state.finish()
            return

        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(*[InlineKeyboardButton(text=rit, callback_data=f"ritual_choice:{rit}") for rit in rituals])

        await callback.message.answer(
            "🔮 <b>Этап 4: тип ритуала</b>\n"
            "Определите тип по наблюдаемым элементам (атрибуты, место, последовательность действий, характер повреждений). "
            "Выбор окончательный для вашей попытки.",
            reply_markup=kb, parse_mode="HTML"
        )
        await state.set_state(FBIReport.choosing_ritual.state)

    @dp.callback_query_handler(lambda c: c.data.startswith("ritual_choice:"), state=FBIReport.choosing_ritual)
    async def ritual_chosen(callback: types.CallbackQuery, state: FSMContext):
        if callback.message.chat.type != "private":
            await callback.answer("⛔ Только в личке.", show_alert=True)
            return

        ritual = callback.data.split(":", 1)[1]
        await state.update_data(ritual_guess=ritual)

        data = await state.get_data()
        case_code = generate_case_code(data["victim_id"])

        summary = (
            "📝 <b>Контрольный лист</b>\n\n"
            f"📁 Дело: {case_code}\n"
            f"🔪 Оружие: <code>{data['weapon_id']}</code>\n"
            f"🎭 Маска: {data.get('chosen_mask_symbol')}\n"
            f"🔮 Ритуал: {ritual}\n\n"
            "Подтвердите отправку результатов. Напоминаем: один агент — одна попытка по делу. "
            "Дело закроется только при верном ответе на все четыре пункта."
        )

        kb = InlineKeyboardMarkup().add(
            InlineKeyboardButton(text="✅ Отправить отчёт", callback_data="fbi_submit_report")
        )
        await callback.message.edit_text(summary, reply_markup=kb, parse_mode="HTML")
        await state.set_state(FBIReport.confirming.state)

    @dp.callback_query_handler(lambda c: c.data == "fbi_submit_report", state=FBIReport.confirming)
    async def fbi_submit_report(callback: types.CallbackQuery, state: FSMContext):
        if callback.message.chat.type != "private":
            await callback.answer("⛔ Только в личке.", show_alert=True)
            return

        agent_id = callback.from_user.id
        data = await state.get_data()
        victim_id = int(data.get("victim_id"))
        report_index = int(data.get("report_index"))

        # 1) Найдём дело
        cases = load_cases()
        case = next((c for c in cases
                     if int(c.get("victim_id", -1)) == victim_id
                     and int(c.get("report_index", -1)) == report_index), None)
        if not case:
            await callback.message.edit_text("⚠️ Дело не найдено или удалено.")
            await state.finish()
            return

        if case.get("status") == "closed":
            closer = case.get("solved_by")
            when = case.get("solved_at", "")[:16].replace("T", " ")
            who = f"агент tg://user?id={closer}" if closer else "другой агент"
            await callback.message.edit_text(
                f"🟡 Это дело уже закрыто ({when}), {who}.",
                parse_mode="HTML"
            )
            await state.finish()
            return

        # 2) Один агент — одна попытка
        attempts = case.get("attempts", [])
        if any(int(a.get("agent_id", 0)) == agent_id for a in attempts):
            await callback.message.edit_text("⛔ У вас уже была попытка по этому делу.")
            await state.finish()
            return

        # 3) Истина из принятых отчётов
        all_reports = load_all_reports()
        block = all_reports.get(str(victim_id))
        if not block:
            await callback.message.edit_text("⚠️ Исходные данные по делу не найдены.")
            await state.finish()
            return
        reports = block.get("reports", [])
        if report_index >= len(reports):
            await callback.message.edit_text("⚠️ Репорт по делу не найден.")
            await state.finish()
            return
        rep = reports[report_index]
        true_weapon = (rep.get("weapon_id") or "").strip().upper()

        # маска из identity
        identity_id = rep.get("identity_id")
        cultists = load_cultists()
        identity = next((c for c in cultists if str(c.get("id")) == str(identity_id)), None)
        true_mask = identity.get("mask_symbol") if identity else None

        true_ritual = block.get("ritual")
        true_victim_id = victim_id  # по case

        # 4) Ответы агента
        selected_victim_id = int(data.get("selected_victim_id", -1))
        agent_weapon = (data.get("weapon_id") or "").strip().upper()
        agent_mask = data.get("chosen_mask_symbol")
        agent_ritual = data.get("ritual_guess")

        # 5) Проверка (все 4 должны быть True)
        victim_correct = (selected_victim_id == true_victim_id)
        weapon_correct = (agent_weapon == true_weapon)
        mask_checked = true_mask is not None
        mask_correct = (agent_mask == true_mask) if mask_checked else False
        ritual_correct = (agent_ritual == true_ritual)

        all_ok = victim_correct and weapon_correct and mask_correct and ritual_correct

        # 6) Записываем попытку
        attempt = {
            "agent_id": agent_id,
            "timestamp": (datetime.utcnow() + TZ_OFFSET).isoformat(),
            "answers": {
                "victim_id": selected_victim_id,
                "weapon_id": agent_weapon,
                "mask_symbol": agent_mask,
                "ritual_guess": agent_ritual
            },
            "result": {
                "victim_correct": victim_correct,
                "weapon_correct": weapon_correct,
                "mask_correct": mask_correct if mask_checked else None,
                "ritual_correct": ritual_correct
            },
            "closed_case": all_ok
        }
        attempts.append(attempt)
        case["attempts"] = attempts

        # 7) Если всё верно — закрываем, начисляем очки и публикуем в канал ФБР
        if all_ok:
            case["status"] = "closed"
            case["solved_by"] = agent_id
            case["solved_at"] = (datetime.utcnow() + TZ_OFFSET).isoformat()

            # Очки
            scores = load_scores()
            scores[str(agent_id)] = scores.get(str(agent_id), 0) + 1
            save_scores(scores)

            # Сообщение агенту
            await callback.message.edit_text(
                "🟢 <b>Дело закрыто.</b>\n"
                "Все четыре пункта подтверждены: жертва, орудие, маска, ритуал.\n"
                "Начислено: +1 очко. Отчёт сохранён в архиве дел.",
                parse_mode="HTML"
            )

            # Публикация в канал ФБР (если задан ID)
            if FBI_CHANNEL_ID:
                try:
                    case_id = case.get("case_id") or f"{victim_id}-R{report_index+1}"
                    closer_mention = f"<a href='tg://user?id={agent_id}'>агент</a>"
                    verdict = "Подтверждены: личность жертвы, идентификатор орудия, символ маски, тип ритуала."
                    await callback.message.bot.send_message(
                        FBI_CHANNEL_ID,
                        f"🗂 <b>Дело #{case_id}</b> закрыто {closer_mention}.\n{verdict}",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    print(f"[FBI] Не удалось опубликовать закрытие дела: {e}")
        else:
            # Сообщение агенту с разбором
            lines = [
                "🔴 <b>Проверка не пройдена.</b>",
                "Для закрытия требуется верный ответ по всем пунктам:",
                f"• Жертва: {'✅' if victim_correct else '❌'}",
                f"• Оружие: {'✅' if weapon_correct else '❌'}",
                f"• Маска: {'✅' if mask_correct else '❌'}",
                f"• Ритуал: {'✅' if ritual_correct else '❌'}",
                "",
                "Попытка агента исчерпана. Дело остаётся открытым.",
                "Рекомендации: переоцените улики, перечитайте описания жертвы и методику ритуала, "
                "перепроверьте транслитерацию оружия (A↔А, X↔Х и т. п.)."
            ]
            await callback.message.edit_text("\n".join(lines), parse_mode="HTML")

        # 8) Сохранить дела и завершить FSM
        save_cases(cases)
        await state.finish()
    @dp.message_handler(lambda m: m.text and m.text.lower().startswith("/start fbi_"), state="*")
    async def fbi_start_router(message: types.Message, state: FSMContext):
        if message.chat.type != "private":
            await message.reply("⛔ Команда доступна в личке боту.")
            return
        args = (message.get_args() or "").lower()
        if args == "fbi_cases":
            # вызвать локальный show_open_cases (объявлен ВНУТРИ register_fbi_handlers)
            return await show_open_cases(message, state)
        if args == "fbi_investigate":
            # вызвать локальный start_fbi_report (тоже объявлен ВНУТРИ)
            return await start_fbi_report(message, state)
        # если это /start с другими аргами — игнорируем, даст отработать твоему общему /start

async def create_fbi_cases_for_victim(victim_id: int, bot, fbi_channel_id: int) -> int:
    """
    На каждый принятый отчёт по жертве создаёт отдельное дело ФБР:
    - делает искажённую копию фото отчёта
    - постит дело в канал ФБР (фото + описание)
    - сохраняет карточку дела в data/fbi_cases.json
    Возвращает количество созданных дел.
    """
    all_reports = load_all_reports()
    block = all_reports.get(str(victim_id))
    if not block:
        return 0

    accepted = block.get("reports", [])
    if not accepted:
        return 0

    cases = load_cases()
    existing = {c.get("case_id") for c in cases}

    created = 0
    out_dir = Path("fbi_cases")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Пробежимся по всем принятым отчётам по этой жертве: R1, R2, R3...
    for idx, rep in enumerate(accepted, start=1):
        case_id = f"{victim_id}-R{idx}"
        if case_id in existing:
            continue

        user_id = rep.get("user_id")
        # фото отчёта сохраняется у тебя как reports/ritual_{victim_id}_{user_id}.jpg
        src = Path("reports") / f"ritual_{victim_id}_{user_id}.jpg"
        if not src.exists():
            # бывают случаи, когда фото публиковалось по file_id — тогда оригинала на диске нет
            # в такой ситуации можно скипнуть или попытаться re-get через API — оставим скип
            continue

        try:
            produced_path = ultra_obscured_version(str(src))  # функция вернёт путь к _distorted.jpg
            if not produced_path or not os.path.exists(produced_path):
                print(f"[FBI] Обфускация не удалась для {case_id}: файл не создан")
                continue

            # хотим хранить всё в папке fbi_cases с предсказуемым именем
            obscured = out_dir / f"case_{case_id}_obscured.jpg"
            # если функция сохранила в другом месте — перенесём/переименуем
            if os.path.abspath(produced_path) != os.path.abspath(obscured):
                try:
                    os.replace(produced_path, obscured)  # атомарное перемещение
                except Exception:
                    # fallback: копия+удаление
                    import shutil
                    shutil.copyfile(produced_path, obscured)
                    try:
                        os.remove(produced_path)
                    except Exception:
                        pass
        except Exception as e:
            print(f"[FBI] Обфускация не удалась для {case_id}: {e}")
            continue

        caption = (
            f"🗂 <b>Новое дело</b> — <b>#{case_id}</b>\n"
            f"Жертва: {block.get('victim_name')}\n"
            f"Ритуал: {block.get('ritual')}\n"
            f"Место: {block.get('place')}\n"
            f"ℹ️ На фото — искажённый кадр. Найдите связь."
        )

        try:
            with open(obscured, "rb") as ph:
                posted = await bot.send_photo(fbi_channel_id, photo=ph, caption=caption, parse_mode="HTML")
            obscured_file_id = posted.photo[-1].file_id if posted.photo else None
        except Exception as e:
            print(f"[FBI] Не удалось опубликовать дело {case_id} в канал ФБР: {e}")
            continue

        cases.append({
            "case_id": case_id,
            "victim_id": victim_id,
            "victim_name": block.get("victim_name"),
            "ritual": block.get("ritual"),
            "place": block.get("place"),
            "created_at": (datetime.utcnow() + TZ_OFFSET).isoformat(),
            "obscured_file_id": obscured_file_id,
            "report_user_id": user_id,
            "report_index": idx - 1,   # индекс репорта в массиве reports
            "status": "open"           # на будущее (можно закрывать)
        })
        existing.add(case_id)
        created += 1

    if created:
        save_cases(cases)

    return created