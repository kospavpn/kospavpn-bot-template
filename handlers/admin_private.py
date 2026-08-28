"""
Админ-панель Kospavpn в Telegram
Полностью защищена — только ADMIN_ID имеет доступ
Интегрирована с реальными данными 3x-UI (xui.py)
"""

from datetime import datetime, timedelta

from aiogram import F, Router, types, Bot
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from sqlalchemy.ext.asyncio import AsyncSession

from database.orm_query import (
    orm_change_banner_image,
    get_users_total,
    get_users_active,
    get_users_new,
    get_users_expired,
    get_users_blocked,
    get_users_paginated,
    get_users_count_filtered,
    get_user_by_user_id,
    toggle_user_block,
    extend_user_subscription,
    get_all_user_ids,
    get_active_user_ids,
    get_payments_paginated,
    get_payments_count,
    get_payments_summary,
    get_user_payments,
    get_user_payments_count,
    reset_user_frozen_days,
    seed_tariffs,
    get_all_tariffs,
    update_tariff,
    orm_create_promo,
    get_all_promos,
    toggle_promo,
    delete_promo,
    get_setting,
    set_setting,
    get_admin_users,
    add_admin_user,
    remove_admin_user,
)

from filters.chat_types import ChatTypeFilter, IsAdmin
from filters.admin import ADMIN_ID, add_admin_id, remove_admin_id

from handlers.user_private import refresh_tariffs_cache, get_tariffs, activate_subscription_by_user_id

from kbds.inline import (
    admin_main_menu,
    admin_stats_menu,
    admin_user_card,
    admin_paginator,
    admin_broadcast_confirm,
    admin_payments_list,
    admin_user_payments_keyboard,
    admin_tariffs_menu,
    admin_tariff_edit,
    admin_promos_menu,
    admin_promo_edit,
    admin_admins_menu,
    admin_logs_menu,
    build_grant_keyboard,
)

# Интеграция с 3x-UI
from services.xui import (
    set_client_enable,
    get_server_info,
    restart_server,
    get_client_stats,
    get_online_clients,
    disable_client_subscription,
    create_or_update_vpn_client,
)

admin_router = Router()
admin_router.message.filter(ChatTypeFilter(["private"]), IsAdmin())
admin_router.callback_query.filter(IsAdmin())


# ========== FSM Состояния ==========
class AdminFSM(StatesGroup):
    search_user = State()
    broadcast_text = State()
    broadcast_confirm = State()
    waiting_for_picture = State()
    extend_custom_days = State()
    tariff_price = State()
    tariff_days = State()
    promo_code = State()
    promo_value = State()
    promo_maxuses = State()
    admin_add_id = State()
    ref_bonus_edit = State()
    grant_id = State()
    grant_days = State()


PAYMENTS_PER_PAGE = 10

METHOD_LABELS = {"sbp": "СБП", "crypto": "Крипта"}

LOG_FILE = "/path/to/project/bot.log"


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n)) % 100
    if 11 <= n <= 19:
        return many
    d = n % 10
    if d == 1:
        return one
    if 2 <= d <= 4:
        return few
    return many


def _fmt_amount(amount) -> str:
    value = float(amount)
    if value == int(value):
        return f"{int(value)} ₽"
    return f"{value:.2f} ₽".replace(".", ",")


# ========== ГЛАВНОЕ МЕНЮ ==========

@admin_router.message(Command("admin"))
async def cmd_admin(message: types.Message):
    await message.answer(
        "*🔧 Админ-панель Kospavpn*\n\n"
        "Выбери раздел:",
        reply_markup=admin_main_menu(),
        parse_mode="Markdown"
    )


@admin_router.callback_query(F.data == "admin:back")
async def admin_back(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "*🔧 Админ-панель Kospavpn*\n\nВыбери раздел:",
        reply_markup=admin_main_menu(),
        parse_mode="Markdown"
    )


# ========== СТАТИСТИКА ==========

@admin_router.callback_query(F.data == "admin:stats")
async def admin_stats(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "*📊 Статистика*\n\nВыбери период:",
        reply_markup=admin_stats_menu(),
        parse_mode="Markdown"
    )


@admin_router.callback_query(F.data.startswith("admin:stats:"))
async def admin_stats_period(callback: types.CallbackQuery, session: AsyncSession):
    days = int(callback.data.split(":")[2])

    total = await get_users_total(session)
    active = await get_users_active(session)
    new = await get_users_new(session, days=days)
    expired = await get_users_expired(session)
    blocked = await get_users_blocked(session)

    server = await get_server_info()
    server_status = "🟢 Online" if server["status"] == "online" else "🔴 Offline"

    period_text = "сутки" if days == 1 else f"{days} дней"

    text = (
        f"*📊 Статистика за {period_text}*\n\n"
        f"*👤 Пользователи:*\n"
        f"  • Всего: `{total}`\n"
        f"  • Активные: `{active}`\n"
        f"  • Новые за период: `{new}`\n"
        f"  • Просроченные: `{expired}`\n"
        f"  • Заблокированные: `{blocked}`\n\n"
        f"*🖥 Сервер:*\n"
        f"  • Название: `{server['name']}`\n"
        f"  • Статус: {server_status}\n"
        f"  • Клиентов: `{server['active_users']}/{server['total_users']}`\n"
        f"  • Трафик: `{server['traffic_gb']} GB`\n"
        f"  • Загрузка: `{server['load']}%`"
    )

    await callback.message.edit_text(text, reply_markup=admin_stats_menu(), parse_mode="Markdown")


# ========== ПОЛЬЗОВАТЕЛИ ==========

USERS_PER_PAGE = 5

