from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)


def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Аккаунты"), KeyboardButton(text="Статистика")],
        ],
        resize_keyboard=True,
    )


def p2c_active_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Статус"), KeyboardButton(text="Остановить")],
            [KeyboardButton(text="Аккаунты")],
        ],
        resize_keyboard=True,
    )


def accounts_list_kb(accounts: list) -> InlineKeyboardMarkup:
    buttons = []
    for acc in accounts:
        dot = "🟢" if acc["is_active"] else "⚫"
        buttons.append([
            InlineKeyboardButton(
                text=f"{dot} {acc['name']}",
                callback_data=f"acc:{acc['id']}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(text="＋ Добавить", callback_data="acc:add"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def account_detail_kb(account_id: int, is_active: bool) -> InlineKeyboardMarkup:
    toggle_text = "Выключить" if is_active else "Включить"

    rows = [
        [
            InlineKeyboardButton(
                text="Баланс",
                callback_data=f"acc:balance:{account_id}",
            ),
            InlineKeyboardButton(
                text="Обновить куки",
                callback_data=f"acc:refresh:{account_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                text=toggle_text,
                callback_data=f"acc:toggle:{account_id}",
            ),
            InlineKeyboardButton(
                text="Удалить",
                callback_data=f"acc:delete:{account_id}",
            ),
        ],
    ]

    if is_active:
        rows.append([
            InlineKeyboardButton(
                text="▶ Запустить P2C",
                callback_data=f"p2c:setup:{account_id}",
            ),
        ])

    rows.append([
        InlineKeyboardButton(text="← Назад", callback_data="acc:list"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_delete_kb(account_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="Да, удалить",
                callback_data=f"acc:confirm_delete:{account_id}",
            ),
            InlineKeyboardButton(
                text="Отмена",
                callback_data=f"acc:{account_id}",
            ),
        ],
    ])


def p2c_concurrent_kb(account_id: int, min_a: int, max_a: int) -> InlineKeyboardMarkup:
    buttons = []
    for i in range(1, 6):
        label = f"{i} заказ" if i == 1 else f"{i} заказа" if i < 5 else f"{i} заказов"
        buttons.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"p2c:go:{account_id}:{min_a}:{max_a}:{i}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="← Назад",
            callback_data=f"acc:{account_id}",
        ),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def taken_order_kb(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✓ Оплачено",
                callback_data=f"p2c:complete:{payment_id}",
            ),
            InlineKeyboardButton(
                text="✕ Отменить",
                callback_data=f"p2c:cancel:{payment_id}",
            ),
        ],
    ])


def p2c_session_status_kb(session_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="Обновить",
                callback_data=f"p2c:stats:{session_key}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="■ Остановить",
                callback_data=f"p2c:stop_session:{session_key}",
            ),
        ],
    ])