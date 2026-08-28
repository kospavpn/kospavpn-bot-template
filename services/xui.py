"""
services/xui.py

ИЗМЕНЕНИЯ ОТНОСИТЕЛЬНО ИСХОДНОГО ФАЙЛА:
- Убран захардкоженный INBOUND_ID. Вместо этого get_all_inbounds() каждый раз
  спрашивает у панели актуальный список inbound'ов (GET /panel/api/inbounds/list).
  Когда вы добавляете новый сервер через панель — этот код НИЧЕГО не нужно
  менять, он подхватит его сам при следующем вызове.
- create_or_update_vpn_client и set_client_enable теперь проходят по ВСЕМ
  inbound'ам и создают/обновляют клиента с одним и тем же uuid+subId в каждом.
  Именно subId — это то, по чему панель склеивает несколько inbound'ов в одну
  ссылку подписки, поэтому важно, чтобы он совпадал везде.
- disable_client_subscription теперь просто алиас на set_client_enable(False),
  чтобы не держать дублирующуюся логику для двух функций.
- XUI_HOST и XUI_TOKEN читаются из переменных окружения (.env), а не хранятся в коде.

НЕ ЗАБУДЬТЕ: для уже активных подписчиков этот фикс сработает только при их
следующем продлении. Чтобы Германия появилась у них сразу — один раз запустите
backfill_inbounds.py (прислал отдельным файлом).
"""

import os
import uuid
import secrets
from datetime import datetime, timedelta

import aiohttp

XUI_HOST = os.environ["XUI_HOST"]        # например: https://your-panel-host:port/<secret_path>
XUI_TOKEN = os.environ["XUI_TOKEN"]
GROUP_ID = 1
SUBSCRIPTION_HOST = os.environ.get("SUBSCRIPTION_HOST", "https://your-server.com:2096")
SUBSCRIPTION_PATH = os.environ.get("SUBSCRIPTION_PATH", "/your-config-path/")


def generate_subid():
    return secrets.token_hex(8)


# ========== БАЗОВЫЕ ЗАПРОСЫ К ПАНЕЛИ ==========

async def get_all_inbounds(session, headers):
    url = f"{XUI_HOST}/panel/api/inbounds/list"
    async with session.get(url, headers=headers, ssl=False) as resp:
        if resp.status != 200:
            raise Exception(f"Не удалось получить список inbound'ов: {resp.status}")
        data = await resp.json()
        if not data.get("success"):
            raise Exception(f"API error: {data}")
        return data["obj"]


async def get_inbound(session, headers, inbound_id):
    url = f"{XUI_HOST}/panel/api/inbounds/get/{inbound_id}"
    async with session.get(url, headers=headers, ssl=False) as resp:
        if resp.status != 200:
            raise Exception(f"Не удалось получить inbound {inbound_id}: {resp.status}")
        data = await resp.json()
        if not data.get("success"):
            raise Exception(f"API error: {data}")
        return data["obj"]


async def update_inbound(session, headers, inbound_id, inbound):
    url = f"{XUI_HOST}/panel/api/inbounds/update/{inbound_id}"
    async with session.post(url, json=inbound, headers=headers, ssl=False) as resp:
        if resp.status != 200:
            error_text = await resp.text()
            raise Exception(f"Ошибка обновления inbound {inbound_id}: {resp.status} - {error_text}")
        result = await resp.json()
        if not result.get("success"):
            raise Exception(f"API вернул ошибку: {result}")


async def restart_xray(session, headers):
    try:
        url = f"{XUI_HOST}/panel/api/inbounds/restart"
        async with session.post(url, headers=headers, ssl=False) as resp:
            if resp.status == 200:
                return
        url2 = f"{XUI_HOST}/panel/api/xray/restart"
        async with session.post(url2, headers=headers, ssl=False) as resp2:
            if resp2.status == 200:
                return
        print("⚠️ Не удалось перезапустить Xray, но продолжаем работу")
    except Exception as e:
        print(f"⚠️ Ошибка при перезапуске Xray: {e}, но продолжаем")


