import asyncio
import aiohttp
from services.xui import XUI_TOKEN, get_all_inbounds

async def main():
    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bearer {XUI_TOKEN}"}
        inbounds = await get_all_inbounds(session, headers)
        by_email = {}
        for ib in inbounds:
            for c in ib["settings"].get("clients", []):
                by_email.setdefault(c.get("email"), {})[ib.get("remark", ib["id"])] = c.get("subId")

        mismatches = 0
        for email, subids in by_email.items():
            values = set(subids.values())
            if len(values) > 1:
                mismatches += 1
                print(f"❌ {email}: {subids}")

        print(f"\nВсего клиентов: {len(by_email)}, с расхождением subId: {mismatches}")

asyncio.run(main())
