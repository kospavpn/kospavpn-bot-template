import math
from sqlalchemy import select, update, delete, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from database.models import Banner, Cart, Category, Product, User, Payment, Tariff, PromoCode, PromoUsage, AdminUser, Setting


############### Работа с баннерами (информационными страницами) ###############

async def orm_add_banner_description(session: AsyncSession, data: dict):
    #Добавляем новый или изменяем существующий по именам
    #пунктов меню: main, about, cart, shipping, payment, catalog
    query = select(Banner)
    result = await session.execute(query)
    if result.first():
        return
    session.add_all([Banner(name=name, description=description) for name, description in data.items()]) 
    await session.commit()


async def orm_change_banner_image(session: AsyncSession, name: str, image: str):
    query = update(Banner).where(Banner.name == name).values(image=image)
    await session.execute(query)
    await session.commit()


async def orm_get_banner(session: AsyncSession, page: str):
    query = select(Banner).where(Banner.name == page)
    result = await session.execute(query)
    return result.scalar()


async def orm_get_info_pages(session: AsyncSession):
    query = select(Banner)
    result = await session.execute(query)
    return result.scalars().all()


############################ Категории ######################################

async def orm_get_categories(session: AsyncSession):
    query = select(Category)
    result = await session.execute(query)
    return result.scalars().all()

async def orm_create_categories(session: AsyncSession, categories: list):
    query = select(Category)
    result = await session.execute(query)
    if result.first():
        return
    session.add_all([Category(name=name) for name in categories]) 
    await session.commit()

############ Админка: добавить/изменить/удалить товар ########################

async def orm_add_product(session: AsyncSession, data: dict):
    obj = Product(
        name=data["name"],
        description=data["description"],
        price=float(data["price"]),
        image=data["image"],
        category_id=int(data["category"]),
    )
    session.add(obj)
    await session.commit()


async def orm_get_products(session: AsyncSession, category_id):
    query = select(Product).where(Product.category_id == int(category_id))
    result = await session.execute(query)
    return result.scalars().all()


async def orm_get_product(session: AsyncSession, product_id: int):
    query = select(Product).where(Product.id == product_id)
    result = await session.execute(query)
    return result.scalar()


async def orm_update_product(session: AsyncSession, product_id: int, data):
    query = (
        update(Product)
        .where(Product.id == product_id)
        .values(
            name=data["name"],
            description=data["description"],
            price=float(data["price"]),
            image=data["image"],
            category_id=int(data["category"]),
        )
    )
    await session.execute(query)
    await session.commit()


async def orm_delete_product(session: AsyncSession, product_id: int):
    query = delete(Product).where(Product.id == product_id)
    await session.execute(query)
    await session.commit()

##################### Добавляем юзера в БД #####################################

async def orm_add_user(
    session: AsyncSession,
    user_id: int,
    first_name: str | None = None,
    last_name: str | None = None,
    phone: str | None = None,
):
    query = select(User).where(User.user_id == user_id)
    result = await session.execute(query)
    if result.first() is None:
        session.add(
            User(user_id=user_id, first_name=first_name, last_name=last_name, phone=phone)
        )
        await session.commit()


######################## Работа с корзинами #######################################

async def orm_add_to_cart(session: AsyncSession, user_id: int, product_id: int):
    query = select(Cart).where(Cart.user_id == user_id, Cart.product_id == product_id)
    cart = await session.execute(query)
    cart = cart.scalar()
    if cart:
        cart.quantity += 1
        await session.commit()
        return cart
    else:
        session.add(Cart(user_id=user_id, product_id=product_id, quantity=1))
        await session.commit()



async def orm_get_user_carts(session: AsyncSession, user_id):
    query = select(Cart).filter(Cart.user_id == user_id).options(joinedload(Cart.product))
    result = await session.execute(query)
    return result.scalars().all()


async def orm_delete_from_cart(session: AsyncSession, user_id: int, product_id: int):
    query = delete(Cart).where(Cart.user_id == user_id, Cart.product_id == product_id)
    await session.execute(query)
    await session.commit()


