import asyncio
import json
import logging
import time
from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware
from aiogram.types import (
    Message, CallbackQuery, BufferedInputFile, TelegramObject,
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from typing import Callable, Dict, Any, Awaitable

from config import BOT_TOKEN, ADMIN_IDS
from database import (
    init_db,
    create_account, get_account, get_all_accounts,
    get_active_accounts, update_account_cookies,
    delete_account, toggle_account,
)
from api_client import SendTGClient
from keyboards import (
    main_menu_kb, p2c_active_kb, accounts_list_kb,
    account_detail_kb, confirm_delete_kb,
    p2c_concurrent_kb, taken_order_kb,
    p2c_session_status_kb,
)
from qr_generator import generate_simple_qr
from p2c_websocket import p2c_session

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()


# ==========================================
# Middleware
# ==========================================

class AdminCheckMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = None
        if isinstance(event, Message):
            user = event.from_user
        elif isinstance(event, CallbackQuery):
            user = event.from_user

        if user and ADMIN_IDS and user.id not in ADMIN_IDS:
            if isinstance(event, Message):
                await event.answer("Доступ запрещён")
            elif isinstance(event, CallbackQuery):
                await event.answer("Доступ запрещён", show_alert=True)
            return

        return await handler(event, data)


router.message.middleware(AdminCheckMiddleware())
router.callback_query.middleware(AdminCheckMiddleware())


# ==========================================
# FSM States
# ==========================================

class AddAccountStates(StatesGroup):
    waiting_name = State()
    waiting_phone = State()


class RefreshCookiesStates(StatesGroup):
    waiting_phone = State()


class P2CSetupStates(StatesGroup):
    waiting_range = State()


# ==========================================
# Основные команды
# ==========================================

@router.message(Command("start"))
async def cmd_start(message: Message):
    has_active = any(
        p2c_session.is_running(k) for k in p2c_session.active_sessions
    )
    kb = p2c_active_kb() if has_active else main_menu_kb()

    await message.answer(
        "*Send.TG P2C Bot*\n\nУправление аккаунтами и P2C-ордерами.",
        parse_mode="Markdown",
        reply_markup=kb,
    )


# ==========================================
# Reply keyboard handlers
# ==========================================

@router.message(F.text == "Аккаунты")
async def show_accounts(message: Message):
    accounts = get_all_accounts()
    count = len(accounts)
    text = f"*Аккаунты* ({count})" if accounts else "Нет аккаунтов"
    await message.answer(
        text,
        parse_mode="Markdown",
        reply_markup=accounts_list_kb(accounts),
    )


@router.message(F.text == "Статистика")
async def show_stats_reply(message: Message):
    session_key = _find_session_for_chat(message.chat.id)
    if session_key:
        text = p2c_session.get_session_stats_text(session_key)
        await message.answer(
            text,
            parse_mode="Markdown",
            reply_markup=p2c_session_status_kb(session_key),
        )
    else:
        accounts = get_all_accounts()
        active = [a for a in accounts if a["is_active"]]
        text = (
            f"*Статистика*\n\n"
            f"Аккаунтов: {len(accounts)}\n"
            f"Активных: {len(active)}\n"
            f"P2C сессий: 0"
        )
        await message.answer(text, parse_mode="Markdown")


@router.message(F.text == "Статус")
async def show_p2c_status(message: Message):
    session_key = _find_session_for_chat(message.chat.id)
    if not session_key:
        await message.answer(
            "Нет активной P2C сессии.",
            reply_markup=main_menu_kb(),
        )
        return

    text = p2c_session.get_session_stats_text(session_key)
    await message.answer(
        text,
        parse_mode="Markdown",
        reply_markup=p2c_session_status_kb(session_key),
    )


@router.message(F.text == "Остановить")
async def stop_p2c_reply(message: Message):
    session_key = _find_session_for_chat(message.chat.id)
    if not session_key:
        await message.answer(
            "Нет активной P2C сессии.",
            reply_markup=main_menu_kb(),
        )
        return

    active_count = p2c_session.get_active_taken_count(session_key)
    if active_count > 0:
        await message.answer(
            f"*Внимание*\n\n"
            f"Есть активные заказы: {active_count}\n"
            f"Завершите или отмените их перед остановкой.",
            parse_mode="Markdown",
            reply_markup=p2c_session_status_kb(session_key),
        )
        return

    stats = p2c_session.get_session_stats_text(session_key)
    await p2c_session.stop_session(session_key)

    await message.answer(
        f"*Сессия завершена*\n\n{stats}",
        parse_mode="Markdown",
        reply_markup=main_menu_kb(),
    )


def _find_session_for_chat(chat_id: int) -> str | None:
    for key, config in p2c_session.session_configs.items():
        if config.get("chat_id") == chat_id and p2c_session.is_running(key):
            return key
    return None


# ==========================================
# Callbacks — Аккаунты
# ==========================================

@router.callback_query(F.data == "acc:list")
async def cb_accounts_list(callback: CallbackQuery):
    accounts = get_all_accounts()
    count = len(accounts)
    text = f"*Аккаунты* ({count})" if accounts else "Нет аккаунтов"
    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=accounts_list_kb(accounts),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("acc:") & ~F.data.startswith("acc:add"))