@admin_router.callback_query(F.data.startswith("admin:users:page:"))
async def admin_users_list(callback: types.CallbackQuery, session: AsyncSession):
    parts = callback.data.split(":")
    page = int(parts[3])
    filter_type = parts[4] if len(parts) > 4 else "all"

    total = await get_users_count_filtered(session, filter_type)
    total_pages = (total + USERS_PER_PAGE - 1) // USERS_PER_PAGE
    offset = page * USERS_PER_PAGE

    users = await get_users_paginated(session, offset=offset, limit=USERS_PER_PAGE, filter_type=filter_type)

    if not users:
        await callback.answer("Пользователи не найдены", show_alert=True)
        return

    # Получаем список online клиентов из XUI
    try:
        online_emails = await get_online_clients()
    except Exception:
        online_emails = []

    text = f"*👤 Пользователи* (фильтр: {filter_type})\n\n"
    for u in users:
        is_expired = u.subscription_end and u.subscription_end < datetime.utcnow()
        status = "🟢" if u.is_active and not is_expired else "🔴"

        # Проверяем online статус
        client_email = f"user_{u.user_id}"
        online_status = "🌐 " if client_email in online_emails else ""

        exp = u.subscription_end.strftime("%d.%m.%Y") if u.subscription_end else "—"
        name = u.first_name or u.user_id
        text += f"{status} {online_status}*#{u.user_id}* | {name}\n   📅 {exp}\n\n"

    paginator = admin_paginator(page, total_pages, "admin:users:page", filter_type)

    await callback.message.edit_text(
        text,
        reply_markup=paginator,
        parse_mode="Markdown"
    )


@admin_router.callback_query(F.data.startswith("admin:user:extend:"))
async def admin_user_extend(callback: types.CallbackQuery, session: AsyncSession):
    _, _, _, user_id, days = callback.data.split(":")
    user = await extend_user_subscription(session, int(user_id), int(days))
    if user:
        try:
            await set_client_enable(int(user_id), enable=True)
        except Exception as e:
            print(f"⚠️ Ошибка включения клиента в XUI: {e}")
        await callback.answer(f"✅ Подписка продлена на {days} дней!")
        await show_user_card(callback, session, int(user_id))
    else:
        await callback.answer("❌ Пользователь не найден")


@admin_router.callback_query(F.data.startswith("admin:user:block:"))
async def admin_user_block(callback: types.CallbackQuery, session: AsyncSession):
    user_id = int(callback.data.split(":")[3])
    user = await toggle_user_block(session, user_id)
    if user:
        status_text = "заблокирован" if not user.is_active else "разблокирован"
        try:
            await set_client_enable(user_id, enable=user.is_active)
        except Exception as e:
            print(f"⚠️ Ошибка управления клиентом в XUI: {e}")
        await callback.answer(f"✅ Пользователь {status_text}")
        await show_user_card(callback, session, user_id)
    else:
        await callback.answer("❌ Пользователь не найден")


@admin_router.callback_query(F.data.startswith("admin:user:disable:"))
async def admin_user_disable(callback: types.CallbackQuery, session: AsyncSession):
    """Ручное отключение подписки пользователя"""
    user_id = int(callback.data.split(":")[3])
    user = await get_user_by_user_id(session, user_id)
    if not user:
        return await callback.answer("❌ Пользователь не найден")

    # Отключаем в БД
    user.is_active = False
    if user.subscription_end:
        user.subscription_end = datetime.utcnow()
    await session.commit()

    # Отключаем в XUI
    try:
        await disable_client_subscription(user_id)
    except Exception as e:
        print(f"⚠️ Ошибка отключения в XUI: {e}")

    await callback.answer("❌ Подписка отключена")
    await show_user_card(callback, session, user_id)


def _render_user_card(user, title: str | None = None) -> str:
    is_expired = user.subscription_end and user.subscription_end < datetime.utcnow()
    status = "🟢 Активен" if user.is_active and not is_expired else "🔴 Неактивен"
    exp = user.subscription_end.strftime("%d.%m.%Y %H:%M") if user.subscription_end else "—"
    plan = user.subscription_plan or "—"
    phone = user.phone or "—"
    created = user.created.strftime("%d.%m.%Y") if user.created else "—"
    frozen = user.frozen_days or 0
    frozen_text = f"{frozen} дн." if frozen else "нет"

    return (
        f"*👤 {title or f"Карточка пользователя #{user.user_id}"}*\n\n"
        f"🆔 Telegram ID: `{user.user_id}`\n"
        f"👤 Имя: `{user.first_name or "—"}`\n"
        f"📱 Телефон: `{phone}`\n"
        f"📅 Подписка до: `{exp}`\n"
        f"📦 Тариф: `{plan}`\n"
        f"📊 Статус: *{status}*\n"
        f"🧊 Заморозка: `{frozen_text}`\n"
        f"🕐 Регистрация: `{created}`"
    )


async def show_user_card(callback: types.CallbackQuery, session: AsyncSession, user_id: int):
    user = await get_user_by_user_id(session, user_id)
    if not user:
        return await callback.answer("Пользователь не найден")

    # Проверяем online статус
    try:
        online_emails = await get_online_clients()
        client_email = f"user_{user.user_id}"
        is_online = client_email in online_emails
        online_text = "\n🌍 Сеть: *🌐 В сети*" if is_online else ""
    except Exception:
        online_text = ""

    text = _render_user_card(user) + online_text

    await callback.message.edit_text(
        text,
        reply_markup=admin_user_card(user.user_id, user.is_active, user.frozen_days or 0),
        parse_mode="Markdown"
    )


@admin_router.callback_query(F.data.startswith("admin:user:card:"))
async def admin_user_card_back(callback: types.CallbackQuery, session: AsyncSession):
    user_id = int(callback.data.split(":")[3])
    await callback.answer()
    try:
        await show_user_card(callback, session, user_id)
    except Exception:
        pass


# ========== ПОИСК ПОЛЬЗОВАТЕЛЯ ==========

@admin_router.callback_query(F.data == "admin:search")
async def admin_search_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "*🔍 Поиск пользователя*\n\n"
        "Введи Telegram ID пользователя:",
        parse_mode="Markdown"
    )
    await state.set_state(AdminFSM.search_user)


@admin_router.message(AdminFSM.search_user)
async def admin_search_process(message: types.Message, session: AsyncSession, state: FSMContext):
    query = message.text.strip()
    user = None

    if query.isdigit():
        user = await get_user_by_user_id(session, int(query))

    if user:
        text = _render_user_card(user, title=f"Найден пользователь #{user.user_id}")
        await message.answer(
            text,
            reply_markup=admin_user_card(user.user_id, user.is_active, user.frozen_days or 0),
            parse_mode="Markdown"
        )
    else:
        await message.answer("❌ Пользователь не найден. Попробуй снова через /admin")

    await state.clear()


