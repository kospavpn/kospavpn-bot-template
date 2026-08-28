import os
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


class MenuCallBack(CallbackData, prefix="menu"):
    level: int
    menu_name: str
    category: int | None = None
    page: int = 1
    product_id: int | None = None


def get_user_main_btns(*, level: int):
    keyboard = [
        [InlineKeyboardButton(
            text="🎁 Пробный период на 24 часа",
            callback_data=MenuCallBack(level=level, menu_name='trial').pack()
        )],
        [InlineKeyboardButton(
            text="💳 Подписка",
            callback_data=MenuCallBack(level=level, menu_name='subscription').pack()
        )],
        [
            InlineKeyboardButton(
                text="👤 Профиль",
                callback_data=MenuCallBack(level=level, menu_name='profile').pack()
            ),
            InlineKeyboardButton(
                text="📚 Инструкция",
                callback_data=MenuCallBack(level=level, menu_name='instructions').pack()
            ),
        ],
        [
            InlineKeyboardButton(
                text="⚙️ Настройки",
                callback_data=MenuCallBack(level=level, menu_name='settings').pack()
            ),
            InlineKeyboardButton(
                text="ℹ️ О Kospavpn",
                callback_data=MenuCallBack(level=level, menu_name='about').pack()
            ),
        ],
        [InlineKeyboardButton(
            text="💬 Поддержка",
            url=os.environ.get("SUPPORT_URL", "https://t.me/your_support")
        )],
        [InlineKeyboardButton(
            text="📢 Telegram-канал",
            url=f"https://t.me/{os.environ.get('CHANNEL_USERNAME', 'your_channel')}"
        )],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_user_catalog_btns(*, level: int, categories: list, sizes: tuple[int] = (2,)):
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(
        text='Назад',
        callback_data=MenuCallBack(level=level - 1, menu_name='main').pack()
    ))
    keyboard.add(InlineKeyboardButton(
        text='Корзина 🛒',
        callback_data=MenuCallBack(level=3, menu_name='cart').pack()
    ))
    for c in categories:
        keyboard.add(InlineKeyboardButton(
            text=c.name,
            callback_data=MenuCallBack(level=level + 1, menu_name=c.name, category=c.id).pack()
        ))
    return keyboard.adjust(*sizes).as_markup()


def get_products_btns(
    *,
    level: int,
    category: int,
    page: int,
    pagination_btns: dict,
    product_id: int,
    sizes: tuple[int] = (2, 1)
):
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(
        text='Назад',
        callback_data=MenuCallBack(level=level - 1, menu_name='catalog').pack()
    ))
    keyboard.add(InlineKeyboardButton(
        text='Корзина 🛒',
        callback_data=MenuCallBack(level=3, menu_name='cart').pack()
    ))
    keyboard.add(InlineKeyboardButton(
        text='Купить 💵',
        callback_data=MenuCallBack(level=level, menu_name='add_to_cart', product_id=product_id).pack()
    ))
    keyboard.adjust(*sizes)
    row = []
    for text, menu_name in pagination_btns.items():
        if menu_name == "next":
            row.append(InlineKeyboardButton(
                text=text,
                callback_data=MenuCallBack(
                    level=level,
                    menu_name=menu_name,
                    category=category,
                    page=page + 1
                ).pack()
            ))
        elif menu_name == "previous":
            row.append(InlineKeyboardButton(
                text=text,
                callback_data=MenuCallBack(
                    level=level,
                    menu_name=menu_name,
                    category=category,
                    page=page - 1
                ).pack()
            ))
    return keyboard.row(*row).as_markup()


def get_user_cart(
    *,
    level: int,
    page: int | None,
    pagination_btns: dict | None,
    product_id: int | None,
    sizes: tuple[int] = (3,)
):
    keyboard = InlineKeyboardBuilder()
    if page:
        keyboard.add(InlineKeyboardButton(
            text='Удалить',
            callback_data=MenuCallBack(level=level, menu_name='delete', product_id=product_id, page=page).pack()
        ))
        keyboard.add(InlineKeyboardButton(
            text='-1',
            callback_data=MenuCallBack(level=level, menu_name='decrement', product_id=product_id, page=page).pack()
        ))
        keyboard.add(InlineKeyboardButton(
            text='+1',
            callback_data=MenuCallBack(level=level, menu_name='increment', product_id=product_id, page=page).pack()
        ))
        keyboard.adjust(*sizes)
        row = []
        if pagination_btns:
            for text, menu_name in pagination_btns.items():
                if menu_name == "next":
                    row.append(InlineKeyboardButton(
                        text=text,
                        callback_data=MenuCallBack(level=level, menu_name=menu_name, page=page + 1).pack()
                    ))
                elif menu_name == "previous":
                    row.append(InlineKeyboardButton(
                        text=text,
                        callback_data=MenuCallBack(level=level, menu_name=menu_name, page=page - 1).pack()
                    ))
            keyboard.row(*row)
        row2 = [
            InlineKeyboardButton(
                text='На главную 🏠',
                callback_data=MenuCallBack(level=0, menu_name='main').pack()
            ),
            InlineKeyboardButton(
                text='Заказать',
                callback_data=MenuCallBack(level=0, menu_name='order').pack()
            ),
        ]
        return keyboard.row(*row2).as_markup()
    else:
        keyboard.add(
            InlineKeyboardButton(
                text='На главную 🏠',
                callback_data=MenuCallBack(level=0, menu_name='main').pack()
            )
        )
        return keyboard.adjust(*sizes).as_markup()


