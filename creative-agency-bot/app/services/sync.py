import html
import math
from dataclasses import dataclass, field

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Plan, Product
from app.services import settings as settings_service
from app.supplier import supplier

# (category name, emoji, keywords matched inside the supplier product title)
BRANDS = [
    ("ChatGPT", "🤖", ["chatgpt", "openai"]), ("Claude", "🧠", ["claude", "anthropic"]),
    ("Gemini", "✨", ["gemini"]), ("Perplexity", "🔎", ["perplexity"]), ("Canva", "🎨", ["canva"]),
    ("Netflix", "🎬", ["netflix"]), ("YouTube", "▶️", ["youtube"]), ("NordVPN", "🛡️", ["nordvpn"]),
    ("Notion", "📝", ["notion"]), ("LinkedIn", "💼", ["linkedin"]), ("Microsoft 365", "🪟", ["microsoft"]),
    ("Hotstar", "📺", ["hotstar"]), ("ElevenLabs", "🎙️", ["elevenlabs"]), ("HeyGen", "🎥", ["heygen"]),
    ("Midjourney", "🖼️", ["midjourney"]),
]
FALLBACK = ("Other Tools", "📦")


class SyncError(Exception):
    pass


def pick_category(title: str) -> tuple[str, str]:
    flat = title.lower().replace(" ", "").replace("-", "")
    for name, emoji, keys in BRANDS:
        if any(k in flat for k in keys):
            return name, emoji
    return FALLBACK


@dataclass
class SyncReport:
    added: int = 0
    updated: int = 0
    unavailable: int = 0
    demo_hidden: int = 0
    loss: list = field(default_factory=list)

    def text(self) -> str:
        lines = ["🔄 <b>Sync finished</b>", "", f"➕ New plans: <b>{self.added}</b>", f"♻️ Updated: <b>{self.updated}</b>"]
        if self.unavailable:
            lines.append(f"🚫 No longer at supplier (stock set to 0): <b>{self.unavailable}</b>")
        if self.demo_hidden:
            lines.append(f"🧹 Demo plans hidden: <b>{self.demo_hidden}</b>")
        if self.loss:
            lines.append("\n⚠️ <b>Selling price is BELOW cost for:</b>")
            lines += [f"• {html.escape(n)}" for n in self.loss[:10]]
            lines.append("Fix these in Products & Plans before selling.")
        lines.append("\nNew plans get your markup automatically. Existing selling prices are never overwritten.")
        return "\n".join(lines)


async def sync_products() -> SyncReport:
    rate_s = await settings_service.get("usd_inr")
    if not rate_s:
        raise SyncError("Set the USD→INR rate first. Send: /rate 90   (1 USD = ₹90)")
    rate = float(rate_s)
    markup = float(await settings_service.get("markup_pct", "30"))

    items = await supplier.list_products()
    if not items:
        raise SyncError("The supplier returned no products.")

    rep = SyncReport()
    seen = set()
    async with SessionLocal() as s, s.begin():
        plans = {p.supplier_ref: p for p in (await s.execute(select(Plan).where(Plan.supplier_ref.is_not(None)))).scalars()}
        cats = {p.name: p for p in (await s.execute(select(Product))).scalars()}
        for it in items:
            seen.add(it.ref)
            cost = round(it.usd_price * rate * 100)
            plan = plans.get(it.ref)
            if plan:
                plan.cost_paise = cost
                if it.stock is not None:
                    plan.stock = it.stock
                elif plan.stock == 0:
                    plan.stock = None
                rep.updated += 1
            else:
                cname, emoji = pick_category(it.title)
                cat = cats.get(cname)
                if not cat:
                    cat = Product(name=cname, emoji=emoji)
                    s.add(cat)
                    await s.flush()
                    cats[cname] = cat
                sell = math.ceil(cost * (1 + markup / 100) / 100) * 100
                plan = Plan(product_id=cat.id, name=it.title[:128], sell_price_paise=sell, cost_paise=cost,
                            stock=it.stock, supplier_ref=it.ref)
                s.add(plan)
                rep.added += 1
            if plan.sell_price_paise < plan.cost_paise:
                rep.loss.append(plan.name)
        for ref, plan in plans.items():
            if ref not in seen and plan.stock != 0:
                plan.stock = 0
                rep.unavailable += 1
        demo = (await s.execute(select(Plan).where(Plan.supplier_ref.is_(None), Plan.active))).scalars().all()
        for p in demo:
            if p.terms.startswith("DEMO plan"):
                p.active = False
                rep.demo_hidden += 1
    return rep
