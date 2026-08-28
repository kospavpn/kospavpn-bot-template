import os
import re
from aiogram import F, types, Router
from aiogram.filters import CommandStart, Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaAnimation
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta
from database.orm_query import (
    orm_add_to_cart,
    orm_add_user,
    orm_record_payment,
    get_tariffs_from_db,
    get_promo_by_code,
    check_promo_usable,
    apply_promo_usage,
    get_setting,
    set_referred_by,
    count_referrals,
    get_user_payments_count,
    extend_user_subscription,
)
from database.models import User
from filters.chat_types import ChatTypeFilter
from handlers.menu_processing import get_menu_content
from kbds.inline import MenuCallBack
from services.platega import create_platega_payment, check_platega_payment
from services.xui import create_or_update_vpn_client, set_client_enable

# Тарифы: кэш, загружается из БД (админка меняет цены/периоды без правки кода).
# ВАЖНО: словарь мутируется на месте — ссылки из других модулей остаются валидными.
TARIFFS = {
    "1 месяц": {"price": 50, "days": 30},
    "3 месяца": {"price": 140, "days": 90},
    "6 месяцев": {"price": 270, "days": 180},
    "12 месяцев": {"price": 500, "days": 365},
}


def get_tariffs():
    return TARIFFS


async def refresh_tariffs_cache(session):
    rows = await get_tariffs_from_db(session)
    if rows:
        fresh = {t.name: {"price": float(t.price), "days": t.days} for t in rows}
        TARIFFS.clear()
        TARIFFS.update(fresh)


# Комиссии Platega для разных методов (в долях)
COMMISSIONS = {
    2: 0.08,   # СБП (8%)
    13: 0.05,  # Криптовалюта (5%) — подгоните под реальную
}
CRYPTO_METHOD = 13
SBP_METHOD = 2
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "your_channel")
# Хранилище: {user_id: {"plan": str, "method": int, "transaction_id": str, "payment_link": str}}
selected_payments = {}
# Применённые промокоды: {user_id: {"promo_id", "code", "discount_type", "discount_value"}}
applied_promos = {}


class UserFSM(StatesGroup):
    promo_code = State()


user_private_router = Router()
user_private_router.message.filter(ChatTypeFilter(["private"]))

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====

def clean_name(name):
    if not name:
        return None
    return name.replace('\\', '').strip()


def _plural_days(n: int) -> str:
    n = abs(int(n)) % 100
    if 11 <= n <= 19:
        return "дней"
    d = n % 10
    if d == 1:
        return "день"
    if 2 <= d <= 4:
        return "дня"
    return "дней"


def fmt_price(value) -> str:
    """50.0 -> '50', 149.9 -> '149.9'"""
    value = float(value)
    return f"{int(value)}" if value == int(value) else f"{value:g}"

def escape_md(text):
    return re.sub(r'([_*[\]()~`>#+=|{}!])', r'\\\1', str(text))

def get_base_price(plan: str, method: int) -> float:
    """Возвращает базовую цену (без комиссии) для передачи в Platega с учётом метода."""
    total = get_tariffs()[plan]['price']
    commission = COMMISSIONS.get(method, 0.0)
    if commission == 0:
        return float(total)
    return round(total / (1 + commission), 2)


def calc_discounted(total: float, promo: dict | None) -> float:
    """Цена с учётом промокода (promo — запись из applied_promos или None)."""
    if not promo:
        return total
    if promo["discount_type"] == "percent":
        value = total * (1 - promo["discount_value"] / 100)
    else:
        value = total - promo["discount_value"]
    return round(max(value, 0), 2)