# ========== ИСТОРИЯ ПЛАТЕЖЕЙ ==========

@admin_router.callback_query(F.data == "admin:payments")
async def admin_payments_summary(callback: types.CallbackQuery, session: AsyncSession):
    summary = await get_payments_summary(session)
    today_c, today_s = summary["today"]
    d7_c, d7_s = summary["7d"]
    d30_c, d30_s = summary["30d"]
    all_c, all_s = summary["all"]

    word = _plural(all_c, "платёж", "платежа", "платежей")

    text = (
        "*💰 История платежей*\n\n"
        f"*За 24 часа:* `{today_c}` — {_fmt_amount(today_s)}\n"
        f"*За 7 дней:* `{d7_c}` — {_fmt_amount(d7_s)}\n"
        f"*За 30 дней:* `{d30_c}` — {_fmt_amount(d30_s)}\n"
        f"*Всего:* `{all_c}` {word} — {_fmt_amount(all_s)}"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Список платежей", callback_data="admin:payments:list:0")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:back")],
    ])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")


@admin_router.callback_query(F.data.startswith("admin:payments:list:"))
async def admin_payments_list_page(callback: types.CallbackQuery, session: AsyncSession):
    page = max(0, int(callback.data.split(":")[3]))
    total = await get_payments_count(session)
    total_pages = max(1, (total + PAYMENTS_PER_PAGE - 1) // PAYMENTS_PER_PAGE)
    page = min(page, total_pages - 1)

    payments = await get_payments_paginated(session, offset=page * PAYMENTS_PER_PAGE, limit=PAYMENTS_PER_PAGE)
    if not payments:
        await callback.answer("Платежей пока нет", show_alert=True)
        return

    text = f"*💰 Платежи* · всего `{total}`\n\n"
    for p in payments:
        dt = p.created.strftime("%d.%m.%y %H:%M") if p.created else "—"
        method = METHOD_LABELS.get(p.method, "—")
        text += f"`{dt}` · #{p.user_id} · {p.plan} · *{_fmt_amount(p.amount)}* · {method}\n"

    await callback.message.edit_text(
        text,
        reply_markup=admin_payments_list(page, total_pages),
        parse_mode="Markdown"
    )


# ========== ПЛАТЕЖИ КОНКРЕТНОГО ЮЗЕРА ==========

@admin_router.callback_query(F.data.startswith("admin:user:payments:"))
async def admin_user_payments(callback: types.CallbackQuery, session: AsyncSession):
    _, _, _, user_id_str, page_str = callback.data.split(":")
    user_id, page = int(user_id_str), max(0, int(page_str))

    total = await get_user_payments_count(session, user_id)
    if not total:
        await callback.answer("У юзера нет платежей", show_alert=True)
        return
    total_pages = max(1, (total + 4) // 5)  # по 5 на страницу
    page = min(page, total_pages - 1)

    payments = await get_user_payments(session, user_id, offset=page * 5, limit=5)

    word = _plural(total, "платёж", "платежа", "платежей")
    text = f"*💳 Платежи юзера #{user_id}* · `{total}` {word}\n\n"
    for p in payments:
        dt = p.created.strftime("%d.%m.%y %H:%M") if p.created else "—"
        method = METHOD_LABELS.get(p.method, "—")
        status = "✅" if p.status == "CONFIRMED" else f"⚠️ {p.status}"
        text += f"`{dt}` · {p.plan} · *{_fmt_amount(p.amount)}* · {method} {status}\n"

    await callback.message.edit_text(
        text,
        reply_markup=admin_user_payments_keyboard(user_id, page, total_pages),
        parse_mode="Markdown"
    )


# ========== ПРОДЛЕНИЕ НА СВОЁ ЧИСЛО ДНЕЙ ==========

@admin_router.callback_query(F.data.startswith("admin:user:extendcustom:"))
async def admin_extend_custom_start(callback: types.CallbackQuery, state: FSMContext):
    user_id = int(callback.data.split(":")[3])
    await state.set_state(AdminFSM.extend_custom_days)
    await state.update_data(extend_user_id=user_id)
    await callback.message.edit_text(
        f"*➕ Продление подписки*\n\n"
        f"Введи количество дней для юзера `{user_id}`\n\n"
        f"_Для отмены: /cancel_",
        parse_mode="Markdown"
    )


@admin_router.message(Command("cancel"), StateFilter(AdminFSM.extend_custom_days))
async def admin_extend_custom_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("*❌ Отменено.* Открой карточку заново через поиск.", parse_mode="Markdown")


@admin_router.message(AdminFSM.extend_custom_days)
async def admin_extend_custom_process(message: types.Message, session: AsyncSession, state: FSMContext):
    raw = (message.text or "").strip()
    if not raw.isdigit() or not (1 <= int(raw) <= 3650):
        await message.answer(
            "⚠️ Нужно целое число от 1 до 3650. Попробуй ещё раз или /cancel",
            parse_mode="Markdown"
        )
        return

    data = await state.get_data()
    user_id = data["extend_user_id"]
    days = int(raw)
    await state.clear()

    user = await extend_user_subscription(session, user_id, days)
    if not user:
        await message.answer("❌ Пользователь не найден")
        return
    try:
        await set_client_enable(int(user_id), enable=True)
    except Exception as e:
        print(f"⚠️ Ошибка включения клиента в XUI: {e}")

    exp = user.subscription_end.strftime("%d.%m.%Y %H:%M") if user.subscription_end else "—"
    await message.answer(
        f"✅ Подписка юзера `{user_id}` продлена на *{days}* {_plural(days, 'день', 'дня', 'дней')}.\n"
        f"📅 Теперь активна до: `{exp}`",
        parse_mode="Markdown"
    )


# ========== ОБНУЛЕНИЕ ЗАМОРОЗКИ ==========

@admin_router.callback_query(F.data.startswith("admin:user:frozenreset:"))
async def admin_frozen_reset(callback: types.CallbackQuery, session: AsyncSession):
    user_id = int(callback.data.split(":")[3])
    user = await reset_user_frozen_days(session, user_id)
    if not user:
        return await callback.answer("❌ Пользователь не найден", show_alert=True)
    await callback.answer("🧊 Замороженные дни обнулены")
    try:
        await show_user_card(callback, session, user_id)
    except Exception:
        pass


# ========== СЕРВЕРЫ (РЕАЛЬНЫЕ ДАННЫЕ ИЗ 3x-UI) ==========

@admin_router.callback_query(F.data == "admin:servers")
async def admin_servers(callback: types.CallbackQuery):
    server = await get_server_info()
    status_emoji = "🟢" if server["status"] == "online" else "🔴"

    text = (
        f"*🖥 Сервер {server['name']}*\n\n"
        f"{status_emoji} *Статус:* `{server['status'].upper()}`\n"
        f"🔌 *Порт:* `{server['port']}`\n"
        f"📡 *Протокол:* `{server['protocol']}`\n"
        f"👥 *Клиентов:* `{server['active_users']}` активных / `{server['total_users']}` всего\n"
        f"📊 *Загрузка:* `{server['load']}%`\n"
        f"📈 *Трафик:* `{server['traffic_gb']} GB`\n\n"
        f"_Данные получены из 3x-UI API_"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить данные", callback_data="admin:servers")],
        [InlineKeyboardButton(text="👥 Кто в сети", callback_data="admin:server:online")],
        [InlineKeyboardButton(text="🔄 Перезапустить Xray", callback_data="admin:server:restart")],
        [InlineKeyboardButton(text="📊 Статистика клиентов", callback_data="admin:server:clients")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:back")],
    ])

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")


@admin_router.callback_query(F.data == "admin:server:online")
async def admin_server_online(callback: types.CallbackQuery):
    """Показывает список клиентов, которые сейчас в сети"""
    await callback.answer("⏳ Получаю список online клиентов...")

    try:
        online_emails = await get_online_clients()
    except Exception as e:
        await callback.message.edit_text(
            f"❌ *Ошибка получения online статуса*\n\n`{e}`",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Повторить", callback_data="admin:server:online")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:servers")],
            ]),
            parse_mode="Markdown"
        )
        return

    if not online_emails:
        await callback.message.edit_text(
            "👥 *Кто в сети*\n\n"
            "Сейчас никого нет в сети.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:server:online")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:servers")],
            ]),
            parse_mode="Markdown"
        )
        return

    text = f"👥 *Клиенты в сети ({len(online_emails)}):*\n\n"
    for email in online_emails[:20]:  # Показываем первые 20
        # Убираем префикс user_ для читаемости
        display = email.replace("user_", "")
        text += f"🟢 `{display}`\n"

    if len(online_emails) > 20:
        text += f"\n_...и ещё {len(online_emails) - 20}_"

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:server:online")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:servers")],
        ]),
        parse_mode="Markdown"
    )