async def _default_inbound_id(session, headers):
    inbounds = await get_all_inbounds(session, headers)
    if not inbounds:
        raise Exception("На панели нет ни одного inbound'а")
    return inbounds[0]["id"]


async def find_client_by_email(session, headers, email, inbound_id=None):
    """Ищет клиента по email. Без inbound_id — ищет по всем inbound'ам."""
    if inbound_id is not None:
        inbound = await get_inbound(session, headers, inbound_id)
        for c in inbound["settings"].get("clients", []):
            if c.get("email") == email:
                return c
        return None
    inbounds = await get_all_inbounds(session, headers)
    for inbound in inbounds:
        for c in inbound["settings"].get("clients", []):
            if c.get("email") == email:
                return c
    return None


# ========== СОЗДАНИЕ / ОБНОВЛЕНИЕ КЛИЕНТА (ВО ВСЕХ INBOUND'АХ) ==========

async def create_or_update_vpn_client(user_email: str, days: int = 30):
    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bearer {XUI_TOKEN}"}
        client_email = f"user_{user_email}"
        client_remark = client_email
        expiry = int((datetime.now() + timedelta(days=days)).timestamp() * 1000)

        inbounds = await get_all_inbounds(session, headers)
        if not inbounds:
            raise Exception("На панели нет ни одного inbound'а")

        # Ищем, есть ли клиент уже хоть в одном inbound — чтобы переиспользовать
        # его uuid и subId, а не плодить новые (иначе старая ссылка подписки
        # у пользователя перестанет работать при каждом продлении)
        client_uuid = None
        sub_id = None
        for inbound in inbounds:
            for c in inbound["settings"].get("clients", []):
                if c.get("email") == client_email:
                    client_uuid = c.get("id")
                    sub_id = c.get("subId")
                    break
            if client_uuid:
                break

        client_uuid = client_uuid or str(uuid.uuid4())
        sub_id = sub_id or generate_subid()

        for inbound in inbounds:
            inbound_id = inbound["id"]
            full_inbound = await get_inbound(session, headers, inbound_id)
            clients = full_inbound["settings"].setdefault("clients", [])
            found = next((c for c in clients if c.get("email") == client_email), None)

            if found:
                # продление — трогаем только срок/статус, остальные настройки
                # (flow, security и т.п.) этого конкретного inbound'а не трогаем
                found["expiryTime"] = expiry
                found["enable"] = True
                found["remark"] = client_remark
                found["id"] = client_uuid
                found["subId"] = sub_id
            else:
                # новый клиент в этом inbound'е — берём шаблон настроек с любого
                # существующего клиента ЭТОГО inbound'а, чтобы унаследовать его
                # flow/security (у разных серверов они могут отличаться)
                if clients:
                    new_client = clients[0].copy()
                else:
                    new_client = {
                        "limitIp": 2,
                        "totalGB": 0,
                        "flow": "xtls-rprx-vision",
                        "security": "reality",
                    }
                new_client["email"] = client_email
                new_client["id"] = client_uuid
                new_client["enable"] = True
                new_client["expiryTime"] = expiry
                new_client["remark"] = client_remark
                new_client["subId"] = sub_id
                new_client["groupId"] = GROUP_ID
                clients.append(new_client)

            await update_inbound(session, headers, inbound_id, full_inbound)

        config_link = f"{SUBSCRIPTION_HOST}{SUBSCRIPTION_PATH}{sub_id}"
        return {
            "email": client_email,
            "uuid": client_uuid,
            "sub_id": sub_id,
            "config_link": config_link,
            "expiry": datetime.now() + timedelta(days=days),
        }


async def set_client_enable(user_id: int, enable: bool):
    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bearer {XUI_TOKEN}"}
        client_email = f"user_{user_id}"
        client_email_no_prefix = str(user_id)

        inbounds = await get_all_inbounds(session, headers)
        touched = False
        for inbound in inbounds:
            inbound_id = inbound["id"]
            full_inbound = await get_inbound(session, headers, inbound_id)
            clients = full_inbound["settings"].get("clients", [])
            found = next(
                (c for c in clients if c.get("email") in (client_email, client_email_no_prefix)),
                None,
            )
            if not found:
                continue
            found["enable"] = enable
            if not enable:
                found["expiryTime"] = 0
            await update_inbound(session, headers, inbound_id, full_inbound)
            touched = True

        if not touched:
            raise Exception(f"Клиент с email {client_email} не найден ни в одном inbound'е")