def build_tariff_buttons(back_callback_data) -> InlineKeyboardMarkup:
    """Кнопки тарифов из актуального кэша (сортировка по сроку)."""
    rows = []
    for name, info in sorted(get_tariffs().items(), key=lambda kv: kv[1]['days']):
        rows.append([InlineKeyboardButton(
            text=f"{name} — {fmt_price(info['price'])} ₽",
            callback_data=f"subscription_days_{info['days']}"
        )])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback_data)])
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def safe_edit_text(callback: types.CallbackQuery, text: str, reply_markup=None, parse_mode=None):
    try:
        if callback.message.photo or callback.message.video or callback.message.document:
            await callback.message.delete()
            await callback.message.answer(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            await callback.message.edit_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:
        try:
            await callback.message.delete()
            await callback.message.answer(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception:
            await callback.message.answer(text=text, reply_markup=reply_markup, parse_mode=parse_mode)

async def send_media(message, media, caption, reply_markup):
    if isinstance(media, InputMediaAnimation):
        return await message.answer_animation(
            animation=media.media,
            caption=caption,
            reply_markup=reply_markup
        )
    else:
        return await message.answer_photo(
            photo=media.media,
            caption=caption,
            reply_markup=reply_markup
        )

# ===== АКТИВАЦИЯ ПОДПИСКИ (С УЧЁТОМ ЗАМОРОЖЕННЫХ ДНЕЙ) =====

async def activate_subscription_by_user_id(session: AsyncSession, user_id: int, plan: str):
    user = await session.scalar(select(User).where(User.user_id == user_id))
    if not user:
        user = User(user_id=user_id, trial_used=False)
        session.add(user)
        await session.commit()
        await session.refresh(user)
    days = get_tariffs().get(plan, {}).get("days", 30)
    # НОВОЕ: добавляем замороженные дни, если есть
    if user.frozen_days and user.frozen_days > 0:
        days += user.frozen_days
        user.frozen_days = 0  # обнуляем после использования
    if user.subscription_end and user.subscription_end > datetime.now():
        user.subscription_end = user.subscription_end + timedelta(days=days)
    else:
        user.subscription_end = datetime.now() + timedelta(days=days)
    user.is_active = True
    user.subscription_plan = plan
    await session.commit()
    if user_id in selected_payments:
        del selected_payments[user_id]
    try:
        vpn_data = await create_or_update_vpn_client(
            user_email=str(user_id),
            days=days
        )
        # НОВОЕ: включаем клиента в панели (если был отключён)
        try:
            await set_client_enable(user_id, enable=True)
        except Exception as e:
            print(f"⚠️ Не удалось включить клиента: {e}")
        return vpn_data
    except Exception as e:
        print(f"Ошибка генерации ключа: {e}")
        return None

# ===== РЕФЕРАЛЬНЫЙ БОНУС =====

async def process_referral_bonus(session: AsyncSession, payer_id: int, bot=None):
    """Начисляет бонус пригласившему за ПЕРВУЮ оплату приглашённого."""
    payer = await session.scalar(select(User).where(User.user_id == payer_id))
    if not payer or not payer.referred_by:
        return
    referrer_id = payer.referred_by
    # Бонус только если это первый платёж этого юзера
    paid_count = await get_user_payments_count(session, payer_id)
    if paid_count != 1:
        return
    days = int(await get_setting(session, "ref_bonus_days", "7") or 7)
    if days <= 0:
        return
    referrer = await extend_user_subscription(session, referrer_id, days)
    if referrer and bot:
        try:
            name = payer.first_name or f"юзер #{payer_id}"
            await bot.send_message(
                referrer_id,
                f"🎉 *{name}* оплатил подписку по твоей ссылке!\n\n"
                f"🎁 Тебе начислено *+{days}* дней подписки.",
                parse_mode="Markdown"
            )
        except Exception:
            pass


# ===== АКТИВАЦИЯ ПРОБНОГО ПЕРИОДА =====

async def activate_trial(callback: types.CallbackQuery, session: AsyncSession):
    user_id = callback.from_user.id
    user = await session.scalar(select(User).where(User.user_id == user_id))
    if not user:
        user = User(
            user_id=user_id,
            first_name=callback.from_user.first_name,
            last_name=callback.from_user.last_name,
            subscription_end=None,
            location="Неизвестно",
            devices_count=0,
            is_active=False,
            subscription_plan=None,
            notifications_enabled=True,
            trial_used=False
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    if user.is_active and user.subscription_end and user.subscription_end > datetime.now():
        await callback.message.answer("⚠️ У вас уже есть активная подписка.")
        return
    if user.trial_used:
        await callback.message.answer("❌ Вы уже использовали пробный период.")
        return
    user.is_active = True
    user.subscription_end = datetime.now() + timedelta(days=1)
    user.subscription_plan = "Пробный период"
    user.trial_used = True
    user.deactivated_at = None
    await session.commit()
    try:
        vpn_data = await create_or_update_vpn_client(user_email=str(user_id), days=1)
        expiry_str = user.subscription_end.strftime('%d.%m.%Y %H:%M')
        text = (
            "🎉 **Пробный период активирован!**\n\n"
            "Вы получили доступ к VPN на **1 день**.\n\n"
            "🔑 **Ваш ключ**\n\n"
            f"📧 **Клиент:** `{vpn_data['email']}`\n"
            f"🆔 **UUID:** `{vpn_data['uuid']}`\n"
            f"📅 **Действует до:** {expiry_str}\n\n"
            "🔗 **Ссылка подписки:**\n"
            f"```\n{vpn_data['config_link']}\n```\n\n"
            "Нажмите на синюю область один раз, чтобы скопировать ссылку."
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=MenuCallBack(level=0, menu_name='main').pack())]
        ])
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception as e:
        user.is_active = False
        user.subscription_end = None
        user.subscription_plan = None
        user.trial_used = False
        await session.commit()
        await callback.message.answer(f"❌ Ошибка активации пробного периода: {str(e)}")

# ===== СТАРТ =====

@user_private_router.callback_query(MenuCallBack.filter(F.menu_name == "main"))
async def main_menu_callback(callback: types.CallbackQuery, callback_data: MenuCallBack, session: AsyncSession):
    user_name = callback.from_user.first_name or "Гость"
    media, caption, reply_markup = await get_menu_content(
        session, level=0, menu_name="main", user_name=user_name
    )
    if media:
        try:
            await callback.message.edit_media(media=media, reply_markup=reply_markup)
            return
        except Exception:
            pass
        try:
            await callback.message.delete()
        except Exception:
            pass
        await send_media(callback.message, media, caption, reply_markup)
    else:
        await safe_edit_text(callback, caption, reply_markup)