@admin_router.callback_query(F.data == "admin:server:restart")
async def admin_server_restart(callback: types.CallbackQuery):
    await callback.answer("⏳ Перезапускаю Xray...")
    success = await restart_server()
    if success:
        await callback.message.edit_text(
            "✅ *Xray успешно перезапущен!*\n\n"
            "Сервер обновлён.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🖥 К серверам", callback_data="admin:servers")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:back")],
            ]),
            parse_mode="Markdown"
        )
    else:
        await callback.message.edit_text(
            "❌ *Не удалось перезапустить Xray*\n\n"
            "Проверь логи сервера.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🖥 К серверам", callback_data="admin:servers")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:back")],
            ]),
            parse_mode="Markdown"
        )


@admin_router.callback_query(F.data == "admin:server:clients")
async def admin_server_clients(callback: types.CallbackQuery):
    stats = await get_client_stats()

    if not stats:
        await callback.message.edit_text(
            "📊 *Статистика клиентов*\n\n"
            "Нет данных или произошла ошибка.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:server:clients")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:back")],
            ]),
            parse_mode="Markdown"
        )
        return

    text = "📊 *Топ клиентов по трафику*\n\n"
    sorted_stats = sorted(stats, key=lambda x: x["total_gb"], reverse=True)[:10]

    for i, s in enumerate(sorted_stats, 1):
        status = "🟢" if s["enabled"] else "🔴"
        text += f"{status} *{s['email']}*\n   ⬆️ `{s['up_gb']} GB` ⬇️ `{s['down_gb']} GB`\n"

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:server:clients")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:back")],
        ]),
        parse_mode="Markdown"
    )


# ========== РАССЫЛКА ==========

@admin_router.callback_query(F.data == "admin:broadcast")
async def admin_broadcast_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "*📢 Рассылка*\n\n"
        "Введи текст сообщения для отправки:",
        parse_mode="Markdown"
    )
    await state.set_state(AdminFSM.broadcast_text)


@admin_router.message(AdminFSM.broadcast_text)
async def admin_broadcast_text(message: types.Message, state: FSMContext):
    text = message.text
    await state.update_data(text=text)
    await message.answer(
        "*📨 Предпросмотр сообщения:*\n\n"
        f"{text}\n\n"
        "Кому отправить?",
        reply_markup=admin_broadcast_confirm(),
        parse_mode="Markdown"
    )
    await state.set_state(AdminFSM.broadcast_confirm)


@admin_router.callback_query(F.data.startswith("admin:broadcast:"), StateFilter(AdminFSM.broadcast_confirm))
async def admin_broadcast_send(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot):
    data = await state.get_data()
    text = data.get("text", "")
    target = callback.data.split(":")[2]

    await callback.answer("⏳ Рассылка запущена...")

    if target == "all":
        user_ids = await get_all_user_ids(session)
    else:
        user_ids = await get_active_user_ids(session)

    sent = 0
    failed = 0

    for user_id in user_ids:
        try:
            await bot.send_message(user_id, text, parse_mode="Markdown")
            sent += 1
        except Exception:
            failed += 1

    await callback.message.edit_text(
        f"*✅ Рассылка завершена*\n\n"
        f"📨 Отправлено: `{sent}`\n"
        f"❌ Ошибок: `{failed}`",
        reply_markup=admin_main_menu(),
        parse_mode="Markdown"
    )
    await state.clear()


