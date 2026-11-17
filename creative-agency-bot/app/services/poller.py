"""Checks supplier orders that were 'pending' (manual items) and delivers them when ready."""
import asyncio
import logging

from sqlalchemy import select

from app import config
from app.db import SessionLocal
from app.models import Order
from app.services import deliveries, orders
from app.supplier.base import SupplierError, SupplierTimeout

log = logging.getLogger("poller")
INTERVAL = 60


async def check_once(bot) -> None:
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(Order.id, Order.user_id, Order.supplier_order_id)
            .where(Order.status == "PROCESSING", Order.supplier_order_id.is_not(None)).limit(50)
        )).all()
    for oid, user_id, sup_id in rows:
        try:
            res = await orders.supplier.get_order(sup_id)
        except (SupplierError, SupplierTimeout) as e:
            log.warning("order %s check failed: %s", oid, e)
            continue
        if res.status == "COMPLETED":
            await deliveries.save(oid, res.delivered)
            async with SessionLocal() as s, s.begin():
                (await s.get(Order, oid)).status = "COMPLETED"
            await _send(bot, user_id, f"✅ <b>Order #{oid} is ready!</b>")
            for part in await deliveries.messages(oid):
                await _send(bot, user_id, part)
        elif res.status == "FAILED":
            async with SessionLocal() as s, s.begin():
                o = await s.get(Order, oid)
                o.status, o.note = "NEEDS_CHECK", f"Supplier reports status: {res.raw_status}"
            for admin_id in config.ADMIN_IDS:
                await _send(bot, admin_id, f"⚠️ Order #{oid}: supplier reports status '{res.raw_status}'. Check it in Admin → Needs Check.")


async def _send(bot, chat_id, text) -> None:
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML")
    except Exception:
        log.exception("send failed to %s", chat_id)


async def run(bot) -> None:
    while True:
        await asyncio.sleep(INTERVAL)
        try:
            await check_once(bot)
        except Exception:
            log.exception("poller error")
