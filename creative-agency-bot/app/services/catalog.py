from sqlalchemy import select

from app.db import SessionLocal
from app.models import Plan, Product


async def seed_demo() -> None:
    """Adds DEMO products once so you can test. Replace later from the admin panel."""
    async with SessionLocal() as s, s.begin():
        if (await s.execute(select(Product.id).limit(1))).first():
            return
        demo = {
            ("ChatGPT", "🤖"): [("ChatGPT Plus – 1 Month", "30 days", "Invite on your email", 150000, 100000)],
            ("Claude", "🧠"): [("Claude Pro – 1 Month", "30 days", "Invite on your email", 160000, 110000)],
            ("Gemini", "✨"): [("Gemini Advanced – 1 Month", "30 days", "Account link", 90000, 60000)],
            ("Perplexity", "🔎"): [("Perplexity Pro – 1 Year", "365 days", "Voucher code", 120000, 70000)],
        }
        for (name, emoji), plans in demo.items():
            p = Product(name=name, emoji=emoji)
            s.add(p)
            await s.flush()
            for pname, validity, act, sell, cost in plans:
                s.add(Plan(
                    product_id=p.id, name=pname, validity=validity, activation=act,
                    terms="DEMO plan – test data only. Replace before going live.",
                    sell_price_paise=sell, cost_paise=cost,
                ))