# ========== КАРТИНКА ГЛАВНОГО МЕНЮ ==========

@admin_router.message(Command("picture"))
async def cmd_picture(message: types.Message, state: FSMContext):
    await message.answer(
        "*🖼 Загрузка картинки*\n\n"
        "Отправь фото, которое будет показываться в главном меню бота.\n\n"
        "Для отмены: /cancel",
        parse_mode="Markdown"
    )
    await state.set_state(AdminFSM.waiting_for_picture)


@admin_router.message(Command("cancel"), StateFilter(AdminFSM.waiting_for_picture))
async def cmd_picture_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("*❌ Отменено.*", parse_mode="Markdown")


@admin_router.message(AdminFSM.waiting_for_picture, F.photo)
async def admin_picture_save(message: types.Message, session: AsyncSession, state: FSMContext):
    photo = message.photo[-1]  # самое большое разрешение
    try:
        await orm_change_banner_image(session, "main", photo.file_id)
    except Exception:
        # Баннера может не быть в БД — создаём
        from database.models import Banner
        session.add(Banner(name="main", description="Добро пожаловать!", image=photo.file_id))
        await session.commit()
    await state.clear()
    await message.answer_photo(
        photo=photo.file_id,
        caption="*✅ Картинка сохранена! Теперь она будет в главном меню.*",
        parse_mode="Markdown"
    )


@admin_router.message(AdminFSM.waiting_for_picture)
async def admin_picture_wrong(message: types.Message, state: FSMContext):
    await message.answer(
        "*⚠️ Это не фото.* Отправь именно картинку (фото), либо /cancel для отмены.",
        parse_mode="Markdown"
    )


# ========== ОБЩАЯ ОТМЕНА FSM ==========

@admin_router.message(Command("cancel"), StateFilter(
    AdminFSM.tariff_price, AdminFSM.tariff_days,
    AdminFSM.promo_code, AdminFSM.promo_value, AdminFSM.promo_maxuses,
    AdminFSM.admin_add_id, AdminFSM.ref_bonus_edit,
    AdminFSM.grant_id, AdminFSM.grant_days
))
async def admin_fsm_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("*❌ Отменено.*", parse_mode="Markdown")


# ========== ТАРИФЫ ==========

@admin_router.callback_query(F.data == "admin:tariffs")
async def admin_tariffs_list(callback: types.CallbackQuery, session: AsyncSession):
    tariffs = await get_all_tariffs(session)
    if not tariffs:
        await seed_tariffs(session)
        tariffs = await get_all_tariffs(session)
    text = (
        "*🗂 Тарифы*\n\n"
        "Нажми на тариф, чтобы посмотреть детали и изменить цену/срок.\n"
        "Изменения применяются сразу — перезапуск бота не нужен."
    )
    try:
        await callback.message.edit_text(text, reply_markup=admin_tariffs_menu(tariffs), parse_mode="Markdown")
    except Exception:
        pass
    await callback.answer()


# Регистрируется РАНЬШЕ admin:tariff:{id} из-за общего префикса
@admin_router.callback_query(F.data.startswith("admin:tariff:edit:"))
async def admin_tariff_edit_start(callback: types.CallbackQuery, state: FSMContext):
    tariff_id = int(callback.data.split(":")[3])
    await state.set_state(AdminFSM.tariff_price)
    await state.update_data(tariff_id=tariff_id)
    await callback.message.edit_text(
        "*✏️ Изменение тарифа*\n\nВведи новую цену в рублях (число):\n\n_Для отмены: /cancel_",
        parse_mode="Markdown"
    )


@admin_router.callback_query(F.data.startswith("admin:tariff:"))
async def admin_tariff_detail(callback: types.CallbackQuery, session: AsyncSession):
    tariff_id = int(callback.data.split(":")[2])
    tariffs = await get_all_tariffs(session)
    tariff = next((t for t in tariffs if t.id == tariff_id), None)
    if not tariff:
        return await callback.answer("Тариф не найден", show_alert=True)
    status = "активен" if tariff.is_active else "отключён"
    price_s = int(tariff.price) if float(tariff.price) == int(tariff.price) else float(tariff.price)
    text = (
        f"*🗂 Тариф «{tariff.name}»*\n\n"
        f"💰 Цена: `{price_s} ₽`\n"
        f"📅 Срок: `{tariff.days} дн.`\n"
        f"📊 Статус: `{status}`\n\n"
        "_Изменение цены/срока сразу применится у юзеров._"
    )
    try:
        await callback.message.edit_text(text, reply_markup=admin_tariff_edit(tariff.id), parse_mode="Markdown")
    except Exception:
        pass
    await callback.answer()


@admin_router.message(AdminFSM.tariff_price)
async def admin_tariff_price(message: types.Message, state: FSMContext):
    raw = (message.text or "").strip().replace(",", ".")
    try:
        price = float(raw)
        if price < 0:
            raise ValueError
    except ValueError:
        return await message.answer("⚠️ Введи число ≥ 0. Например: 50 или 149.90", parse_mode="Markdown")
    await state.update_data(price=round(price, 2))
    await state.set_state(AdminFSM.tariff_days)
    await message.answer(
        "📅 Теперь введи срок в днях (целое число от 1 до 3650):\n\n_Для отмены: /cancel_",
        parse_mode="Markdown"
    )


@admin_router.message(AdminFSM.tariff_days)
async def admin_tariff_days(message: types.Message, session: AsyncSession, state: FSMContext):
    raw = (message.text or "").strip()
    if not raw.isdigit() or not (1 <= int(raw) <= 3650):
        return await message.answer("⚠️ Нужно целое число от 1 до 3650.", parse_mode="Markdown")
    data = await state.get_data()
    tariff = await update_tariff(session, data["tariff_id"], data["price"], int(raw))
    await refresh_tariffs_cache(session)
    await state.clear()
    if not tariff:
        return await message.answer("❌ Тариф не найден")
    price_s = int(tariff.price) if float(tariff.price) == int(tariff.price) else float(tariff.price)
    await message.answer(
        f"✅ Тариф *{tariff.name}* обновлён и уже применяется:\n\n💰 {price_s} ₽ · 📅 {tariff.days} дн.",
        parse_mode="Markdown"
    )