@user_private_router.message(CommandStart())
async def start_cmd(message: types.Message, session: AsyncSession, command: CommandObject | None = None):
    user_id = message.from_user.id
    user = await session.scalar(select(User).where(User.user_id == user_id))
    if not user:
        user = User(
            user_id=user_id,
            first_name=clean_name(message.from_user.first_name),
            last_name=clean_name(message.from_user.last_name),
            subscription_end=datetime.now() + timedelta(days=30),
            location="Неизвестно",
            devices_count=0,
            is_active=True,
            subscription_plan="1 месяц",
            notifications_enabled=True,
            trial_used=False
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

    # Реферальная ссылка: /start ref<ID пригласившего>
    referral_note = ""
    args = command.args if command else None
    if args and args.startswith("ref") and args[3:].isdigit():
        referrer_id = int(args[3:])
        if referrer_id != user_id:
            referrer = await session.scalar(select(User).where(User.user_id == referrer_id))
            if referrer and not user.referred_by:
                if await set_referred_by(session, user_id, referrer_id):
                    referral_note = (
                        "🎉 Ты пришёл по приглашению!\n"
                        f"Когда оплатишь подписку, *{referrer.first_name or 'твой друг'}* получит бонус.\n\n"
                    )

    user_name = message.from_user.first_name or "Гость"
    media, caption, reply_markup = await get_menu_content(
        session, level=0, menu_name="main", user_name=user_name
    )
    if referral_note:
        if media:
            caption = referral_note + (caption or "")
        else:
            caption = referral_note + (caption or "")
    if media:
        await send_media(message, media, caption, reply_markup)
    else:
        await message.answer(text=caption, reply_markup=reply_markup)

# ===== ДОБАВЛЕНИЕ В КОРЗИНУ (заглушка) =====

async def add_to_cart(callback: types.CallbackQuery, callback_data: MenuCallBack, session: AsyncSession):
    user = callback.from_user
    await orm_add_user(
        session,
        user_id=user.id,
        first_name=clean_name(user.first_name),
        last_name=clean_name(user.last_name),
        phone=None,
    )
    await orm_add_to_cart(session, user_id=user.id, product_id=callback_data.product_id)
    await callback.answer("Товар добавлен в корзину.")

# ===== ПРОФИЛЬ (С ОТОБРАЖЕНИЕМ ЗАМОРОЖЕННЫХ ДНЕЙ) =====

@user_private_router.callback_query(MenuCallBack.filter(F.menu_name == "profile"))
async def profile_cmd(callback: types.CallbackQuery, callback_data: MenuCallBack, session: AsyncSession):
    try:
        await callback.answer()
        user_id = callback.from_user.id
        user = await session.scalar(select(User).where(User.user_id == user_id))
        if not user:
            user = User(
                user_id=user_id,
                first_name=clean_name(callback.from_user.first_name),
                last_name=clean_name(callback.from_user.last_name),
                subscription_end=datetime.now() + timedelta(days=30),
                location="Неизвестно",
                devices_count=0,
                is_active=True,
                subscription_plan="1 месяц",
                notifications_enabled=True,
                trial_used=False
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        else:
            new_first = clean_name(callback.from_user.first_name)
            new_last = clean_name(callback.from_user.last_name)
            if user.first_name != new_first or user.last_name != new_last:
                user.first_name = new_first
                user.last_name = new_last
                await session.commit()
                await session.refresh(user)
        if user.is_active and user.subscription_end and user.subscription_end > datetime.now():
            status_emoji = "🟢"
            status_text = "Активна"
            expiry = user.subscription_end.strftime('%d.%m.%Y %H:%M')
            if not user.subscription_plan:
                user.subscription_plan = "1 месяц"
                await session.commit()
            plan = user.subscription_plan
        else:
            status_emoji = "🔴"
            status_text = "Не активна"
            expiry = "—"
            plan = "—"
        user_id_esc = escape_md(user.user_id)
        plan_esc = escape_md(plan)
        profile_text = (
            "👤 **Профиль**\n\n"
            f"{status_emoji} **Статус:** {status_text}\n"
            f"📋 **Подписка:** {plan_esc}\n"
            f"📅 **Действует до:** {expiry}\n"
        )
        if user.frozen_days and user.frozen_days > 0:
            n = user.frozen_days
            if n % 10 == 1 and n % 100 != 11:
                word = "день"
            elif n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
                word = "дня"
            else:
                word = "дней"
            profile_text += f"❄️ **Заморожено:** {n} {word}\n"
        deactivated_at = getattr(user, "deactivated_at", None)
        if not user.is_active and deactivated_at:
            profile_text += f"🗑 **Деактивирована:** {deactivated_at.strftime('%d.%m.%Y в %H:%M')}\n"
        profile_text += f"🆔 **ID:** `{user_id_esc}`"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Продлить подписку", callback_data="extend_subscription")],
            [InlineKeyboardButton(text="🔑 Ключ", callback_data="get_config")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=MenuCallBack(level=0, menu_name='main').pack())]
        ])
        await safe_edit_text(callback, profile_text, keyboard, "Markdown")
    except Exception as e:
        print(f"Ошибка в profile_cmd: {e}")
        await callback.message.answer("⚠️ Произошла ошибка при загрузке профиля. Попробуйте позже.")

# ===== ПРОБНЫЙ ПЕРИОД =====

@user_private_router.callback_query(MenuCallBack.filter(F.menu_name == "trial"))
async def trial_period(callback: types.CallbackQuery, session: AsyncSession):
    await callback.answer()
    user_id = callback.from_user.id
    user = await session.scalar(select(User).where(User.user_id == user_id))
    if not user:
        user = User(
            user_id=user_id,
            first_name=callback.from_user.first_name,
            last_name=callback.from_user.last_name,
            subscription_end=None,
            location="Неизвестно",
            devices_count=0,
            is_active=False,
            subscription_plan=None,
            notifications_enabled=True,
            trial_used=False
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    try:
        member = await callback.bot.get_chat_member(chat_id=f"@{CHANNEL_USERNAME}", user_id=user_id)
        if member.status not in ["member", "administrator", "creator"]:
            await callback.message.answer(
                "🎁 **Пробный период на 24 часа**\n\n"
                "Для получения бесплатного доступа на 24 часа подпишитесь на наш Telegram-канал.\n"
                "Это поможет нам развиваться и держать вас в курсе новостей.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📢 Подписаться", url=f"https://t.me/{CHANNEL_USERNAME}")],
                    [InlineKeyboardButton(text="🔄 Проверить подписку", callback_data="check_subscription")],
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data=MenuCallBack(level=0, menu_name='main').pack())]
                ]),
                parse_mode="Markdown"
            )
            return
    except Exception as e:
        await callback.message.answer("⚠️ Ошибка проверки подписки. Попробуйте позже.")
        return
    await activate_trial(callback, session)

# ===== ПРОВЕРКА ПОДПИСКИ =====

@user_private_router.callback_query(F.data == "check_subscription")
async def check_subscription(callback: types.CallbackQuery, session: AsyncSession):
    await callback.answer()
    user_id = callback.from_user.id
    try:
        member = await callback.bot.get_chat_member(chat_id=f"@{CHANNEL_USERNAME}", user_id=user_id)
        if member.status in ["member", "administrator", "creator"]:
            await activate_trial(callback, session)
        else:
            await callback.message.answer(
                "❌ Вы всё ещё не подписаны на канал.\n"
                "Пожалуйста, подпишитесь и нажмите «Проверить подписку» снова.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📢 Подписаться", url=f"https://t.me/{CHANNEL_USERNAME}")],
                    [InlineKeyboardButton(text="🔄 Проверить подписку", callback_data="check_subscription")],
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data=MenuCallBack(level=0, menu_name='main').pack())]
                ])
            )
    except Exception as e:
        await callback.message.answer("⚠️ Ошибка проверки подписки. Попробуйте позже.")

# ===== ПРОДЛЕНИЕ ПОДПИСКИ =====

@user_private_router.callback_query(F.data == "extend_subscription")
async def extend_subscription(callback: types.CallbackQuery, session: AsyncSession):
    await callback.answer()
    user_id = callback.from_user.id
    user = await session.scalar(select(User).where(User.user_id == user_id))
    if not user:
        await callback.message.answer("❌ Пользователь не найден.")
        return
    plan = user.subscription_plan
    if not plan or plan not in get_tariffs():
        text = (
            "💳 **Тарифы**\n\nВыберите подходящий тариф"
        )
        await callback.message.edit_text(
            text,
            reply_markup=build_tariff_buttons(MenuCallBack(level=0, menu_name='profile').pack()),
            parse_mode="Markdown"
        )
        return
    await show_payment_screen(callback, plan)