async def orm_reduce_product_in_cart(session: AsyncSession, user_id: int, product_id: int):
    query = select(Cart).where(Cart.user_id == user_id, Cart.product_id == product_id)
    cart = await session.execute(query)
    cart = cart.scalar()

    if not cart:
        return
    if cart.quantity > 1:
        cart.quantity -= 1
        await session.commit()
        return True
    else:
        await orm_delete_from_cart(session, user_id, product_id)
        await session.commit()
        return False


# ========== АДМИН-ПАНЕЛЬ VPN ORM ==========
# Добавлено для админ-панели Kospavpn

from datetime import datetime, timedelta


async def get_users_total(session: AsyncSession):
    result = await session.execute(select(func.count()).select_from(User))
    return result.scalar()


async def get_users_active(session: AsyncSession):
    stmt = select(func.count()).select_from(User).where(
        User.subscription_end >= datetime.utcnow(),
        User.is_active == True
    )
    result = await session.execute(stmt)
    return result.scalar()


async def get_users_new(session: AsyncSession, days=7):
    since = datetime.utcnow() - timedelta(days=days)
    stmt = select(func.count()).select_from(User).where(User.created >= since)
    result = await session.execute(stmt)
    return result.scalar()


async def get_users_expired(session: AsyncSession):
    stmt = select(func.count()).select_from(User).where(
        User.subscription_end < datetime.utcnow()
    )
    result = await session.execute(stmt)
    return result.scalar()


async def get_users_blocked(session: AsyncSession):
    stmt = select(func.count()).select_from(User).where(User.is_active == False)
    result = await session.execute(stmt)
    return result.scalar()


async def get_users_paginated(session: AsyncSession, offset=0, limit=10, filter_type='all'):
    stmt = select(User).order_by(desc(User.created))
    if filter_type == 'active':
        stmt = stmt.where(User.subscription_end >= datetime.utcnow(), User.is_active == True)
    elif filter_type == 'expired':
        stmt = stmt.where(User.subscription_end < datetime.utcnow())
    elif filter_type == 'blocked':
        stmt = stmt.where(User.is_active == False)
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_users_count_filtered(session: AsyncSession, filter_type='all'):
    stmt = select(func.count()).select_from(User)
    if filter_type == 'active':
        stmt = stmt.where(User.subscription_end >= datetime.utcnow(), User.is_active == True)
    elif filter_type == 'expired':
        stmt = stmt.where(User.subscription_end < datetime.utcnow())
    elif filter_type == 'blocked':
        stmt = stmt.where(User.is_active == False)
    result = await session.execute(stmt)
    return result.scalar()


async def get_user_by_user_id(session: AsyncSession, user_id: int):
    result = await session.execute(select(User).where(User.user_id == user_id))
    return result.scalar_one_or_none()


async def toggle_user_block(session: AsyncSession, user_id: int):
    user = await get_user_by_user_id(session, user_id)
    if user:
        user.is_active = not user.is_active
        await session.commit()
    return user


async def extend_user_subscription(session: AsyncSession, user_id: int, days: int):
    user = await get_user_by_user_id(session, user_id)
    if user:
        if user.subscription_end and user.subscription_end > datetime.utcnow():
            user.subscription_end += timedelta(days=days)
        else:
            user.subscription_end = datetime.utcnow() + timedelta(days=days)
        user.is_active = True
        await session.commit()
    return user


async def get_all_user_ids(session: AsyncSession):
    result = await session.execute(select(User.user_id).where(User.user_id.isnot(None)))
    return [row[0] for row in result.all()]


async def get_active_user_ids(session: AsyncSession):
    stmt = select(User.user_id).where(
        User.user_id.isnot(None),
        User.is_active == True,
        User.subscription_end >= datetime.utcnow()
    )
    result = await session.execute(stmt)
    return [row[0] for row in result.all()]