async def cb_account_actions(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")

    if len(parts) == 2 and parts[1].isdigit():
        account_id = int(parts[1])
        acc = get_account(account_id)
        if not acc:
            await callback.answer("Аккаунт не найден", show_alert=True)
            return

        cookies_age = ''
        if acc["cookies_updated_at"]:
            age_hours = (time.time() - acc["cookies_updated_at"]) / 3600
            if age_hours < 1:
                cookies_age = f"{int(age_hours * 60)} мин назад"
            elif age_hours < 24:
                cookies_age = f"{age_hours:.1f} ч назад"
            else:
                cookies_age = f"{age_hours / 24:.1f} дн назад"

        status = "Активен" if acc["is_active"] else "Неактивен"
        token_status = "есть" if acc["access_token"] else "нет"

        text = (
            f"*{acc['name']}*\n\n"
            f"Телефон: `{acc['phone']}`\n"
            f"Статус: {status}\n"
            f"Куки: {cookies_age or 'не установлены'}\n"
            f"Токен: {token_status}"
        )

        try:
            balance = json.loads(acc.get("last_balance_json", "{}"))
            if balance and "total" in balance:
                text += f"\nБаланс: ${balance['total']}"
        except json.JSONDecodeError:
            pass

        await callback.message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=account_detail_kb(account_id, acc["is_active"]),
        )
        await callback.answer()

    elif len(parts) == 3:
        action = parts[1]
        account_id = int(parts[2])

        if action == "balance":
            await callback.answer("Загружаем...")
            try:
                client = SendTGClient(account_id)
                balance_text = client.get_formatted_balance()
                await callback.message.answer(
                    f"Аккаунт #{account_id}\n\n{balance_text}",
                    parse_mode="Markdown",
                )
            except Exception as e:
                await callback.message.answer(f"Ошибка: {e}")

        elif action == "toggle":
            new_state = toggle_account(account_id)
            status = "включён" if new_state else "выключен"
            await callback.answer(f"Аккаунт {status}", show_alert=True)
            acc = get_account(account_id)
            await callback.message.edit_reply_markup(
                reply_markup=account_detail_kb(account_id, acc["is_active"]),
            )

        elif action == "delete":
            acc = get_account(account_id)
            await callback.message.edit_text(
                f"*Удалить аккаунт?*\n\n"
                f"{acc['name']} ({acc['phone']})\n\n"
                f"Это действие необратимо.",
                parse_mode="Markdown",
                reply_markup=confirm_delete_kb(account_id),
            )
            await callback.answer()

        elif action == "confirm_delete":
            keys_to_stop = [
                k for k in p2c_session.active_sessions
                if f"acc{account_id}_" in k
            ]
            for key in keys_to_stop:
                await p2c_session.stop_session(key)

            delete_account(account_id)
            await callback.answer("Удалён", show_alert=True)
            accounts = get_all_accounts()
            await callback.message.edit_text(
                f"*Аккаунты* ({len(accounts)})",
                parse_mode="Markdown",
                reply_markup=accounts_list_kb(accounts),
            )

        elif action == "refresh":
            # Обновление кук — тоже через Playwright
            acc = get_account(account_id)
            if not acc:
                await callback.answer("Не найден", show_alert=True)
                return

            await state.set_state(RefreshCookiesStates.waiting_phone)
            await state.update_data(
                refresh_account_id=account_id,
                refresh_phone=acc["phone"],
            )
            await callback.message.edit_text(
                f"*Обновление кук — {acc['name']}*\n\n"
                f"Текущий номер: `{acc['phone']}`\n\n"
                f"Отправьте номер телефона или нажмите /skip чтобы использовать текущий.",
                parse_mode="Markdown",
            )
            await callback.answer()