# ===== ВЫБОР ТАРИФА =====

@user_private_router.callback_query(F.data.startswith("subscription_days_"))
async def subscription_choose(callback: types.CallbackQuery):
    await callback.answer()
    days = int(callback.data.replace("subscription_days_", ""))
    plan = next(
        (name for name, info in get_tariffs().items() if info["days"] == days),
        None
    )
    if not plan:
        await callback.message.answer("Тариф не найден.")
        return
    await show_payment_screen(callback, plan)

# ===== ЭКРАН ВЫБОРА МЕТОДА ОПЛАТЫ (БЕЗ СОЗДАНИЯ ПЛАТЕЖА) =====

def build_payment_screen(plan: str, user_id: int):
    """Текст + клавиатура экрана выбора способа оплаты (с учётом промокода)."""
    total_price = get_tariffs()[plan]['price']
    promo = applied_promos.get(user_id)
    if promo:
        final_price = calc_discounted(total_price, promo)
        price_lines = (
            f"- Подписка — {plan}\n"
            f"- Стоимость — {fmt_price(final_price)} ₽ (по промокоду {promo['code']}, было {fmt_price(total_price)} ₽)\n\n"
        )
        promo_row = [[InlineKeyboardButton(
            text=f"❌ Убрать промокод {promo['code']}",
            callback_data="promo_remove"
        )]]
    else:
        price_lines = (
            f"- Подписка — {plan}\n"
            f"- Стоимость — {fmt_price(total_price)} ₽\n\n"
        )
        promo_row = [[InlineKeyboardButton(text="🏷 Ввести промокод", callback_data="promo_enter")]]
    text = "💳 Оплата\n\n" + price_lines + "Выберите способ оплаты: "
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 СБП", callback_data=f"choose_sbp_{plan}"),
         InlineKeyboardButton(text="🪙 Криптовалюта", callback_data=f"choose_crypto_{plan}")],
        *promo_row,
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=MenuCallBack(level=0, menu_name='subscription').pack())]
    ])
    return text, keyboard


async def show_payment_screen(callback: types.CallbackQuery, plan: str):
    """
    Показывает экран выбора метода оплаты без создания платежа.
    """
    text, keyboard = build_payment_screen(plan, callback.from_user.id)
    # Удаляем старые данные платежа, если есть
    user_id = callback.from_user.id
    if user_id in selected_payments:
        del selected_payments[user_id]
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception:
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="Markdown")

# ===== СОЗДАНИЕ ПЛАТЕЖА И ОБНОВЛЕНИЕ СООБЩЕНИЯ (ПОСЛЕ ВЫБОРА МЕТОДА) =====

async def create_payment_and_update(message: types.Message, user_id: int, plan: str, method: int):
    """
    Создаёт платёж для выбранного метода и обновляет сообщение,
    показывая галочку у выбранного метода и кнопку 'Оплатить' со ссылкой.
    """
    total_price = calc_discounted(get_tariffs()[plan]['price'], applied_promos.get(user_id))
    # Базовая цена (с учётом скидки и без комиссии Platega) для отправки в Platega
    commission = COMMISSIONS.get(method, 0.0)
    base_price = round(total_price / (1 + commission), 2) if commission else float(total_price)
    payload = f"{user_id}:{plan}"
    payment = await create_platega_payment(
        amount=base_price,
        description=f"Подписка {plan}",
        payload=payload,
        payment_method=method
    )
    if "error" in payment:
        error_msg = payment.get('error', 'Неизвестная ошибка')
        detail = payment.get('detail', '')
        await message.answer(
            f"❌ Ошибка создания платежа:\n\n```\n{error_msg}\n```"
            f"{f'\n\nДетали: ```{detail}```' if detail else ''}",
            parse_mode="Markdown"
        )
        return
    payment_link = payment.get("redirect")
    transaction_id = payment.get("transactionId")
    if not payment_link or not transaction_id:
        await message.answer("❌ Не удалось создать ссылку для оплаты.")
        return
    # Сохраняем данные
    promo = applied_promos.get(user_id)
    selected_payments[user_id] = {
        "plan": plan,
        "method": method,
        "transaction_id": transaction_id,
        "payment_link": payment_link,
        "amount_charged": total_price,
        "promo_id": promo["promo_id"] if promo else None,
    }
    # Формируем кнопки: выбранный метод с галочкой ✅, невыбранный – без
    if method == SBP_METHOD:
        sbp_button = InlineKeyboardButton(text="✅ 💳 СБП", callback_data=f"choose_sbp_{plan}")
        crypto_button = InlineKeyboardButton(text="🪙 Криптовалюта", callback_data=f"choose_crypto_{plan}")
    else:
        sbp_button = InlineKeyboardButton(text="💳 СБП", callback_data=f"choose_sbp_{plan}")
        crypto_button = InlineKeyboardButton(text="✅ 🪙 Криптовалюта", callback_data=f"choose_crypto_{plan}")
    # Кнопка "Оплатить" с эмодзи 🔘
    pay_button = InlineKeyboardButton(text="🔘 Оплатить", url=payment_link)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [sbp_button, crypto_button],
        [pay_button],
        [InlineKeyboardButton(text="🔍 Проверить оплату", callback_data="check_payment")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=MenuCallBack(level=0, menu_name='subscription').pack())]
    ])
    text = (
        "💳 Оплата\n\n"
        f"- Подписка — {plan}\n"
        + (f"- Стоимость — {fmt_price(total_price)} ₽ (промокод {applied_promos[user_id]['code']})\n\n" if applied_promos.get(user_id) else f"- Стоимость — {fmt_price(total_price)} ₽\n\n")
        + "После успешной оплаты нажмите на кнопку \"🔍 Проверить оплату\""
    )
    try:
        await message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception:
        await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")

# ===== ОБРАБОТЧИКИ ВЫБОРА СПОСОБА ОПЛАТЫ =====