# ========== ПРОМОКОДЫ ==========

@admin_router.callback_query(F.data == "admin:promos")
async def admin_promos_list(callback: types.CallbackQuery, session: AsyncSession):
    promos = await get_all_promos(session)
    text = "*🏷 Промокоды*\n\nСоздавай коды на скидку (% или фикс. сумма)." if promos else "*🏷 Промокоды*\n\nПока нет ни одного промокода."
    try:
        await callback.message.edit_text(text, reply_markup=admin_promos_menu(promos), parse_mode="Markdown")
    except Exception:
        pass
    await callback.answer()


@admin_router.callback_query(F.data == "admin:promo:new")
async def admin_promo_new_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminFSM.promo_code)
    await callback.message.edit_text(
        "*➕ Новый промокод*\n\nВведи код (латиница/цифры/`-`/`_`, 3–32 символа):\n\n_Для отмены: /cancel_",
        parse_mode="Markdown"
    )


@admin_router.message(AdminFSM.promo_code)
async def admin_promo_code_input(message: types.Message, session: AsyncSession, state: FSMContext):
    code = (message.text or "").strip()
    if not (3 <= len(code) <= 32) or not all(c.isascii() and (c.isalnum() or c in "-_") for c in code):
        return await message.answer(
            "⚠️ Код должен быть из латинских букв, цифр, `-` или `_` и длиной 3–32 символа. Попробуй ещё раз:",
            parse_mode="Markdown"
        )
    if await get_promo_by_code(session, code):
        return await message.answer("⚠️ Такой промокод уже существует. Введи другой код:", parse_mode="Markdown")
    await state.update_data(code=code.upper())
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="% процент", callback_data="admin:promo:type:percent"),
         InlineKeyboardButton(text="₽ фикс. сумма", callback_data="admin:promo:type:fixed")],
    ])
    await message.answer("Выбери тип скидки:", reply_markup=kb)


@admin_router.callback_query(F.data.startswith("admin:promo:type:"))
async def admin_promo_type_chosen(callback: types.CallbackQuery, state: FSMContext):
    dtype = callback.data.split(":")[3]
    data = await state.get_data()
    if "code" not in data:
        return await callback.answer("Начни заново: Промокоды → Создать", show_alert=True)
    await state.update_data(discount_type=dtype)
    await state.set_state(AdminFSM.promo_value)
    hint = "процент скидки (1–100)" if dtype == "percent" else "размер скидки в рублях"
    await callback.message.edit_text(
        f"*🏷 {data['code']}*\n\nВведи {hint} (число):\n\n_Для отмены: /cancel_",
        parse_mode="Markdown"
    )


@admin_router.message(AdminFSM.promo_value)
async def admin_promo_value_input(message: types.Message, state: FSMContext):
    raw = (message.text or "").strip().replace(",", ".")
    try:
        value = float(raw)
        if value <= 0:
            raise ValueError
    except ValueError:
        return await message.answer("⚠️ Введи положительное число.", parse_mode="Markdown")
    data = await state.get_data()
    if data.get("discount_type") == "percent" and value > 100:
        return await message.answer("⚠️ Процент не может быть больше 100.", parse_mode="Markdown")
    await state.update_data(discount_value=value)
    await state.set_state(AdminFSM.promo_maxuses)
    await message.answer(
        "🔢 Сколько раз можно использовать промокод? Введи число (0 — без лимита):\n\n_Для отмены: /cancel_",
        parse_mode="Markdown"
    )


@admin_router.message(AdminFSM.promo_maxuses)
async def admin_promo_maxuses_input(message: types.Message, session: AsyncSession, state: FSMContext):
    raw = (message.text or "").strip()
    if not raw.isdigit():
        return await message.answer("⚠️ Нужно целое число ≥ 0.", parse_mode="Markdown")
    data = await state.get_data()
    promo = await orm_create_promo(
        session,
        code=data["code"],
        discount_type=data["discount_type"],
        discount_value=data["discount_value"],
        max_uses=int(raw),
    )
    await state.clear()
    label = f"-{int(promo.discount_value)}%" if promo.discount_type == "percent" else f"-{int(promo.discount_value)} ₽"
    limit = f"{promo.max_uses} раз" if promo.max_uses else "без лимита"
    await message.answer(
        f"✅ Промокод *{promo.code}* создан ({label}, {limit}).\n\nЮзер вводит его на экране оплаты.",
        parse_mode="Markdown"
    )


# Конкретные действия — регистрируются ДО общего admin:promo:{id}
@admin_router.callback_query(F.data.startswith("admin:promo:toggle:"))
async def admin_promo_toggle(callback: types.CallbackQuery, session: AsyncSession):
    promo = await toggle_promo(session, int(callback.data.split(":")[3]))
    await callback.answer("Включён" if promo and promo.is_active else "Отключён")


@admin_router.callback_query(F.data.startswith("admin:promo:delete:"))
async def admin_promo_delete(callback: types.CallbackQuery, session: AsyncSession):
    promo = await delete_promo(session, int(callback.data.split(":")[3]))
    await callback.answer(f"🗑 {promo.code} удалён" if promo else "Не найден")
    promos = await get_all_promos(session)
    try:
        await callback.message.edit_reply_markup(reply_markup=admin_promos_menu(promos))
    except Exception:
        pass


@admin_router.callback_query(F.data.startswith("admin:promo:"))
async def admin_promo_detail(callback: types.CallbackQuery, session: AsyncSession):
    promo_id = int(callback.data.split(":")[2])
    promo = next((p for p in await get_all_promos(session) if p.id == promo_id), None)
    if not promo:
        return await callback.answer("Промокод не найден", show_alert=True)
    if promo.discount_type == "percent":
        label = f"-{int(promo.discount_value)}%"
    else:
        dv = int(promo.discount_value) if float(promo.discount_value) == int(promo.discount_value) else float(promo.discount_value)
        label = f"-{dv} ₽"
    limit = f"{promo.used_count}/{promo.max_uses}" if promo.max_uses else f"{promo.used_count}/∞"
    status = "✅ активен" if promo.is_active else "⛔ отключён"
    text = (
        f"*🏷 {promo.code}*\n\n"
        f"Скидка: *{label}*\n"
        f"Использован: `{limit}`\n"
        f"Статус: {status}"
    )
    try:
        await callback.message.edit_text(text, reply_markup=admin_promo_edit(promo.id, promo.is_active), parse_mode="Markdown")
    except Exception:
        pass
    await callback.answer()