# ========== ПЛАТЕЖИ (ИСТОРИЯ ДЛЯ АДМИНКИ) ==========

async def orm_record_payment(
    session: AsyncSession,
    user_id: int,
    amount: float,
    plan: str,
    transaction_id: str,
    method: str | None = None,
    status: str = "CONFIRMED",
):
    """Записывает платёж. Повторный вызов с тем же transaction_id не создаёт дубль."""
    existing = await session.execute(
        select(Payment).where(Payment.transaction_id == transaction_id)
    )
    payment = existing.scalar_one_or_none()
    if payment:
        if payment.status != status:
            payment.status = status
            await session.commit()
        return payment
    payment = Payment(
        user_id=user_id,
        amount=float(amount),
        plan=plan,
        method=method,
        transaction_id=str(transaction_id),
        status=status,
    )
    session.add(payment)
    await session.commit()
    return payment


async def get_payments_paginated(session: AsyncSession, offset=0, limit=10):
    result = await session.execute(
        select(Payment).order_by(desc(Payment.created)).offset(offset).limit(limit)
    )
    return result.scalars().all()


async def get_payments_count(session: AsyncSession):
    result = await session.execute(select(func.count()).select_from(Payment))
    return result.scalar()


async def get_payments_summary(session: AsyncSession):
    """Возвращает словарь {period: (count, sum)} для today/7d/30d/all."""
    now = datetime.utcnow()
    periods = {
        "today": now - timedelta(days=1),
        "7d": now - timedelta(days=7),
        "30d": now - timedelta(days=30),
        "all": None,
    }
    summary = {}
    for key, since in periods.items():
        stmt = select(func.count(), func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.status == "CONFIRMED"
        )
        if since:
            stmt = stmt.where(Payment.created >= since)
        result = await session.execute(stmt)
        count, total = result.one()
        summary[key] = (int(count), float(total))
    return summary


async def get_user_payments(session: AsyncSession, user_id: int, offset=0, limit=5):
    result = await session.execute(
        select(Payment)
        .where(Payment.user_id == user_id)
        .order_by(desc(Payment.created))
        .offset(offset)
        .limit(limit)
    )
    return result.scalars().all()


async def get_user_payments_count(session: AsyncSession, user_id: int):
    result = await session.execute(
        select(func.count()).select_from(Payment).where(Payment.user_id == user_id)
    )
    return result.scalar()


async def reset_user_frozen_days(session: AsyncSession, user_id: int):
    user = await get_user_by_user_id(session, user_id)
    if user:
        frozen = user.frozen_days or 0
        user.frozen_days = 0
        await session.commit()
    return user


# ========== ТАРИФЫ (УПРАВЛЕНИЕ БЕЗ ПРАВКИ КОДА) ==========

DEFAULT_TARIFFS = [
    {"name": "1 месяц", "price": 50, "days": 30},
    {"name": "3 месяца", "price": 140, "days": 90},
    {"name": "6 месяцев", "price": 270, "days": 180},
    {"name": "12 месяцев", "price": 500, "days": 365},
]


async def seed_tariffs(session: AsyncSession):
    for t in DEFAULT_TARIFFS:
        existing = await session.execute(select(Tariff).where(Tariff.name == t["name"]))
        if not existing.scalar_one_or_none():
            session.add(Tariff(**t))
    await session.commit()


async def get_tariffs_from_db(session: AsyncSession):
    result = await session.execute(
        select(Tariff).where(Tariff.is_active == True).order_by(Tariff.days)
    )
    return result.scalars().all()


async def get_all_tariffs(session: AsyncSession):
    result = await session.execute(select(Tariff).order_by(Tariff.days))
    return result.scalars().all()


async def update_tariff(session: AsyncSession, tariff_id: int, price: float, days: int):
    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()
    if tariff:
        tariff.price = float(price)
        tariff.days = int(days)
        await session.commit()
    return tariff


# ========== ПРОМОКОДЫ ==========