@user_private_router.callback_query(F.data.startswith("choose_sbp_"))
async def choose_sbp(callback: types.CallbackQuery):
    await callback.answer()
    plan = callback.data.replace("choose_sbp_", "")
    if plan not in get_tariffs():
        await callback.message.answer("❌ Тариф не найден.")
        return
    await create_payment_and_update(callback.message, callback.from_user.id, plan, SBP_METHOD)

@user_private_router.callback_query(F.data.startswith("choose_crypto_"))
async def choose_crypto(callback: types.CallbackQuery):
    await callback.answer()
    plan = callback.data.replace("choose_crypto_", "")
    if plan not in get_tariffs():
        await callback.message.answer("❌ Тариф не найден.")
        return
    await create_payment_and_update(callback.message, callback.from_user.id, plan, CRYPTO_METHOD)

# ===== ПРОМОКОДЫ (ЮЗЕР) =====

@user_private_router.callback_query(F.data == "promo_enter")
async def promo_enter(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(UserFSM.promo_code)
    try:
        await callback.message.edit_text(
            "🏷 *Промокод*\n\nВведи промокод сообщением:\n\n_Для отмены: /cancel_",
            parse_mode="Markdown"
        )
    except Exception:
        await callback.message.answer(
            "🏷 *Промокод*\n\nВведи промокод сообщением:\n\n_Для отмены: /cancel_",
            parse_mode="Markdown"
        )


@user_private_router.message(Command("cancel"), StateFilter(UserFSM.promo_code))
async def promo_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отменено. Вернись к тарифам через меню.", parse_mode="Markdown")


@user_private_router.message(StateFilter(UserFSM.promo_code))
async def promo_process(message: types.Message, session: AsyncSession, state: FSMContext):
    code = (message.text or "").strip()[:50]
    user_id = message.from_user.id
    promo = await get_promo_by_code(session, code)
    error = await check_promo_usable(session, promo, user_id) if promo else "Промокод не найден"
    if error:
        await state.clear()
        await message.answer(f"❌ {error}", parse_mode="Markdown")
        return
    applied_promos[user_id] = {
        "promo_id": promo.id,
        "code": promo.code,
        "discount_type": promo.discount_type,
        "discount_value": float(promo.discount_value),
    }
    await state.clear()
    if promo.discount_type == "percent":
        label = f"-{int(promo.discount_value)}%"
    else:
        label = f"-{fmt_price(promo.discount_value)} ₽"
    await message.answer(
        f"✅ Промокод *{promo.code}* применён ({label}).\n\n"
        "Теперь выбери тариф в меню «Подписка» — скидка учтётся автоматически.",
        parse_mode="Markdown"
    )


@user_private_router.callback_query(F.data == "promo_remove")
async def promo_remove(callback: types.CallbackQuery):
    await callback.answer("Промокод убран")
    applied_promos.pop(callback.from_user.id, None)
    # Перерисовываем экран оплаты, если знаем текущий платёж
    payment_data = selected_payments.get(callback.from_user.id)
    plan = payment_data.get("plan") if payment_data else None
    if not plan:
        return
    text, keyboard = build_payment_screen(plan, callback.from_user.id)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception:
        pass

# ===== ПРОВЕРКА ОПЛАТЫ =====

@user_private_router.callback_query(F.data == "check_payment")
async def check_payment(callback: types.CallbackQuery, session: AsyncSession):
    await callback.answer()
    user_id = callback.from_user.id
    payment_data = selected_payments.get(user_id)
    if not payment_data:
        await callback.message.answer(
            "❌ Не найдено ожидающих платежей. Пожалуйста, выберите тариф и способ оплаты заново.",
            parse_mode="Markdown"
        )
        return
    transaction_id = payment_data.get("transaction_id")
    plan = payment_data.get("plan")
    if not transaction_id or not plan:
        await callback.message.answer("❌ Ошибка: отсутствуют данные платежа. Попробуйте заново.")
        return
    status_data = await check_platega_payment(transaction_id)
    if "error" in status_data:
        await callback.message.answer(
            f"❌ Ошибка проверки платежа: {status_data['error']}",
            parse_mode="Markdown"
        )
        return
    status = status_data.get("status")
    if status != "CONFIRMED":
        await callback.message.answer(
            "⏳ Платёж не обнаружен или не подтверждён. Убедитесь, что вы перевели средства, и попробуйте снова через пару минут.\n\n"
            "Если вы только что оплатили, подождите 1-2 минуты и нажмите «Проверить оплату» ещё раз.",
            parse_mode="Markdown"
        )
        return
    vpn_data = await activate_subscription_by_user_id(session, user_id, plan)
    if not vpn_data:
        await callback.message.answer(
            "❌ Ошибка при генерации ключа после оплаты. Пожалуйста, обратитесь в поддержку.",
            parse_mode="Markdown"
        )
        return
    # Записываем платёж в историю (сумма — с учётом промокода)
    amount = payment_data.get("amount_charged") or get_tariffs().get(plan, {}).get("price", 0)
    try:
        await orm_record_payment(
            session,
            user_id=user_id,
            amount=amount,
            plan=plan,
            transaction_id=str(transaction_id),
            method="sbp" if payment_data.get("method") == SBP_METHOD else "crypto",
        )
    except Exception as e:
        print(f"⚠️ Не удалось записать платёж в историю: {e}")
    # Списываем промокод
    promo = applied_promos.get(user_id)
    if promo and payment_data.get("promo_id"):
        try:
            await apply_promo_usage(session, promo["promo_id"], user_id)
        except Exception as e:
            print(f"⚠️ Не удалось списать промокод: {e}")
        applied_promos.pop(user_id, None)
    # Бонус приглашавшему за первую оплату реферала
    try:
        await process_referral_bonus(session, user_id, bot=callback.bot)
    except Exception as e:
        print(f"⚠️ Ошибка реферального бонуса: {e}")
    user = await session.scalar(select(User).where(User.user_id == user_id))
    expiry_date = user.subscription_end.strftime('%d.%m.%Y %H:%M') if user and user.subscription_end else 'Неизвестно'
    text = (
        "🔑 **Ваш ключ**\n\n"
        f"```\n{vpn_data['config_link']}\n```\n\n"
        "Нажмите на синюю область один раз, чтобы скопировать ваш ключ.\n\n"
        f"📧 **Клиент:** `{vpn_data['email']}`\n\n"
        f"🆔 **UUID:** `{vpn_data['uuid']}`"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=MenuCallBack(level=0, menu_name='main').pack())]
    ])
    await callback.message.answer(text, reply_markup=keyboard, parse_mode="Markdown")
    if user_id in selected_payments:
        del selected_payments[user_id]
    await safe_edit_text(
        callback,
        f"✅ **Оплата подтверждена!**\n\nВаша подписка на **{plan}** активирована до "
        f"{expiry_date}\n"
        "Спасибо, что выбрали Kospavpn! 🚀",
        parse_mode="Markdown"
    )

