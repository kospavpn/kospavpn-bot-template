from aiogram.types import InputMediaPhoto, InputMediaAnimation
from sqlalchemy.ext.asyncio import AsyncSession

from database.orm_query import (
    orm_add_to_cart,
    orm_delete_from_cart,
    orm_get_banner,
    orm_get_categories,
    orm_get_products,
    orm_get_user_carts,
    orm_reduce_product_in_cart,
)
from kbds.inline import (
    get_products_btns,
    get_user_cart,
    get_user_catalog_btns,
    get_user_main_btns,
)
from utils.paginator import Paginator


def get_media(file_path: str | None, caption: str = ""):
    """Создаёт медиа-объект (фото или анимация) по расширению файла."""
    if not file_path:
        return None
    # Проверяем расширение (регистронезависимо)
    if file_path.lower().endswith(('.gif', '.mp4', '.webm')):
        return InputMediaAnimation(media=file_path, caption=caption)
    else:
        return InputMediaPhoto(media=file_path, caption=caption)


async def main_menu(session, level, menu_name, user_name=None):
    banner = await orm_get_banner(session, menu_name)
    
    if user_name:
        caption = (
            f"Здравствуйте, {user_name}.\n\n"
            "Добро пожаловать в Kospavpn.\n\n"
            "Всё необходимое для управления сервисом доступно в меню ниже."
        )
    else:
        caption = (
            "Добро пожаловать в Kospavpn.\n\n"
            "Всё необходимое для управления сервисом доступно в меню ниже."
        )
    
    media = get_media(banner.image if banner else None, caption)
    kbds = get_user_main_btns(level=level)
    return media, caption, kbds


async def catalog(session, level, menu_name):
    banner = await orm_get_banner(session, menu_name)
    caption = banner.description if banner else "Каталог"
    media = get_media(banner.image if banner else None, caption)
    categories = await orm_get_categories(session)
    kbds = get_user_catalog_btns(level=level, categories=categories)
    return media, caption, kbds


def pages(paginator: Paginator):
    btns = dict()
    if paginator.has_previous():
        btns["◀ Пред."] = "previous"
    if paginator.has_next():
        btns["След. ▶"] = "next"
    return btns


async def products(session, level, category, page):
    products = await orm_get_products(session, category_id=category)
    paginator = Paginator(products, page=page)
    product = paginator.get_page()[0]
    caption = (
        f"<strong>{product.name}</strong>\n{product.description}\n"
        f"Стоимость: {round(product.price, 2)}\n"
        f"<strong>Товар {paginator.page} из {paginator.pages}</strong>"
    )
    media = get_media(product.image, caption)
    pagination_btns = pages(paginator)
    kbds = get_products_btns(
        level=level,
        category=category,
        page=page,
        pagination_btns=pagination_btns,
        product_id=product.id,
    )
    return media, caption, kbds


async def carts(session, level, menu_name, page, user_id, product_id):
    if menu_name == "delete":
        await orm_delete_from_cart(session, user_id, product_id)
        if page > 1:
            page -= 1
    elif menu_name == "decrement":
        is_cart = await orm_reduce_product_in_cart(session, user_id, product_id)
        if page > 1 and not is_cart:
            page -= 1
    elif menu_name == "increment":
        await orm_add_to_cart(session, user_id, product_id)

    carts = await orm_get_user_carts(session, user_id)

    if not carts:
        banner = await orm_get_banner(session, "cart")
        caption = f"<strong>{banner.description}</strong>" if banner else "Корзина пуста"
        media = get_media(banner.image if banner else None, caption)
        kbds = get_user_cart(
            level=level,
            page=None,
            pagination_btns=None,
            product_id=None,
        )
        return media, caption, kbds

    paginator = Paginator(carts, page=page)
    cart = paginator.get_page()[0]
    cart_price = round(cart.quantity * cart.product.price, 2)
    total_price = round(
        sum(cart.quantity * cart.product.price for cart in carts), 2
    )
    caption = (
        f"<strong>{cart.product.name}</strong>\n"
        f"{cart.product.price}$ x {cart.quantity} = {cart_price}$\n"
        f"Товар {paginator.page} из {paginator.pages} в корзине.\n"
        f"Общая стоимость товаров в корзине {total_price}"
    )
    media = get_media(cart.product.image, caption)
    pagination_btns = pages(paginator)
    kbds = get_user_cart(
        level=level,
        page=page,
        pagination_btns=pagination_btns,
        product_id=cart.product.id,
    )
    return media, caption, kbds


async def get_menu_content(
    session: AsyncSession,
    level: int,
    menu_name: str,
    category: int | None = None,
    page: int | None = None,
    product_id: int | None = None,
    user_id: int | None = None,
    user_name: str | None = None,
):
    if level == 0:
        return await main_menu(session, level, menu_name, user_name)
    elif level == 1:
        return await catalog(session, level, menu_name)
    elif level == 2:
        return await products(session, level, category, page)
    elif level == 3:
        return await carts(session, level, menu_name, page, user_id, product_id)
    else:
        return await main_menu(session, 0, "main", user_name)