# ========== УПРАВЛЕНИЕ АДМИНАМИ ==========

@admin_router.callback_query(F.data == "admin:admins")
async def admin_admins_list(callback: types.CallbackQuery, session: AsyncSession):
    admins = await get_admin_users(session)
    names = ", ".join(a.name or str(a.user_id) for a in admins) or "—"
    text = (
        "*👑 Администраторы*\n\n"
        f"Владелец: `{ADMIN_ID}` (не удаляется)\n"
        f"В админке: `{names}`\n\n"
        "Добавленные админы получают полный доступ к панели сразу."
    )
    try:
        await callback.message.edit_text(
            text,
            reply_markup=admin_admins_menu(admins, ADMIN_ID),
            parse_mode="Markdown"
        )
    except Exception:
        pass
    await callback.answer()


@admin_router.callback_query(F.data == "admin:admins:add")
async def admin_admin_add_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminFSM.admin_add_id)
    await callback.message.edit_text(
        "*➕ Добавление админа*\n\n"
        "Введи Telegram ID (можно с именем через пробел):\n"
        "`123456789 Иван`\n\n_Для отмены: /cancel_",
        parse_mode="Markdown"
    )


@admin_router.message(AdminFSM.admin_add_id)
async def admin_admin_add_process(message: types.Message, session: AsyncSession, state: FSMContext):
    parts = (message.text or "").split(maxsplit=1)
    if not parts or not parts[0].lstrip("-").isdigit():
        return await message.answer("⚠️ Первым должно идти число — Telegram ID. Попробуй ещё раз:", parse_mode="Markdown")
    user_id = int(parts[0])
    name = parts[1].strip()[:100] if len(parts) > 1 else None
    admin = await add_admin_user(session, user_id, name)
    if not admin:
        return await message.answer("⚠️ Такой админ уже есть.", parse_mode="Markdown")
    add_admin_id(user_id)
    await state.clear()
    await message.answer(
        f"✅ `{user_id}` теперь админ{f' ({name})' if name else ''}. Доступ выдан немедленно.",
        parse_mode="Markdown"
    )


@admin_router.callback_query(F.data.startswith("admin:admins:remove:"))
async def admin_admin_remove(callback: types.CallbackQuery, session: AsyncSession):
    user_id = int(callback.data.split(":")[3])
    if user_id == ADMIN_ID:
        return await callback.answer("Владельца удалить нельзя", show_alert=True)
    admin = await remove_admin_user(session, user_id)
    remove_admin_id(user_id)
    await callback.answer(f"Удалён: {admin.name or user_id}" if admin else "Не найден")
    admins = await get_admin_users(session)
    try:
        await callback.message.edit_reply_markup(reply_markup=admin_admins_menu(admins, ADMIN_ID))
    except Exception:
        pass


# ========== НАСТРОЙКИ ==========

@admin_router.callback_query(F.data == "admin:settings")
async def admin_settings(callback: types.CallbackQuery, session: AsyncSession):
    ref_days = await get_setting(session, "ref_bonus_days", "7")
    text = (
        "*⚙️ Настройки*\n\n"
        f"🎁 Реферальный бонус: *{ref_days}* дн. — начисляется пригласившему за первую оплату приглашённого.\n"
        "(0 — отключить бонусы)"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Изменить бонус", callback_data="admin:settings:refbonus")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:back")],
    ])
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception:
        pass
    await callback.answer()


@admin_router.callback_query(F.data == "admin:settings:refbonus")
async def admin_refbonus_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminFSM.ref_bonus_edit)
    await callback.message.edit_text(
        "🎁 Введи количество дней бонуса (целое число, 0 — отключить):\n\n_Для отмены: /cancel_",
        parse_mode="Markdown"
    )


@admin_router.message(AdminFSM.ref_bonus_edit)
async def admin_refbonus_set(message: types.Message, session: AsyncSession, state: FSMContext):
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) > 365:
        return await message.answer("⚠️ Целое число от 0 до 365.", parse_mode="Markdown")
    days = int(raw)
    await set_setting(session, "ref_bonus_days", str(days))
    await state.clear()
    text = "отключены" if days == 0 else f"{days} дн."
    await message.answer(f"✅ Реферальный бонус: {text}", parse_mode="Markdown")


# ========== ЛОГИ БОТА ==========

def _read_log_tail(nbytes: int = 8000) -> str | None:
    try:
        with open(LOG_FILE, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - nbytes))
            return f.read().decode("utf-8", errors="ignore")
    except OSError:
        return None


@admin_router.callback_query(F.data == "admin:logs")
async def admin_logs_menu_cb(callback: types.CallbackQuery):
    try:
        await callback.message.edit_text(
            "*📜 Логи бота*\n\nПоследние строки, ошибки и полный файл.",
            reply_markup=admin_logs_menu(),
            parse_mode="Markdown"
        )
    except Exception:
        pass
    await callback.answer()


@admin_router.callback_query(F.data == "admin:logs:tail")
async def admin_logs_tail(callback: types.CallbackQuery):
    await callback.answer("⏳ Читаю лог...")
    data = _read_log_tail()
    if data is None:
        return await callback.message.answer("❌ Файл лога не найден.")
    lines = [l[:250] for l in data.splitlines()][-40:]
    text = "\n".join(lines) if lines else "(пусто)"
    if len(text) > 3500:
        text = text[-3500:]
    await callback.message.answer(f"📄 Последние строки bot.log:\n\n{text}")


@admin_router.callback_query(F.data == "admin:logs:errors")
async def admin_logs_errors(callback: types.CallbackQuery):
    await callback.answer("⏳ Ищу ошибки...")
    data = _read_log_tail(nbytes=60000)
    if data is None:
        return await callback.message.answer("❌ Файл лога не найден.")
    keys = ("Traceback", "ERROR", "Exception", "❌")
    lines = [l[:250] for l in data.splitlines() if any(k in l for k in keys)][-30:]
    if not lines:
        return await callback.message.answer("✅ Ошибок в последних записях нет.")
    text = "\n".join(lines)
    if len(text) > 3500:
        text = text[-3500:]
    await callback.message.answer(f"⚠️ Ошибки (последние):\n\n{text}")


