import asyncio
import os
import json
import ssl
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from dotenv import find_dotenv, load_dotenv
load_dotenv(find_dotenv())
from sqlalchemy import select
from datetime import datetime, timedelta

from middlewares.db import DataBaseSession
from database.engine import create_db, drop_db, session_maker
from database.models import User
from database.orm_query import seed_tariffs, get_setting, set_setting, get_admin_users
from handlers.user_private import user_private_router, activate_subscription_by_user_id, refresh_tariffs_cache
from handlers.user_group import user_group_router
from handlers.admin_private import admin_router
from services.xui import set_client_enable
from filters.admin import add_admin_id

bot = Bot(token=os.environ["BOT_TOKEN"], parse_mode=ParseMode.MARKDOWN)
bot.my_admins_list = [int(os.environ.get("ADMIN_ID", "123456789"))]

dp = Dispatcher()

dp.include_router(user_private_router)
dp.include_router(user_group_router)
dp.include_router(admin_router)

# ===== ФОНОВАЯ ЗАДАЧА: УВЕДОМЛЕНИЯ ЗА 3 ДНЯ =====
async def check_upcoming_expirations():
    while True:
        try:
            async with session_maker() as session:
                now = datetime.now()
                start_range = now + timedelta(days=3)
                end_range = now + timedelta(days=4)
                users = await session.scalars(
                    select(User).where(
                        User.is_active == True,
                        User.subscription_end >= start_range,
                        User.subscription_end < end_range
                    )
                )
                users = users.all()
                for user in users:
                    if user.notifications_enabled:
                        await bot.send_message(
                            user.user_id,
                            f"⏳ **Ваша подписка истекает через 3 дня!**\n\n"
                            f"Тариф: {user.subscription_plan or 'не указан'}\n"
                            f"Дата окончания: {user.subscription_end.strftime('%d.%m.%Y')}\n\n"
                            "Чтобы продлить подписку, нажмите «Продлить подписку» в профиле.\n"
                            "Если вы уже продлили, проигнорируйте это сообщение.",
                            parse_mode="Markdown"
                        )
                        await asyncio.sleep(1)
        except Exception as e:
            print(f"❌ Ошибка в фоновой задаче уведомлений: {e}")
        await asyncio.sleep(86400)

# ===== ФОНОВАЯ ЗАДАЧА: ПРОВЕРКА ИСТЕКШИХ ПОДПИСОК =====
async def check_expired_subscriptions():
    while True:
        try:
            async with session_maker() as session:
                now = datetime.now()
                expired_users = await session.scalars(
                    select(User).where(
                        User.is_active == True,
                        User.subscription_end < now
                    )
                )
                expired_users = expired_users.all()
                if expired_users:
                    print(f"🕒 Найдено {len(expired_users)} пользователей с истекшей подпиской.")
                    for user in expired_users:
                        try:
                            user.is_active = False
                            await set_client_enable(user.user_id, enable=False)
                            print(f"✅ Клиент для user_id {user.user_id} отключён.")
                        except Exception as e:
                            print(f"❌ Ошибка при отключении клиента для user_id {user.user_id}: {e}")
                    await session.commit()
        except Exception as e:
            print(f"❌ Ошибка в фоновой задаче проверки истекших: {e}")
        await asyncio.sleep(3600)

# ===== ВЕБХУК ДЛЯ PLATEGA =====
async def handle_platega_webhook(request):
    try:
        data = await request.json()
        headers = request.headers

        status = data.get("status")
        transaction_id = data.get("transactionId")
        payload = data.get("payload")

        print(f"🔔 Webhook Platega: status={status}, transaction={transaction_id}, payload={payload}")

        if status == "CONFIRMED" and payload:
            # Парсим payload: ожидается "user_id:plan"
            if ":" in payload:
                user_id_str, plan = payload.split(":", 1)
                user_id = int(user_id_str)
            else:
                user_id = int(payload)
                plan = "1 месяц"

            async with session_maker() as session:
                vpn_data = await activate_subscription_by_user_id(session, user_id, plan)
                if vpn_data:
                    print(f"✅ Подписка для пользователя {user_id} активирована (план: {plan})")
                    # Можно отправить уведомление пользователю, но не обязательно
                else:
                    print(f"⚠️ Ошибка активации подписки для пользователя {user_id}")

        return web.Response(text="OK", status=200)
    except Exception as e:
        print(f"❌ Ошибка в вебхуке: {e}")
        return web.Response(text="ERROR", status=500)

# ===== НАСТРОЙКА ВЕБ-СЕРВЕРА С SSL =====
async def start_web_server():
    web_app = web.Application()
    web_app.router.add_post("/platega/webhook", handle_platega_webhook)

    # SSL-контекст для HTTPS
    ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ssl_context.load_cert_chain(
        os.environ.get("SSL_CERT_PATH", "/path/to/fullchain.pem"),
        os.environ.get("SSL_KEY_PATH", "/path/to/privkey.pem")
    )

    runner = web.AppRunner(web_app)
    await runner.setup()
    webhook_port = int(os.environ.get("WEBHOOK_PORT", "8443"))
    site = web.TCPSite(runner, "0.0.0.0", webhook_port, ssl_context=ssl_context)
    await site.start()
    print(f"🌐 Веб-сервер для вебхуков запущен на порту {webhook_port}")
    return runner

# ===== ФУНКЦИИ ЗАПУСКА/ОСТАНОВКИ =====
async def on_startup(bot):
    # await drop_db()
    await create_db()
    # Сиды и загрузка конфигурации в память
    async with session_maker() as session:
        await seed_tariffs(session)
        if await get_setting(session, "ref_bonus_days") is None:
            await set_setting(session, "ref_bonus_days", "7")
        await refresh_tariffs_cache(session)
        for admin in await get_admin_users(session):
            add_admin_id(admin.user_id)
    asyncio.create_task(check_upcoming_expirations())
    asyncio.create_task(check_expired_subscriptions())

async def on_shutdown(bot):
    print('бот остановился')

# ===== ГЛАВНАЯ ФУНКЦИЯ =====
async def main():
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    dp.update.middleware(DataBaseSession(session_pool=session_maker))

    runner = await start_web_server()

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        print("🔄 Остановка веб-сервера...")
        await runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())