# ===== ПОКАЗАТЬ КОНФИГУРАЦИЮ =====

@user_private_router.callback_query(F.data == "get_config")
async def get_config(callback: types.CallbackQuery, session: AsyncSession):
    await callback.answer()
    user_id = callback.from_user.id
    user = await session.scalar(select(User).where(User.user_id == user_id))
    if not user or not user.is_active or (user.subscription_end and user.subscription_end < datetime.now()):
        await callback.message.answer("❌ У вас нет активной подписки. Оформите подписку через меню.")
        return
    try:
        days_left = (user.subscription_end - datetime.now()).days if user.subscription_end else 0
        if days_left <= 0:
            days_left = 30
        vpn_data = await create_or_update_vpn_client(
            user_email=str(user_id),
            days=days_left
        )
        text = (
            "🔑 **Ваш ключ**\n\n"
            f"```\n{vpn_data['config_link']}\n```\n\n"
            "Нажмите на синюю область один раз, чтобы скопировать ваш ключ.\n\n"
            f"📧 **Клиент:** `{vpn_data['email']}`\n\n"
            f"🆔 **UUID:** `{vpn_data['uuid']}`"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=MenuCallBack(level=0, menu_name='main').pack())]
        ])
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка при генерации ключа: {str(e)}")

# ===== КОМАНДА /newkey =====

@user_private_router.message(Command("newkey"))
async def new_key_cmd(message: types.Message, session: AsyncSession):
    user_id = message.from_user.id
    user = await session.scalar(select(User).where(User.user_id == user_id))
    if not user or not user.is_active or (user.subscription_end and user.subscription_end < datetime.now()):
        await message.answer("❌ У вас нет активной подписки. Оформите подписку через меню.")
        return
    try:
        days_left = (user.subscription_end - datetime.now()).days if user.subscription_end else 30
        if days_left <= 0:
            days_left = 30
        vpn_data = await create_or_update_vpn_client(
            user_email=str(user_id),
            days=days_left
        )
        text = (
            "🔑 **Ваш ключ**\n\n"
            f"```\n{vpn_data['config_link']}\n```\n\n"
            "Нажмите на синюю область один раз, чтобы скопировать ваш ключ.\n\n"
            f"📧 **Клиент:** `{vpn_data['email']}`\n\n"
            f"🆔 **UUID:** `{vpn_data['uuid']}`"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=MenuCallBack(level=0, menu_name='main').pack())]
        ])
        await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Ошибка при генерации ключа: {str(e)}")

# ===== ВЫБОР ТАРИФА (МЕНЮ) =====

@user_private_router.callback_query(MenuCallBack.filter(F.menu_name == "subscription"))
async def subscription_menu_callback(callback: types.CallbackQuery, callback_data: MenuCallBack):
    await callback.answer()
    text = (
        "💳  Тарифы \n\n "
        "Выберите подходящий тариф\n\n "
    )
    await safe_edit_text(
        callback,
        text,
        build_tariff_buttons(MenuCallBack(level=0, menu_name='main').pack()),
        "Markdown"
    )

# ===== НАСТРОЙКИ (ГЛАВНОЕ МЕНЮ) С ДИНАМИЧЕСКИМИ КНОПКАМИ =====

@user_private_router.callback_query(MenuCallBack.filter(F.menu_name == "settings"))
async def settings_menu(callback: types.CallbackQuery, callback_data: MenuCallBack, session: AsyncSession):
    await callback.answer()
    user_id = callback.from_user.id
    user = await session.scalar(select(User).where(User.user_id == user_id))
    settings_text = "⚙️ **Настройки**\n\nВыберите раздел:"
    buttons = [
        [InlineKeyboardButton(text="🔔 Уведомления", callback_data="settings_notifications")],
        [InlineKeyboardButton(text="❄️ Заморозить подписку", callback_data="settings_freeze")],
        [InlineKeyboardButton(text="▶️ Разморозить подписку", callback_data="settings_resume")],
    ]
    buttons.append([InlineKeyboardButton(text="🗑️ Деактивировать подписку", callback_data="settings_deactivate")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=MenuCallBack(level=0, menu_name='main').pack())])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback, settings_text, keyboard, "Markdown")

# ===== ЗАМОРОЗКА ПОДПИСКИ =====

@user_private_router.callback_query(F.data == "settings_freeze")
async def settings_freeze(callback: types.CallbackQuery):
    await callback.answer()
    text = (
        "❄️  Заморозка подписки \n\n "
        "Вы действительно хотите заморозить подписку?\n "
        "Оставшиеся дни будут сохранены и добавлены к следующему оплаченному периоду.\n\n "
        "Доступ к VPN будет временно отключён. "
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❄️ Подтвердить заморозку", callback_data="settings_freeze_confirm")],
        [InlineKeyboardButton(text="⬅️ Отмена", callback_data="settings_back")]
    ])
    await safe_edit_text(callback, text, keyboard, "Markdown")

@user_private_router.callback_query(F.data == "settings_freeze_confirm")
async def settings_freeze_confirm(callback: types.CallbackQuery, session: AsyncSession):
    await callback.answer()
    user_id = callback.from_user.id
    user = await session.scalar(select(User).where(User.user_id == user_id))
    if not user:
        await callback.message.answer("❌ Пользователь не найден.")
        return
    if not user.is_active or (user.subscription_end and user.subscription_end < datetime.now()):
        await callback.message.answer("❌ Подписка уже неактивна.")
        return
    # Вычисляем оставшиеся дни
    remaining = (user.subscription_end - datetime.now()).days
    if remaining > 0:
        user.frozen_days = (user.frozen_days or 0) + remaining
    else:
        remaining = 0
    user.is_active = False
    user.subscription_end = None
    await session.commit()
    try:
        await set_client_enable(user_id, enable=False)
        msg = "Доступ к VPN отключён. Оставшиеся дни заморожены."
    except Exception as e:
        msg = f"⚠️ Не удалось отключить клиента в панели: {str(e)}"
    await safe_edit_text(
        callback,
        f"❄️ **Подписка заморожена.**\n\n{msg}\n"
        f"Заморожено дней: {remaining}\n"
        "При следующей оплате они будут добавлены к новому сроку.",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data=MenuCallBack(level=0, menu_name='main').pack())]
        ]),
        "Markdown"
    )

