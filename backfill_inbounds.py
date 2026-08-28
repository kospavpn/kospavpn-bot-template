"""
backfill_inbounds.py

Одноразовый скрипт. Копирует всех клиентов из первого inbound'а (обычно
Нидерланды — тот, что был всегда) во все остальные inbound'ы на панели,
у которых их ещё нет. Нужен один раз, после того как поставите
обновлённый services/xui.py — чтобы уже купленные подписки увидели новый
сервер сразу, не дожидаясь продления.

Запуск (с сервера, из папки проекта, с активированным venv):
    cd /path/to/project
    source venv/bin/activate
    python backfill_inbounds.py

Безопасно запускать повторно — уже существующих клиентов (по email)
пропускает, дубликатов не создаст.
"""

import asyncio
import aiohttp

from services.xui import XUI_HOST, XUI_TOKEN, get_all_inbounds, get_inbound, update_inbound


async def main():
    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bearer {XUI_TOKEN}"}
        inbounds = await get_all_inbounds(session, headers)

        if len(inbounds) < 2:
            print("Найден только один inbound — синхронизировать нечего.")
            return

        source = inbounds[0]
        source_clients = source["settings"].get("clients", [])
        print(f"Источник: {source.get('remark', '?')} (id={source['id']}), клиентов: {len(source_clients)}")

        for inbound in inbounds[1:]:
            inbound_id = inbound["id"]
            full_inbound = await get_inbound(session, headers, inbound_id)
            existing_emails = {c.get("email") for c in full_inbound["settings"].get("clients", [])}

            added = 0
            for client in source_clients:
                if client.get("email") in existing_emails:
                    continue
                full_inbound["settings"]["clients"].append(client.copy())
                added += 1

            if added:
                await update_inbound(session, headers, inbound_id, full_inbound)

            print(f"{inbound.get('remark', '?')} (id={inbound_id}): добавлено {added} клиентов")

    print("Готово.")


if __name__ == "__main__":
    asyncio.run(main())
