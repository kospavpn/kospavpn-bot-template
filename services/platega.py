import os
import aiohttp
import json

MERCHANT_ID = os.environ.get("PLATEGA_MERCHANT_ID", "YOUR_MERCHANT_ID")
SECRET_KEY = os.environ.get("PLATEGA_SECRET_KEY", "YOUR_SECRET_KEY")
API_URL = "https://app.platega.io/transaction/process"


def verify_platega_signature(data: dict, signature: str) -> bool:
    """
    Проверяет подпись вебхука Platega.
    Реальную логику подпишите под документацию Platega.
    """
    # TODO: реализовать проверку хеша отправленных данных с SECRET_KEY
    return True

async def create_platega_payment(amount: float, description: str, payload: str, payment_method: int = 2):
    headers = {
        "Content-Type": "application/json",
        "X-MerchantId": MERCHANT_ID,
        "X-Secret": SECRET_KEY,
    }

    data = {
        "command": "create",
        "paymentMethod": payment_method,
        "paymentDetails": {
            "amount": amount,
            "currency": "RUB"
        },
        "description": description,
        "payload": payload,
        "return": os.environ.get("BOT_RETURN_URL", "https://t.me/your_bot"),
        "failedUrl": os.environ.get("BOT_RETURN_URL", "https://t.me/your_bot"),
    }

    print(f"🔹 Platega URL: {API_URL}")
    print(f"🔹 Platega request: {json.dumps(data, indent=2)}")

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(API_URL, json=data, headers=headers) as resp:
                result = await resp.json()
                print(f"🔹 Platega response status: {resp.status}")
                print(f"🔹 Platega response body: {json.dumps(result, indent=2)}")
                if resp.status == 200:
                    return result
                else:
                    return {"error": result.get("message", "Ошибка создания платежа"), "detail": result}
        except Exception as e:
            print(f"🔥 Exception: {e}")
            return {"error": str(e)}

async def check_platega_payment(transaction_id: str):
    url = f"https://app.platega.io/transaction/{transaction_id}"
    headers = {
        "X-MerchantId": MERCHANT_ID,
        "X-Secret": SECRET_KEY,
        "Content-Type": "application/json"
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers) as resp:
                result = await resp.json()
                if resp.status == 200:
                    return result
                else:
                    return {"error": result.get("message", "Ошибка получения статуса")}
        except Exception as e:
            return {"error": str(e)}

async def cancel_platega_payment(transaction_id: str):
    """
    Отмена платежа. Реализуйте согласно API Platega.
    Здесь заглушка – просто логирует попытку отмены.
    """
    # Пример возможной реализации (закомментирован):
    # headers = {
    #     "Content-Type": "application/json",
    #     "X-MerchantId": MERCHANT_ID,
    #     "X-Secret": SECRET_KEY,
    # }
    # data = {
    #     "command": "cancel",
    #     "transactionId": transaction_id
    # }
    # async with aiohttp.ClientSession() as session:
    #     try:
    #         async with session.post(API_URL, json=data, headers=headers) as resp:
    #             result = await resp.json()
    #             print(f"Cancel response: {result}")
    #             return result
    #     except Exception as e:
    #         print(f"Cancel exception: {e}")
    #         return {"error": str(e)}
    print(f"⚠️ Отмена платежа {transaction_id} не реализована (заглушка). Платёж останется в системе Platega.")
    return {"status": "not_implemented"}