# ===== ВОССТАНОВЛЕНИЕ ПОДПИСКИ =====

@user_private_router.callback_query(F.data == "settings_resume")
async def settings_resume(callback: types.CallbackQuery, session: AsyncSession):
    await callback.answer()
    user_id = callback.from_user.id
    user = await session.scalar(select(User).where(User.user_id == user_id))
    if not user:
        await callback.message.answer("❌ Пользователь не найден.")
        return
    frozen = user.frozen_days or 0
    text = (
        "▶️ **Восстановление подписки**\n\n"
        f"Замороженных дней: **{frozen}**\n\n"
        "Вы хотите восстановить подписку?\n"
        "Оставшиеся дни будут добавлены к текущей дате и доступ к VPN будет включён."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Восстановить", callback_data="settings_resume_confirm")],
        [InlineKeyboardButton(text="⬅️ Отмена", callback_data="settings_back")]
    ])
    await safe_edit_text(callback, text, keyboard, "Markdown")

@user_private_router.callback_query(F.data == "settings_resume_confirm")
async def settings_resume_confirm(callback: types.CallbackQuery, session: AsyncSession):
    await callback.answer()
    user_id = callback.from_user.id
    user = await session.scalar(select(User).where(User.user_id == user_id))
    if not user:
        await callback.message.answer("❌ Пользователь не найден.")
        return
    if not user.frozen_days or user.frozen_days <= 0:
        await callback.message.answer("❌ Нет замороженных дней для восстановления.")
        return
    frozen = user.frozen_days
    user.is_active = True
    user.subscription_end = datetime.now() + timedelta(days=frozen)
    user.frozen_days = 0
    user.deactivated_at = None
    await session.commit()
    try:
        await set_client_enable(user_id, enable=True)
        msg = "Доступ к VPN включён."
    except Exception as e:
        msg = f"⚠️ Не удалось включить клиента в панели: {str(e)}"
    expiry_str = user.subscription_end.strftime('%d.%m.%Y %H:%M')
    await safe_edit_text(
        callback,
        f"▶️ **Подписка восстановлена!**\n\n{msg}\n"
        f"📅 Действует до: **{expiry_str}**\n"
        f"Добавлено замороженных дней: **{frozen}**",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data=MenuCallBack(level=0, menu_name='main').pack())]
        ]),
        "Markdown"
    )

# ===== РАЗДЕЛ УВЕДОМЛЕНИЙ =====

@user_private_router.callback_query(F.data == "settings_notifications")
async def settings_notifications(callback: types.CallbackQuery, session: AsyncSession):
    await callback.answer()
    user_id = callback.from_user.id
    user = await session.scalar(select(User).where(User.user_id == user_id))
    if not user:
        await callback.message.answer("❌ Пользователь не найден.")
        return
    status = "🟢 включены" if user.notifications_enabled else "🔴 выключены"
    text = (
        "🔔 **Уведомления**\n\n"
        "Вы будете получать напоминание за 3 дня до окончания подписки.\n"
        f"Текущий статус: {status}\n\n"
        "Выберите действие:"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔔 Включить", callback_data="enable_notifications")],
        [InlineKeyboardButton(text="🔕 Отключить", callback_data="disable_notifications")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_back")]
    ])
    await safe_edit_text(callback, text, keyboard, "Markdown")

# ===== ВКЛЮЧИТЬ УВЕДОМЛЕНИЯ =====

@user_private_router.callback_query(F.data == "enable_notifications")
async def enable_notifications(callback: types.CallbackQuery, session: AsyncSession):
    await callback.answer()
    user_id = callback.from_user.id
    user = await session.scalar(select(User).where(User.user_id == user_id))
    if not user:
        await callback.message.answer("❌ Пользователь не найден.")
        return
    user.notifications_enabled = True
    await session.commit()
    status = "🟢 включены"
    text = (
        "🔔  Уведомления \n\n "
        "Вы будете получать напоминание за 3 дня до окончания подписки.\n "
        f"Текущий статус: {status}\n\n "
        "Выберите действие: "
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔔 Включить", callback_data="enable_notifications")],
        [InlineKeyboardButton(text="🔕 Отключить", callback_data="disable_notifications")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_back")]
    ])
    await safe_edit_text(callback, text, keyboard, "Markdown")

# ===== ОТКЛЮЧИТЬ УВЕДОМЛЕНИЯ =====

@user_private_router.callback_query(F.data == "disable_notifications")
async def disable_notifications(callback: types.CallbackQuery, session: AsyncSession):
    await callback.answer()
    user_id = callback.from_user.id
    user = await session.scalar(select(User).where(User.user_id == user_id))
    if not user:
        await callback.message.answer("❌ Пользователь не найден.")
        return
    user.notifications_enabled = False
    await session.commit()
    status = "🔴 выключены"
    text = (
        "🔔  Уведомления \n\n "
        "Вы будете получать напоминание за 3 дня до окончания подписки.\n "
        f"Текущий статус: {status}\n\n "
        "Выберите действие: "
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔔 Включить", callback_data="enable_notifications")],
        [InlineKeyboardButton(text="🔕 Отключить", callback_data="disable_notifications")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_back")]
    ])
    await safe_edit_text(callback, text, keyboard, "Markdown")

# ===== ВОЗВРАТ В ГЛАВНОЕ МЕНЮ НАСТРОЕК =====