@admin_router.callback_query(F.data == "admin:logs:file")
async def admin_logs_file(callback: types.CallbackQuery, bot: Bot):
    await callback.answer("⏳ Отправляю файл...")
    try:
        await callback.message.answer_document(FSInputFile(LOG_FILE))
    except Exception as e:
        await callback.message.answer(f"❌ Не удалось отправить файл: {e}")


# ========== ВЫДАЧА ПОДПИСКИ ПО ID ==========

def _grant_notify_text(plan_label: str, exp: str, vpn_data) -> str:
    text = (
        f"🎁 *Тебе выдана подписка!* {plan_label}\n"
        f"📅 Активна до: `{exp}`\n"
    )
    if vpn_data:
        text += (
            f"\n🔑 *Твой ключ:*\n"
            f"```\n{vpn_data['config_link']}\n```\n\n"
            f"📧 Клиент: `{vpn_data['email']}`"
        )
    return text


async def _do_grant(session: AsyncSession, user_id: int, plan: str | None, days: int) -> tuple[bool, str, object]:
    """Выдаёт подписку. Возвращает (успех, дата окончания, vpn_data)."""
    from database.orm_query import orm_add_user
    await orm_add_user(session, user_id)
    user = await get_user_by_user_id(session, user_id)
    if not user:
        return False, "—", None

    if user.subscription_end and user.subscription_end > datetime.utcnow():
        user.subscription_end += timedelta(days=days)
    else:
        user.subscription_end = datetime.utcnow() + timedelta(days=days)
    user.is_active = True
    if plan:
        user.subscription_plan = plan
    await session.commit()

    try:
        await set_client_enable(int(user_id), enable=True)
    except Exception as e:
        print(f"⚠️ XUI enable при выдаче подписки: {e}")

    vpn_data = None
    try:
        vpn_data = await create_or_update_vpn_client(user_email=str(user_id), days=days)
    except Exception as e:
        print(f"⚠️ XUI ключ при выдаче подписки: {e}")

    exp = user.subscription_end.strftime("%d.%m.%Y %H:%M")
    return True, exp, vpn_data


@admin_router.callback_query(F.data == "admin:grant")
async def admin_grant_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminFSM.grant_id)
    await callback.message.edit_text(
        "*🎁 Выдача подписки*\n\n"
        "Введи Telegram ID человека, которому выдать подписку:\n\n_Для отмены: /cancel_",
        parse_mode="Markdown"
    )


@admin_router.message(AdminFSM.grant_id)
async def admin_grant_id_input(message: types.Message, state: FSMContext):
    raw = (message.text or "").strip()
    if not raw.isdigit():
        return await message.answer("⚠️ Введи числовой Telegram ID:", parse_mode="Markdown")
    user_id = int(raw)
    await state.set_state(AdminFSM.grant_days)
    await state.update_data(grant_target=user_id)
    await message.answer(
        f"Выбери, что выдать юзеру `{user_id}`:",
        reply_markup=build_grant_keyboard(user_id, get_tariffs()),
        parse_mode="Markdown"
    )


# grantcustom регистрируется РАНЬШЕ admin:grant:{id}:{days} из-за общего префикса
@admin_router.callback_query(F.data.startswith("admin:grantcustom:"))
async def admin_grant_custom(callback: types.CallbackQuery, state: FSMContext):
    user_id = int(callback.data.split(":")[2])
    await state.set_state(AdminFSM.grant_days)
    await state.update_data(grant_target=user_id)
    try:
        await callback.message.edit_text(
            f"🧪 Введи количество дней для юзера `{user_id}`:\n\n_Для отмены: /cancel_",
            parse_mode="Markdown"
        )
    except Exception:
        pass


@admin_router.callback_query(F.data.startswith("admin:grant:"))
async def admin_grant_tariff(callback: types.CallbackQuery, session: AsyncSession, bot: Bot, state: FSMContext):
    await state.clear()
    _, _, _, user_id_str, days_str = callback.data.split(":")
    user_id, days = int(user_id_str), int(days_str)
    plan = next(
        (name for name, info in get_tariffs().items() if info["days"] == days),
        None
    )
    ok, exp, vpn_data = await _do_grant(session, user_id, plan, days)
    if not ok:
        return await callback.answer("❌ Не удалось создать/найти юзера", show_alert=True)

    label = plan or f"{days} дн."
    notified = True
    try:
        await bot.send_message(user_id, _grant_notify_text(f"({label})", exp, vpn_data), parse_mode="Markdown")
    except Exception:
        notified = False
    await callback.answer(f"✅ Выдано: {label}, до {exp}" + ("" if notified else "\n(юзер не получил уведомление)"), show_alert=True)


@admin_router.message(AdminFSM.grant_days)
async def admin_grant_days_process(message: types.Message, session: AsyncSession, bot: Bot, state: FSMContext):
    raw = (message.text or "").strip()
    if not raw.isdigit() or not (1 <= int(raw) <= 3650):
        return await message.answer("⚠️ Целое число от 1 до 3650. Попробуй ещё раз или /cancel", parse_mode="Markdown")
    data = await state.get_data()
    user_id = data["grant_target"]
    days = int(raw)
    await state.clear()

    ok, exp, vpn_data = await _do_grant(session, user_id, None, days)
    if not ok:
        return await message.answer("❌ Не удалось создать/найти юзера")
    notified = True
    try:
        await bot.send_message(user_id, _grant_notify_text("(тест)", exp, vpn_data), parse_mode="Markdown")
    except Exception:
        notified = False
    extra = "" if notified else "\n⚠️ Уведомление не доставлено (юзер не писал боту или заблокировал его)."
    await message.answer(
        f"✅ Юзеру `{user_id}` выдано *{days}* дн.\n📅 Активно до: `{exp}`{extra}",
        parse_mode="Markdown"
    )
