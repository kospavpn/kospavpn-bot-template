from aiohttp import web
import json
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database.models import User
from database.engine import session_maker
from services.platega import verify_platega_signature
from services.xui import create_or_update_vpn_client
from handlers.user_private import activate_subscription_by_user_id, get_tariffs, process_referral_bonus  # мы создадим позже
from database.orm_query import orm_record_payment

async def handle_platega_webhook(request):
    """Обработчик POST-запросов от Platega."""
    try:
        data = await request.json()
    except:
        return web.Response(status=400)

    # Проверяем подпись (если нужно)
    signature = request.headers.get("X-Signature", "")
    if not verify_platega_signature(data, signature):
        return web.Response(status=403, text="Invalid signature")

    transaction_id = data.get("transactionId")
    status = data.get("status")
    payload = data.get("payload")

    if not transaction_id or status != "CONFIRMED":
        return web.Response(status=200)  # ничего не делаем

    # Разбираем payload: ожидаем "user_id:plan"
    if ":" in payload:
        user_id_str, plan = payload.split(":", 1)
        user_id = int(user_id_str)
    else:
        return web.Response(status=200)

    # Активируем подписку
    async with session_maker() as session:
        await activate_subscription_by_user_id(session, user_id, plan)
        # Записываем платёж в историю
        try:
            price = get_tariffs().get(plan, {}).get("price")
            if price is not None:
                await orm_record_payment(
                    session,
                    user_id=user_id,
                    amount=price,
                    plan=plan,
                    transaction_id=str(transaction_id),
                    method=None,
                )
        except Exception as e:
            print(f"⚠️ Не удалось записать платёж (webhook): {e}")
        # Бонус пригласившему (без уведомления — нет контекста бота)
        try:
            await process_referral_bonus(session, user_id, bot=None)
        except Exception as e:
            print(f"⚠️ Ошибка реферального бонуса (webhook): {e}")

    return web.Response(status=200)