async def orm_create_promo(session: AsyncSession, code: str, discount_type: str, discount_value: float, max_uses: int = 0):
    promo = PromoCode(
        code=code.upper().strip(),
        discount_type=discount_type,
        discount_value=float(discount_value),
        max_uses=int(max_uses),
    )
    session.add(promo)
    await session.commit()
    return promo


async def get_all_promos(session: AsyncSession):
    result = await session.execute(select(PromoCode).order_by(desc(PromoCode.created)))
    return result.scalars().all()


async def get_promo_by_code(session: AsyncSession, code: str):
    result = await session.execute(
        select(PromoCode).where(PromoCode.code == code.upper().strip())
    )
    return result.scalar_one_or_none()


async def toggle_promo(session: AsyncSession, promo_id: int):
    result = await session.execute(select(PromoCode).where(PromoCode.id == promo_id))
    promo = result.scalar_one_or_none()
    if promo:
        promo.is_active = not promo.is_active
        await session.commit()
    return promo


async def delete_promo(session: AsyncSession, promo_id: int):
    promo = await session.get(PromoCode, promo_id)
    if promo:
        await session.delete(promo)
        await session.commit()
    return promo


async def check_promo_usable(session: AsyncSession, promo: PromoCode, user_id: int) -> str | None:
    """None — ок, иначе строка с причиной отказа."""
    if not promo or not promo.is_active:
        return "Промокод не найден или отключён"
    if promo.max_uses and promo.used_count >= promo.max_uses:
        return "Лимит использований промокода исчерпан"
    used = await session.execute(
        select(PromoUsage).where(PromoUsage.promo_id == promo.id, PromoUsage.user_id == user_id)
    )
    if used.scalar_one_or_none():
        return "Ты уже использовал этот промокод"
    return None


async def apply_promo_usage(session: AsyncSession, promo_id: int, user_id: int):
    result = await session.execute(select(PromoCode).where(PromoCode.id == promo_id))
    promo = result.scalar_one_or_none()
    if promo:
        promo.used_count += 1
        session.add(PromoUsage(promo_id=promo.id, user_id=user_id))
        await session.commit()
    return promo


# ========== НАСТРОЙКИ (KV) ==========

async def get_setting(session: AsyncSession, key: str, default: str | None = None):
    result = await session.execute(select(Setting).where(Setting.key == key))
    row = result.scalar_one_or_none()
    return row.value if row else default


async def set_setting(session: AsyncSession, key: str, value: str):
    result = await session.execute(select(Setting).where(Setting.key == key))
    row = result.scalar_one_or_none()
    if row:
        row.value = value
    else:
        session.add(Setting(key=key, value=value))
    await session.commit()


# ========== РЕФЕРАЛКА ==========

async def set_referred_by(session: AsyncSession, user_id: int, referrer_id: int) -> bool:
    user = await get_user_by_user_id(session, user_id)
    if not user or user.referred_by:
        return False
    user.referred_by = referrer_id
    await session.commit()
    return True


async def count_referrals(session: AsyncSession, referrer_id: int) -> int:
    result = await session.execute(
        select(func.count()).select_from(User).where(User.referred_by == referrer_id)
    )
    return int(result.scalar() or 0)


# ========== АДМИНИСТРАТОРЫ (ИЗ БД) ==========

async def get_admin_users(session: AsyncSession):
    result = await session.execute(select(AdminUser).order_by(AdminUser.created))
    return result.scalars().all()


async def add_admin_user(session: AsyncSession, user_id: int, name: str | None = None):
    existing = await session.execute(select(AdminUser).where(AdminUser.user_id == user_id))
    if existing.scalar_one_or_none():
        return None
    admin = AdminUser(user_id=user_id, name=name)
    session.add(admin)
    await session.commit()
    return admin


async def remove_admin_user(session: AsyncSession, user_id: int):
    result = await session.execute(select(AdminUser).where(AdminUser.user_id == user_id))
    admin = result.scalar_one_or_none()
    if admin:
        await session.delete(admin)
        await session.commit()
    return admin