def get_callback_btns(*, btns: dict[str, str], sizes: tuple[int] = (2,)):
    keyboard = InlineKeyboardBuilder()
    for text, data in btns.items():
        keyboard.add(InlineKeyboardButton(text=text, callback_data=data))
    return keyboard.adjust(*sizes).as_markup()


# ========== АДМИН-ПАНЕЛЬ VPN INLINE КЛАВИАТУРЫ ==========


def admin_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Выдать подписку", callback_data="admin:grant")],
        [
            InlineKeyboardButton(text="👤 Пользователи", callback_data="admin:users:page:0:all"),
            InlineKeyboardButton(text="💰 История платежей", callback_data="admin:payments"),
        ],
        [
            InlineKeyboardButton(text="📢 Рассылка", callback_data="admin:broadcast"),
            InlineKeyboardButton(text="🔍 Поиск пользователя", callback_data="admin:search"),
        ],
        [
            InlineKeyboardButton(text="🗂 Тарифы", callback_data="admin:tariffs"),
            InlineKeyboardButton(text="🏷 Промокоды", callback_data="admin:promos"),
        ],
        [
            InlineKeyboardButton(text="👑 Админы", callback_data="admin:admins"),
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="admin:settings"),
        ],
        [InlineKeyboardButton(text="📜 Логи", callback_data="admin:logs")],
    ])


def admin_stats_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 За 24 часа", callback_data="admin:stats:1")],
        [InlineKeyboardButton(text="📈 За 7 дней", callback_data="admin:stats:7")],
        [InlineKeyboardButton(text="📈 За 30 дней", callback_data="admin:stats:30")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:back")],
    ])


