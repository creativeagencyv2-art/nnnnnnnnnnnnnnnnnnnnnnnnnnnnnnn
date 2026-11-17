import html

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Delivery


async def save(order_id: int, items: list[dict]) -> None:
    if not items:
        return
    async with SessionLocal() as s, s.begin():
        have = (await s.execute(select(Delivery.id).where(Delivery.order_id == order_id).limit(1))).first()
        if have:  # never store the same delivery twice
            return
        for it in items:
            s.add(Delivery(order_id=order_id, title=it.get("title", "")[:256], content=it.get("content", "")))


async def messages(order_id: int) -> list[str]:
    """Telegram-ready HTML messages with the delivered items."""
    async with SessionLocal() as s:
        rows = (await s.execute(select(Delivery).where(Delivery.order_id == order_id).order_by(Delivery.id))).scalars().all()
    out = []
    for d in rows:
        title = f"<b>{html.escape(d.title)}</b>\n" if d.title else ""
        out.append(f"🔑 {title}<code>{html.escape(d.content[:3500])}</code>")
    return out