# ==========================================
# Обновление кук через Playwright
# ==========================================

@router.message(RefreshCookiesStates.waiting_phone)
async def process_refresh_phone(message: Message, state: FSMContext):
    data = await state.get_data()
    account_id = data["refresh_account_id"]

    if message.text.strip() == "/skip":
        phone = data["refresh_phone"]
    else:
        phone = message.text.strip()
        if not phone.startswith("+") or len(phone) < 10:
            await message.answer("Формат: `+0`", parse_mode="Markdown")
            return

    await state.clear()

    await message.answer(
        f"Запускаем авторизацию для `{phone}`...\n"
        f"Подтвердите вход в Telegram.",
        parse_mode="Markdown",
    )

    from auth import refresh_cookies_playwright, save_cookies_to_file
    try:
        cookies = await refresh_cookies_playwright(phone)
        if cookies:
            update_account_cookies(account_id, cookies)
            save_cookies_to_file(account_id, cookies)
            await message.answer(
                f"Куки обновлены — #{account_id}",
                reply_markup=main_menu_kb(),
            )
        else:
            await message.answer(
                "Не удалось авторизоваться. Попробуйте ещё раз.",
                reply_markup=main_menu_kb(),
            )
    except Exception as e:
        await message.answer(
            f"Ошибка: {e}",
            reply_markup=main_menu_kb(),
        )


# ==========================================
# Добавление аккаунта — всегда Playwright
# ==========================================