@user_private_router.callback_query(F.data == "settings_back")
async def settings_back(callback: types.CallbackQuery, session: AsyncSession):
    await callback.answer()
    user_id = callback.from_user.id
    user = await session.scalar(select(User).where(User.user_id == user_id))
    settings_text = "⚙️ **Настройки**\n\nВыберите раздел:"
    buttons = [
        [InlineKeyboardButton(text="🔔 Уведомления", callback_data="settings_notifications")],
        [InlineKeyboardButton(text="❄️ Заморозить подписку", callback_data="settings_freeze")],
        [InlineKeyboardButton(text="▶️ Разморозить подписку", callback_data="settings_resume")],
    ]
    buttons.append([InlineKeyboardButton(text="🗑️ Деактивировать подписку", callback_data="settings_deactivate")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=MenuCallBack(level=0, menu_name='main').pack())])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await safe_edit_text(callback, settings_text, keyboard, "Markdown")

# ===== ПРЕДУПРЕЖДЕНИЕ ПЕРЕД ДЕАКТИВАЦИЕЙ =====

@user_private_router.callback_query(F.data == "settings_deactivate")
async def settings_deactivate(callback: types.CallbackQuery):
    await callback.answer()
    text = (
        "⚠️  Внимание! \n\n "
        "Вы действительно хотите деактивировать подписку?\n "
        "Это действие отключит доступ к VPN и вы не сможете пользоваться сервисом.\n\n "
        "Чтобы восстановить доступ, вам нужно будет оплатить новый тариф. "
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Да, деактивировать", callback_data="settings_delete_confirm")],
        [InlineKeyboardButton(text="⬅️ Отмена", callback_data="settings_back")]
    ])
    await safe_edit_text(callback, text, keyboard, "Markdown")

# ===== ПОДТВЕРЖДЕНИЕ ДЕАКТИВАЦИИ (без сохранения срока) =====

@user_private_router.callback_query(F.data == "settings_delete_confirm")
async def settings_delete_confirm(callback: types.CallbackQuery, session: AsyncSession):
    await callback.answer()
    user_id = callback.from_user.id
    user = await session.scalar(select(User).where(User.user_id == user_id))
    if not user:
        await callback.message.answer("❌ Пользователь не найден.")
        return
    if not user.is_active or (user.subscription_end and user.subscription_end < datetime.now()):
        await callback.message.answer("❌ Подписка уже неактивна.")
        return
    user.is_active = False
    user.subscription_end = datetime.now()
    user.frozen_days = 0  # сбрасываем замороженные дни при полной деактивации
    user.deactivated_at = datetime.now()  # запоминаем когда деактивировали
    await session.commit()
    try:
        await set_client_enable(user_id, enable=False)
        msg = "Доступ к VPN отключён."
    except Exception as e:
        msg = f"⚠️ Не удалось отключить клиента в панели: {str(e)}"
    await safe_edit_text(
        callback,
        f"🗑️ **Подписка деактивирована.**\n\n{msg}\n"
        "Статус в профиле теперь **🔴 Неактивна**.\n"
        "Для возобновления подписки оплатите любой тариф.",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data=MenuCallBack(level=0, menu_name='main').pack())]
        ]),
        "Markdown"
    )

# ===== ИНСТРУКЦИЯ =====

@user_private_router.callback_query(MenuCallBack.filter(F.menu_name == "instructions"))
async def instructions_menu(callback: types.CallbackQuery, callback_data: MenuCallBack):
    await callback.answer()
    instructions_text = (
        "📚  Инструкция \n\n "
        "Выберите устройство для настройки Kospavpn. "
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 iPhone / iPad", url="https://telegra.ph/Instrukciya-po-ustanovke-Kospavpn-07-07")],
        [InlineKeyboardButton(text="🤖 Android", url="https://telegra.ph/Instrukciya-po-ustanovke-Kospavpn-07-08")],
        [InlineKeyboardButton(text="🖥️ Windows", url="https://telegra.ph/Instrukciya-po-ustanovke-Kospavpn-07-08-2")],
        [InlineKeyboardButton(text="💻 macOS", url="https://telegra.ph/Instrukciya-po-ustanovke-Kospavpn-07-08-3")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=MenuCallBack(level=0, menu_name='main').pack())]
    ])
    await safe_edit_text(callback, instructions_text, keyboard, "Markdown")

# ===== О Kospavpn =====

@user_private_router.callback_query(MenuCallBack.filter(F.menu_name == "about"))
async def about_menu(callback: types.CallbackQuery, callback_data: MenuCallBack):
    await callback.answer()
    about_text = (
        "ℹ️  О Kospavpn\n\n"
        "<b>Кто мы?</b>\n\n"
        "Мы — независимый современный VPN-сервис, который обеспечивает безопасный и стабильный доступ к интернету. Наша единственная задача — предоставить вам качественный сервис.\n\n"
        "<b>Почему нам можно доверять?</b>\n\n"
        "Мы не собираем и не храним данные пользователей, не передаём и не продаём их третьим лицам — и не финансируемся сторонними организациями.\n\n"
        "<b>Ключевые возможности:</b>\n\n"
        "💰 От 50 ₽/мес — низкие цены\n"
        "🇷🇺 RU-приложения работают с VPN\n"
        "🚫 Нет рекламы на YouTube и сервисах\n"
        "♾️ Без ограничений по трафику\n"
        "📱 Поддержка популярных платформ\n"
        "💬 Оперативная поддержка\n"
        "💳 Оплата: СБП и крипта\n\n"
        "<b>Подписка</b>\n\n"
        "Доступ к сервису предоставляется по подписке. Вы сами выбираете срок и оплачиваете подходящий тариф — от 1 до 12 месяцев.\n\n"
        "<b>Оплата</b>\n\n"
        "Оплата доступна через СБП или криптовалюту. После совершения платежа необходимо нажать «Проверить оплату» для активации подписки.\n\n"
        "<b>Юридические документы:</b>\n\n"
        "Ниже — ссылки на документы, регулирующие использование сервиса:\n\n"
        "<b>Поддержка</b>\n\n"
        "Остались вопросы или нужна помощь с настройкой? Мы всегда на связи: @your_support"
    )
    terms_url = "https://telegra.ph/Polzovatelskoe-soglashenie-06-29-29"
    privacy_url = "https://telegra.ph/Politika-konfidencialnosti-06-29-35"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📜 Условия использования", url=terms_url)],
        [InlineKeyboardButton(text="🔒 Политика конфиденциальности", url=privacy_url)],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=MenuCallBack(level=0, menu_name='main').pack())]
    ])
    await safe_edit_text(callback, about_text, keyboard, "HTML")
