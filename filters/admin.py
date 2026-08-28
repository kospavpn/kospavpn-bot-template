"""
Фильтр для защиты админ-панели.
Применяется на весь роутер admin_private — 
все хэндлеры в нём будут доступны ТОЛЬКО админу.
"""

from aiogram.filters import BaseFilter
from aiogram.types import Message, CallbackQuery, InlineQuery

import os

# ========== ВЛАДЕЛЕЦ (не удаляется и всегда имеет доступ) ==========
ADMIN_ID = int(os.environ.get("ADMIN_ID", "123456789"))
# ================================================================

# Дополнительные админы из БД загружаются при старте бота (app.py)
# и обновляются через раздел «Управление админами».
ADMIN_IDS = {ADMIN_ID}


def add_admin_id(user_id: int):
    ADMIN_IDS.add(int(user_id))


def remove_admin_id(user_id: int):
    if int(user_id) != ADMIN_ID:
        ADMIN_IDS.discard(int(user_id))


class IsAdmin(BaseFilter):
    """
    Пропускает только сообщения/callback от администратора.
    Для остальных — молча игнорирует (или можно добавить ответ).
    """
    async def __call__(self, event: Message | CallbackQuery | InlineQuery) -> bool:
        user_id = event.from_user.id
        return user_id in ADMIN_IDS


class IsAdminWithAlert(BaseFilter):
    """
    Версия с ответом "Нет доступа" для callback'ов.
    Используй, если хочешь показать алерт обычным юзерам.
    """
    async def __call__(self, event: Message | CallbackQuery | InlineQuery) -> bool:
        user_id = event.from_user.id
        if user_id not in ADMIN_IDS:
            if isinstance(event, CallbackQuery):
                await event.answer("⛔ Доступ запрещён!", show_alert=True)
            elif isinstance(event, Message):
                await event.answer("⛔ У тебя нет доступа к админ-панели.")
            return False
        return True