@router.callback_query(F.data == "acc:add")
async def cb_add_account(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddAccountStates.waiting_name)
    await callback.message.edit_text(
        "*Новый аккаунт*\n\n"
        "Введите название:",
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(AddAccountStates.waiting_name)
async def process_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if len(name) < 1 or len(name) > 50:
        await message.answer("Название: от 1 до 50 символов.")
        return

    await state.update_data(name=name)
    await state.set_state(AddAccountStates.waiting_phone)
    await message.answer(
        f"Название: *{name}*\n\n"
        f"Введите номер телефона (например `+0`):",
        parse_mode="Markdown",
    )


@router.message(AddAccountStates.waiting_phone)
async def process_phone(message: Message, state: FSMContext):
    phone = message.text.strip()
    if not phone.startswith("+") or len(phone) < 10:
        await message.answer("Формат: `+0`", parse_mode="Markdown")
        return

    data = await state.get_data()
    name = data["name"]
    await state.clear()

    await message.answer(
        f"Запускаем авторизацию для `{phone}`...\n"
        f"Подтвердите вход в Telegram.",
        parse_mode="Markdown",
    )

    from auth import refresh_cookies_playwright, save_cookies_to_file
    try:
        cookies = await refresh_cookies_playwright(phone)
        if cookies:
            account_id = create_account(name, phone, cookies)
            save_cookies_to_file(account_id, cookies)
            await message.answer(
                f"Аккаунт создан — #{account_id}\n"
                f"{name} | {phone}",
                reply_markup=main_menu_kb(),
            )
        else:
            await message.answer(
                "Не удалось авторизоваться. Попробуйте ещё раз.",
                reply_markup=main_menu_kb(),
            )
    except Exception as e:
        await message.answer(
            f"Ошибка: {e}",
            reply_markup=main_menu_kb(),
        )


# ==========================================
# P2C — Настройка и запуск
# ==========================================

@router.callback_query(F.data.startswith("p2c:setup:"))
async def cb_p2c_setup(callback: CallbackQuery, state: FSMContext):
    account_id = int(callback.data.split(":")[2])
    acc = get_account(account_id)

    if not acc:
        await callback.answer("Не найден", show_alert=True)
        return
    if not acc["is_active"]:
        await callback.answer("Аккаунт неактивен", show_alert=True)
        return
    if not acc["access_token"]:
        await callback.answer("Нет токена — обновите куки", show_alert=True)
        return

    await state.set_state(P2CSetupStates.waiting_range)
    await state.update_data(p2c_account_id=account_id)

    await callback.message.edit_text(
        f"*P2C для {acc['name']}*\n\n"
        f"Введите диапазон сумм в рублях:\n\n"
        f"`мин макс` — например `1000 5000`\n"
        f"`мин` — без верхней границы, например `500`\n"
        f"`0` — все ордера",
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(P2CSetupStates.waiting_range)
async def process_range(message: Message, state: FSMContext):
    text = message.text.strip()
    try:
        parts = text.replace(",", "").replace("–", " ").replace("-", " ").split()
        min_a = int(float(parts[0]))
        if len(parts) > 1:
            max_a = int(float(parts[1]))
            if max_a == 0:
                max_a = 0
        else:
            max_a = 0
    except (ValueError, IndexError):
        await message.answer(
            "Не понял. Примеры:\n"
            "`1000 5000` — от 1000 до 5000 ₽\n"
            "`500` — от 500 ₽\n"
            "`0` — все ордера",
            parse_mode="Markdown",
        )
        return

    data = await state.get_data()
    account_id = data["p2c_account_id"]
    await state.clear()

    if min_a == 0 and max_a >= 0:
        range_text = "все ордера"
    elif max_a >= 0:
        range_text = f"от {min_a:,} ₽"
    else:
        range_text = f"{min_a:,} – {max_a:,} ₽"

    await message.answer(
        f"Диапазон: *{range_text}*\n\n"
        f"Сколько заказов одновременно?",
        parse_mode="Markdown",
        reply_markup=p2c_concurrent_kb(account_id, min_a, max_a),
    )


@router.callback_query(F.data.startswith("p2c:go:"))
async def cb_p2c_go(callback: CallbackQuery):
    parts = callback.data.split(":")
    account_id = int(parts[2])
    min_a = int(parts[3])
    max_a = int(parts[4])
    max_concurrent = int(parts[5])

    acc = get_account(account_id)
    if not acc or not acc["is_active"]:
        await callback.answer("Аккаунт недоступен", show_alert=True)
        return

    session_key = f"acc{account_id}_{int(time.time())}"
    max_amount = float("inf") if max_a >= 0 else float(max_a)

    p2c_session.set_bot(bot)

    await p2c_session.start_session(
        session_key=session_key,
        account_id=account_id,
        chat_id=callback.message.chat.id,
        min_amount=float(min_a),
        max_amount=max_amount,
        max_concurrent=max_concurrent,
    )

    if min_a == 0 and max_a >= 0:
        range_text = "все ордера"
    elif max_a >= 0:
        range_text = f"от {min_a:,} ₽"
    else:
        range_text = f"{min_a:,} – {max_a:,} ₽"

    await callback.message.edit_text(
        f"*P2C запускается*\n\n"
        f"Аккаунт: *{acc['name']}*\n"
        f"Диапазон: *{range_text}*\n"
        f"Макс. заказов: *{max_concurrent}*\n\n"
        f"Подключение...",
        parse_mode="Markdown",
    )

    await callback.message.answer(
        "P2C активна.",
        reply_markup=p2c_active_kb(),
    )
    await callback.answer("Запущено")


# ==========================================
# P2C — Управление заказами
# ==========================================

@router.callback_query(F.data.startswith("p2c:complete:"))
async def cb_p2c_complete(callback: CallbackQuery):
    payment_id = int(callback.data.split(":")[2])

    session_key = p2c_session.find_session_by_payment(payment_id)
    if not session_key:
        await callback.answer("Заказ не найден", show_alert=True)
        return

    taken = p2c_session.get_taken_order(session_key, payment_id)
    if not taken:
        await callback.answer("Заказ не найден", show_alert=True)
        return

    if taken.status != "processing":
        await callback.answer(f"Заказ уже {taken.status}", show_alert=True)
        return

    await callback.answer("Отправляем подтверждение...")

    success = await p2c_session.complete_order(session_key, payment_id)

    if success:
        try:
            out_usdt = int(taken.out_amount) / 1e18
        except (ValueError, TypeError):
            out_usdt = 0

        await callback.message.edit_caption(
            caption=(
                f"✓ *Оплачено*\n\n"
                f"{taken.brand_name}\n"
                f"{taken.in_amount:,.2f} {taken.in_asset} → "
                f"{out_usdt:,.2f} {taken.out_asset}\n"
                f"{time.strftime('%H:%M:%S')}"
            ),
            parse_mode="Markdown",
            reply_markup=None,
        )
        await callback.answer("Оплата подтверждена")
    else:
        await callback.answer("Ошибка подтверждения", show_alert=True)


@router.callback_query(F.data.startswith("p2c:cancel:"))
async def cb_p2c_cancel(callback: CallbackQuery):
    payment_id = int(callback.data.split(":")[2])

    session_key = p2c_session.find_session_by_payment(payment_id)
    if not session_key:
        await callback.answer("Заказ не найден", show_alert=True)
        return

    taken = p2c_session.get_taken_order(session_key, payment_id)
    if not taken:
        await callback.answer("Не найден", show_alert=True)
        return

    if taken.status != "processing":
        await callback.answer(f"Заказ уже {taken.status}", show_alert=True)
        return

    await callback.answer("Отменяем...")

    success = await p2c_session.cancel_order(session_key, payment_id)

    if success:
        await callback.message.edit_caption(
            caption=(
                f"✕ *Отменён*\n\n"
                f"{taken.brand_name}\n"
                f"{taken.in_amount:,.2f} {taken.in_asset}\n"
                f"{time.strftime('%H:%M:%S')}"
            ),
            parse_mode="Markdown",
            reply_markup=None,
        )
        await callback.answer("Отменено")
    else:
        await callback.answer("Ошибка отмены", show_alert=True)


# ==========================================
# P2C — Статус и остановка
# ==========================================

@router.callback_query(F.data.startswith("p2c:stats:"))
async def cb_p2c_stats(callback: CallbackQuery):
    session_key = callback.data[10:]

    if session_key not in p2c_session.session_configs:
        await callback.answer("Сессия не найдена", show_alert=True)
        return

    text = p2c_session.get_session_stats_text(session_key)

    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=p2c_session_status_kb(session_key),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("p2c:stop_session:"))
async def cb_p2c_stop_session(callback: CallbackQuery):
    session_key = callback.data[17:]

    active_count = p2c_session.get_active_taken_count(session_key)
    stats_text = p2c_session.get_session_stats_text(session_key)

    await p2c_session.stop_session(session_key)

    warning = ""
    if active_count > 0:
        warning = f"\n\nБыло активных заказов: {active_count}"

    await callback.message.edit_text(
        f"*Сессия завершена*{warning}\n\n{stats_text}",
        parse_mode="Markdown",
    )

    await callback.message.answer(
        "Главное меню",
        reply_markup=main_menu_kb(),
    )
    await callback.answer("Завершена")


@router.callback_query(F.data == "menu:main")
async def cb_main_menu(callback: CallbackQuery):
    await callback.message.delete()
    await callback.message.answer(
        "Главное меню",
        parse_mode="Markdown",
        reply_markup=main_menu_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()


# ==========================================
# Запуск
# ==========================================

async def main():
    init_db()
    logger.info("DB initialized")

    dp.include_router(router)

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Bot started")
    logger.info(f"ADMIN_IDS: {ADMIN_IDS}")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())