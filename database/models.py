from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text, BigInteger, Integer, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from datetime import datetime


class Base(DeclarativeBase):
    created: Mapped[DateTime] = mapped_column(DateTime, default=func.now())
    updated: Mapped[DateTime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


class Banner(Base):
    __tablename__ = 'banner'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(15), unique=True)
    image: Mapped[str] = mapped_column(String(150), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=True)


class Category(Base):
    __tablename__ = 'category'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)


class Product(Base):
    __tablename__ = 'product'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str] = mapped_column(Text)
    price: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    image: Mapped[str] = mapped_column(String(150))
    category_id: Mapped[int] = mapped_column(ForeignKey('category.id', ondelete='CASCADE'), nullable=False)

    category: Mapped['Category'] = relationship(backref='product')


class User(Base):
    __tablename__ = 'user'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    first_name: Mapped[str] = mapped_column(String(150), nullable=True)
    last_name: Mapped[str] = mapped_column(String(150), nullable=True)
    phone: Mapped[str] = mapped_column(String(13), nullable=True)

    subscription_end: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    location: Mapped[str] = mapped_column(String(100), nullable=True, default="Неизвестно")
    devices_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    subscription_plan: Mapped[str] = mapped_column(String(50), nullable=True, default=None)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    trial_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Новое поле для хранения замороженных дней
    frozen_days: Mapped[int] = mapped_column(Integer, default=0)

    # Когда подписка была деактивирована вручную
    deactivated_at: Mapped[datetime] = mapped_column(DateTime, nullable=True, default=None)

    # Реферальная система: кто пригласил этого пользователя
    referred_by: Mapped[int] = mapped_column(BigInteger, nullable=True, default=None)


class Cart(Base):
    __tablename__ = 'cart'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('user.user_id', ondelete='CASCADE'), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey('product.id', ondelete='CASCADE'), nullable=False)
    quantity: Mapped[int]

    user: Mapped['User'] = relationship(backref='cart')
    product: Mapped['Product'] = relationship(backref='cart')


class Payment(Base):
    __tablename__ = 'payment'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    plan: Mapped[str] = mapped_column(String(50), nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=True)  # sbp / crypto / None (webhook)
    transaction_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="CONFIRMED")


class Tariff(Base):
    __tablename__ = 'tariff'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    days: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class PromoCode(Base):
    __tablename__ = 'promocode'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    discount_type: Mapped[str] = mapped_column(String(10), nullable=False)  # percent / fixed
    discount_value: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 0 = без лимита
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class PromoUsage(Base):
    __tablename__ = 'promousage'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    promo_id: Mapped[int] = mapped_column(ForeignKey('promocode.id', ondelete='CASCADE'), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)


class AdminUser(Base):
    __tablename__ = 'adminuser'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=True)


class Setting(Base):
    __tablename__ = 'setting'

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(200), nullable=False)