async def disable_client_subscription(user_id: int):
    """Полностью отключает клиента во всех inbound'ах: enable=False, expiryTime=0."""
    await set_client_enable(user_id, enable=False)


# ========== ФУНКЦИИ ДЛЯ АДМИН-ПАНЕЛИ ==========
# Пока рассчитаны на один (первый) сервер — просто чтобы не падали после
# смены сигнатуры get_inbound(). Если нужна честная статистика по всем
# серверам сразу — скажите, допишу отдельно, тут уже вопрос того, как
# именно вы хотите это увидеть (по серверам отдельно или суммарно).

async def get_server_info(inbound_id: int = None):
    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bearer {XUI_TOKEN}"}
        try:
            if inbound_id is None:
                inbound_id = await _default_inbound_id(session, headers)
            inbound = await get_inbound(session, headers, inbound_id)
            name = inbound.get("remark", "Server-01")
            if not name or name == "VLESS":
                name = "Kospavpn-Server"
            status = "online" if inbound.get("enable", True) else "offline"
            clients = inbound.get("settings", {}).get("clients", [])
            total_users = len(clients)
            active_users = sum(1 for c in clients if c.get("enable", True))
            up_bytes = inbound.get("up", 0) or 0
            down_bytes = inbound.get("down", 0) or 0
            total_traffic_gb = round((up_bytes + down_bytes) / (1024**3), 2)
            max_clients = 500
            load_percent = min(int((active_users / max_clients) * 100), 100) if max_clients > 0 else 0
            return {
                "name": name,
                "status": status,
                "load": load_percent,
                "total_users": total_users,
                "active_users": active_users,
                "traffic_gb": total_traffic_gb,
                "port": inbound.get("port", "—"),
                "protocol": inbound.get("protocol", "vless").upper(),
            }
        except Exception as e:
            print(f"❌ Ошибка получения данных сервера: {e}")
            return {
                "name": "Kospavpn-Server",
                "status": "offline",
                "load": 0,
                "total_users": 0,
                "active_users": 0,
                "traffic_gb": 0,
                "port": "—",
                "protocol": "VLESS",
            }


async def restart_server():
    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bearer {XUI_TOKEN}"}
        try:
            await restart_xray(session, headers)
            return True
        except Exception as e:
            print(f"❌ Ошибка перезапуска сервера: {e}")
            return False


async def get_client_stats(inbound_id: int = None):
    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bearer {XUI_TOKEN}"}
        try:
            if inbound_id is None:
                inbound_id = await _default_inbound_id(session, headers)
            inbound = await get_inbound(session, headers, inbound_id)
            client_stats = inbound.get("clientStats", [])
            clients = inbound.get("settings", {}).get("clients", [])
            result = []
            for stat in client_stats:
                email = stat.get("email", "—")
                up = round((stat.get("up", 0) or 0) / (1024**3), 2)
                down = round((stat.get("down", 0) or 0) / (1024**3), 2)
                total = up + down
                client_info = next((c for c in clients if c.get("email") == email), {})
                is_enabled = client_info.get("enable", True)
                result.append({
                    "email": email,
                    "up_gb": up,
                    "down_gb": down,
                    "total_gb": total,
                    "enabled": is_enabled,
                })
            return result
        except Exception as e:
            print(f"❌ Ошибка получения статистики клиентов: {e}")
            return []


async def get_online_clients():
    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bearer {XUI_TOKEN}"}
        try:
            url = f"{XUI_HOST}/panel/api/inbounds/onlines"
            async with session.post(url, headers=headers, ssl=False) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("success"):
                        return data.get("obj", [])
                    print(f"⚠️ API /onlines вернул ошибку: {data}")
                    return []
                error_text = await resp.text()
                print(f"⚠️ /onlines вернул статус {resp.status}: {error_text}")
                return []
        except Exception as e:
            print(f"⚠️ Ошибка получения online клиентов: {e}")
            return []
