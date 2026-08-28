import urllib.request
import json
import urllib.error

import os

SECRET_KEY = os.environ.get("COINSO_SECRET_KEY", "YOUR_COINSO_SECRET_KEY")
PROJECT_ID = int(os.environ.get("COINSO_PROJECT_ID", "123456789"))

def create_invoice(amount: float, currency: str = "RUB", description: str = "Оплата подписки Kospavpn"):
    """
    Создаёт счёт на оплату.
    currency: "RUB" или "USDT" (или другая валюта, поддерживаемая Coinso)
    amount: сумма в указанной валюте
    """
    if amount <= 0:
        return {"error": "Сумма должна быть больше 0"}

    payload = {
        "project_id": PROJECT_ID,
        "amount": amount,
        "currency": currency,
        "description": description
    }

    data = json.dumps(payload).encode()

    req = urllib.request.Request(
        "https://coinso.io/api/payment/create",
        data=data,
        headers={
            "Authorization": f"Bearer {SECRET_KEY}",
            "Content-Type": "application/json"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            response_data = response.read().decode()
            result = json.loads(response_data)
            return result
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else ""
        return {"error": f"HTTP {e.code}: {e.reason}", "detail": error_body}
    except Exception as e:
        return {"error": str(e)}

def check_balance() -> float:
    """
    Возвращает текущий баланс кошелька в той валюте, которую вы используете.
    (предполагается, что баланс в той же валюте, что и счета)
    """
    req = urllib.request.Request(
        "https://coinso.io/api/me",
        headers={"Authorization": f"Bearer {SECRET_KEY}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            me = json.loads(response.read().decode())
            # В ответе обычно есть поле "balance" (число)
            return me.get("balance", 0.0)
    except Exception as e:
        print(f"Ошибка проверки баланса: {e}")
        return -1.0