def admin_user_card(user_id: int, is_active: bool, frozen_days: int = 0) -> InlineKeyboardMarkup:
    block_text = "🚫 Заблокировать" if is_active else "✅ Разблокировать"
    rows = [
        [InlineKeyboardButton(text="⏰ +7 дней", callback_data=f"admin:user:extend:{user_id}:7"),
         InlineKeyboardButton(text="⏰ +30 дней", callback_data=f"admin:user:extend:{user_id}:30")],
        [InlineKeyboardButton(text="➕ Своя длительность", callback_data=f"admin:user:extendcustom:{user_id}")],
        [InlineKeyboardButton(text="💳 Платежи юзера", callback_data=f"admin:user:payments:{user_id}:0")],
        [InlineKeyboardButton(text="❌ Отключить подписку", callback_data=f"admin:user:disable:{user_id}")],
        [InlineKeyboardButton(text=block_text, callback_data=f"admin:user:block:{user_id}")],
    ]
    if frozen_days and frozen_days > 0:
        rows.append([InlineKeyboardButton(
            text=f"🧊 Обнулить заморозку ({frozen_days} дн.)",
            callback_data=f"admin:user:frozenreset:{user_id}"
        )])
    rows.append([InlineKeyboardButton(text="🔙 К списку", callback_data="admin:users:page:0:all")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_payments_list(page: int, total_pages: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    row = []
    if page > 0:
        row.append(InlineKeyboardButton(text="◀️", callback_data=f"admin:payments:list:{page-1}"))
    row.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        row.append(InlineKeyboardButton(text="▶️", callback_data=f"admin:payments:list:{page+1}"))
    if row:
        builder.row(*row)
    builder.row(InlineKeyboardButton(text="💰 К сводке", callback_data="admin:payments"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin:back"))
    return builder.as_markup()


def admin_user_payments_keyboard(user_id: int, page: int, total_pages: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    row = []
    if page > 0:
        row.append(InlineKeyboardButton(text="◀️", callback_data=f"admin:user:payments:{user_id}:{page-1}"))
    row.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        row.append(InlineKeyboardButton(text="▶️", callback_data=f"admin:user:payments:{user_id}:{page+1}"))
    if row:
        builder.row(*row)
    builder.row(InlineKeyboardButton(text="👤 К карточке", callback_data=f"admin:user:card:{user_id}"))
    return builder.as_markup()


def admin_paginator(current_page: int, total_pages: int, prefix: str, filter_type: str = "") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if total_pages <= 1:
        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin:back"))
        return builder.as_markup()
    row = []
    if current_page > 0:
        if filter_type:
            row.append(InlineKeyboardButton(text="◀️", callback_data=f"{prefix}:{current_page-1}:{filter_type}"))
        else:
            row.append(InlineKeyboardButton(text="◀️", callback_data=f"{prefix}:{current_page-1}"))
    row.append(InlineKeyboardButton(text=f"{current_page+1}/{total_pages}", callback_data="noop"))
    if current_page < total_pages - 1:
        if filter_type:
            row.append(InlineKeyboardButton(text="▶️", callback_data=f"{prefix}:{current_page+1}:{filter_type}"))
        else:
            row.append(InlineKeyboardButton(text="▶️", callback_data=f"{prefix}:{current_page+1}"))
    builder.row(*row)
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin:back"))
    return builder.as_markup()


def admin_broadcast_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 Отправить всем", callback_data="admin:broadcast:all")],
        [InlineKeyboardButton(text="📨 Только активным", callback_data="admin:broadcast:active")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin:back")],
    ])


def admin_servers_menu(servers_status: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for name, status, load in servers_status:
        emoji = "🟢" if status == "online" else "🔴"
        builder.row(InlineKeyboardButton(
            text=f"{emoji} {name} ({load}%)",
            callback_data=f"admin:server:{name}"
        ))
    builder.row(InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:servers"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin:back"))
    return builder.as_markup()


def admin_tariffs_menu(tariffs) -> InlineKeyboardMarkup:
    """tariffs — список объектов Tariff из БД (все, включая неактивные)."""
    builder = InlineKeyboardBuilder()
    for t in tariffs:
        status = "" if t.is_active else " ⛔"
        builder.row(InlineKeyboardButton(
            text=f"{t.name} — {int(t.price) if float(t.price) == int(t.price) else t.price} ₽ / {t.days} дн.{status}",
            callback_data=f"admin:tariff:{t.id}"
        ))
    builder.row(InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:tariffs"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin:back"))
    return builder.as_markup()


def admin_tariff_edit(tariff_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить цену/срок", callback_data=f"admin:tariff:edit:{tariff_id}")],
        [InlineKeyboardButton(text="🔙 К тарифам", callback_data="admin:tariffs")],
    ])


def admin_promos_menu(promos) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for p in promos:
        status = "✅" if p.is_active else "⛔"
        limit = f"/{p.max_uses}" if p.max_uses else "/∞"
        if p.discount_type == "percent":
            discount = f"-{int(p.discount_value)}%"
        else:
            dv = int(p.discount_value) if float(p.discount_value) == int(p.discount_value) else float(p.discount_value)
            discount = f"-{dv} ₽"
        builder.row(InlineKeyboardButton(
            text=f"{status} {p.code} ({discount}) · {p.used_count}{limit}",
            callback_data=f"admin:promo:{p.id}"
        ))
    builder.row(InlineKeyboardButton(text="➕ Создать промокод", callback_data="admin:promo:new"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin:back"))
    return builder.as_markup()


def admin_promo_edit(promo_id: int, is_active: bool) -> InlineKeyboardMarkup:
    toggle_text = "⛔ Отключить" if is_active else "✅ Включить"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle_text, callback_data=f"admin:promo:toggle:{promo_id}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin:promo:delete:{promo_id}")],
        [InlineKeyboardButton(text="🏷 К промокодам", callback_data="admin:promos")],
    ])


def admin_admins_menu(admins, owner_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for a in admins:
        if a.user_id == owner_id:
            continue
        label = a.name or str(a.user_id)
        builder.row(InlineKeyboardButton(
            text=f"❌ {label} ({a.user_id})",
            callback_data=f"admin:admins:remove:{a.user_id}"
        ))
    builder.row(InlineKeyboardButton(text="➕ Добавить админа", callback_data="admin:admins:add"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin:back"))
    return builder.as_markup()


def admin_logs_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Последние строки", callback_data="admin:logs:tail")],
        [InlineKeyboardButton(text="⚠️ Только ошибки", callback_data="admin:logs:errors")],
        [InlineKeyboardButton(text="📥 Скачать файл лога", callback_data="admin:logs:file")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:back")],
    ])


def build_grant_keyboard(user_id: int, tariffs: dict) -> InlineKeyboardMarkup:
    """Выбор тарифа/длительности для выдачи подписки юзеру user_id."""
    rows = []
    for name, info in sorted(tariffs.items(), key=lambda kv: kv[1]['days']):
        price = int(info['price']) if float(info['price']) == int(float(info['price'])) else float(info['price'])
        rows.append([InlineKeyboardButton(
            text=f"{name} — {price} ₽ ({info['days']} дн.)",
            callback_data=f"admin:grant:{user_id}:{info['days']}"
        )])
    rows.append([InlineKeyboardButton(text="🧪 Своя длительность (тест)", callback_data=f"admin:grantcustom:{user_id}")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
