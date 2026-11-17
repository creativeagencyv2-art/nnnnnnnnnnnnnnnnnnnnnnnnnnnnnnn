import html
import uuid

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.types import InlineKeyboardButton as Btn
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import config
from app.bot.keyboards import back_home, kb, main_menu
from app.db import SessionLocal
from app.models import Order, Plan, Product, User, WalletTransaction
from app.services import deliveries
from app.services import orders as order_service
from app.services import wallet
from app.services.wallet import InsufficientFunds

router = Router()


def inr(paise: int) -> str:
    return f"₹{paise // 100:,}" if paise % 100 == 0 else f"₹{paise / 100:,.2f}"


async def show(event: Message | CallbackQuery, text: str, markup) -> None:
    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        except TelegramBadRequest:
            pass
        await event.answer()
    else:
        await event.answer(text, reply_markup=markup, parse_mode="HTML")


async def ensure_user(tg_user) -> User:
    async with SessionLocal() as s, s.begin():
        user = await s.get(User, tg_user.id)
        if not user:
            user = User(id=tg_user.id, username=tg_user.username, full_name=tg_user.full_name or "")
            s.add(user)
        else:
            user.username, user.full_name = tg_user.username, tg_user.full_name or ""
        return user


# ---------- Main menu ----------
async def home(event: Message | CallbackQuery):
    user = await ensure_user(event.from_user)
    text = f"Hey <b>{event.from_user.first_name}</b> 👋\nWelcome to <b>{config.BRAND}</b>!\n\n💰 Wallet: <b>{inr(user.balance_paise)}</b>\n\nWhat would you like to do?"
    await show(event, text, main_menu())


@router.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext):
    await state.clear()
    await home(m)


@router.callback_query(F.data == "menu:home")
async def cb_home(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await home(c)


# ---------- Buy flow ----------
@router.callback_query(F.data == "menu:buy")
async def buy(c: CallbackQuery):
    async with SessionLocal() as s:
        products = (await s.execute(
            select(Product).where(Product.active, Product.plans.any(Plan.active)).order_by(Product.id)
        )).scalars().all()
    rows = [[Btn(text=f"{p.emoji} {p.name}", callback_data=f"cat:{p.id}")] for p in products]
    rows.append([Btn(text="🏠 Main Menu", callback_data="menu:home")])
    text = "🛍️ <b>AI Subscriptions</b>\nWhich tool do you need?" if products else "No products are available right now."
    await show(c, text, kb(rows))


@router.callback_query(F.data.startswith("cat:"))
async def category(c: CallbackQuery):
    pid = int(c.data.split(":")[1])
    async with SessionLocal() as s:
        product = await s.get(Product, pid)
        plans = (await s.execute(select(Plan).where(Plan.product_id == pid, Plan.active).order_by(Plan.id))).scalars().all()
    rows = []
    for p in plans:
        stock = "" if p.stock is None else (f" | 📦 {p.stock}" if p.stock > 0 else " | ❌ Out of stock")
        rows.append([Btn(text=f"{p.name} | {inr(p.sell_price_paise)}{stock}", callback_data=f"plan:{p.id}")])
    rows.append([Btn(text="⬅️ Back", callback_data="menu:buy"), Btn(text="🏠 Main Menu", callback_data="menu:home")])
    await show(c, f"{product.emoji} <b>{product.name}</b>\nSelect a plan:", kb(rows))


@router.callback_query(F.data.startswith("plan:"))
async def plan_detail(c: CallbackQuery):
    plan_id = int(c.data.split(":")[1])
    async with SessionLocal() as s:
        p = await s.get(Plan, plan_id)
    text = (
        f"<b>{p.name}</b>\n\n"
        f"💵 Price: <b>{inr(p.sell_price_paise)}</b>\n"
        f"⏳ Validity: {p.validity or '-'}\n"
        f"🔑 Activation: {p.activation or '-'}\n\n"
        f"📜 <i>{p.terms or 'No special terms.'}</i>"
    )
    rows = [
        [Btn(text="✅ Buy Now", callback_data=f"ask:{p.id}")],
        [Btn(text="⬅️ Back", callback_data=f"cat:{p.product_id}"), Btn(text="🏠 Main Menu", callback_data="menu:home")],
    ]
    await show(c, text, kb(rows))


@router.callback_query(F.data.startswith("ask:"))
async def ask_confirm(c: CallbackQuery):
    plan_id = int(c.data.split(":")[1])
    user = await ensure_user(c.from_user)
    async with SessionLocal() as s:
        p = await s.get(Plan, plan_id)
    token = uuid.uuid4().hex[:12]  # one token per confirm screen => double taps can't create 2 orders
    if user.balance_paise < p.sell_price_paise:
        short = p.sell_price_paise - user.balance_paise
        text = f"❌ Insufficient balance.\n\nPrice: <b>{inr(p.sell_price_paise)}</b>\nWallet: <b>{inr(user.balance_paise)}</b>\nYou need: <b>{inr(short)}</b>"
        rows = [[Btn(text="➕ Add Balance", callback_data="menu:addbal")], [Btn(text="⬅️ Back", callback_data=f"plan:{p.id}")]]
    else:
        text = (
            f"🧾 <b>Confirm your order</b>\n\n{p.name}\nPrice: <b>{inr(p.sell_price_paise)}</b>\n"
            f"Wallet: {inr(user.balance_paise)} → <b>{inr(user.balance_paise - p.sell_price_paise)}</b>"
        )
        rows = [[Btn(text="✅ Confirm Purchase", callback_data=f"do:{p.id}:{token}")], [Btn(text="❌ Cancel", callback_data="menu:home")]]
    await show(c, text, kb(rows))


async def alert_admins(bot, text: str) -> None:
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception:
            pass


@router.callback_query(F.data.startswith("do:"))
async def do_purchase(c: CallbackQuery):
    _, plan_id, token = c.data.split(":")
    await ensure_user(c.from_user)
    try:
        outcome, order_id = await order_service.place_order(c.from_user.id, int(plan_id), f"{c.from_user.id}:{token}")
    except InsufficientFunds:
        return await show(c, "❌ Insufficient balance. Please add balance first.", kb([[Btn(text="➕ Add Balance", callback_data="menu:addbal")]]))
    except order_service.OrderError as e:
        return await show(c, f"❌ {e}", back_home())

    messages = {
        "ok": f"✅ <b>Order #{order_id} successful!</b>\nYour details are below. You can open them again anytime from My Orders.",
        "processing": f"⏳ <b>Order #{order_id} is being processed.</b>\nYou will be notified here as soon as it is delivered.",
        "needs_check": f"⚠️ <b>Order #{order_id} is under review.</b>\nWe are verifying it. Your money is safe.",
        "refunded": "❌ Your order failed. The amount has been refunded to your wallet.",
        "duplicate": "ℹ️ This order was already placed. Check My Orders.",
    }
    await show(c, messages[outcome], kb([[Btn(text="📦 My Orders", callback_data="menu:orders")], [Btn(text="🏠 Main Menu", callback_data="menu:home")]]))
    if outcome == "ok" and order_id:
        for part in await deliveries.messages(order_id):
            await c.message.answer(part, parse_mode="HTML")
    if outcome in ("refunded", "needs_check") and order_id:
        async with SessionLocal() as s:
            o = await s.get(Order, order_id)
        await alert_admins(c.bot, f"⚠️ Order #{order_id} ({outcome}) for user <code>{c.from_user.id}</code>.\nReason: {html.escape(o.note or '-')}")


# ---------- Orders ----------
STATUS = {"COMPLETED": "✅ Completed", "PROCESSING": "⏳ Processing", "NEEDS_CHECK": "⚠️ Under review", "REFUNDED": "↩️ Refunded", "FUNDS_HELD": "⏳ Pending"}


@router.callback_query(F.data == "menu:orders")
async def my_orders(c: CallbackQuery):
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(Order).options(selectinload(Order.plan)).where(Order.user_id == c.from_user.id).order_by(Order.id.desc()).limit(10)
        )).scalars().all()
    if not rows:
        return await show(c, "📦 You have no orders yet.", back_home())
    lines = [f"#{o.id} • {o.plan.name} • {inr(o.sell_price_paise)} • {STATUS.get(o.status, o.status)}" for o in rows]
    buttons = [[Btn(text=f"#{o.id} {o.plan.name[:28]}", callback_data=f"ord:{o.id}")] for o in rows]
    buttons.append([Btn(text="🏠 Main Menu", callback_data="menu:home")])
    await show(c, "📦 <b>Your recent orders</b>\nTap an order to see its details.\n\n" + "\n".join(lines), kb(buttons))


@router.callback_query(F.data.startswith("ord:"))
async def order_detail(c: CallbackQuery):
    oid = int(c.data.split(":")[1])
    async with SessionLocal() as s:
        o = (await s.execute(
            select(Order).options(selectinload(Order.plan)).where(Order.id == oid, Order.user_id == c.from_user.id)
        )).scalar_one_or_none()
    if not o:
        return await c.answer("Order not found.", show_alert=True)
    text = f"📦 <b>Order #{o.id}</b>\n{o.plan.name}\nPrice: <b>{inr(o.sell_price_paise)}</b>\nStatus: {STATUS.get(o.status, o.status)}\nDate: {o.created_at:%d %b %Y %H:%M}"
    await show(c, text, kb([[Btn(text="⬅️ My Orders", callback_data="menu:orders")], [Btn(text="🏠 Main Menu", callback_data="menu:home")]]))
    if o.status == "COMPLETED":
        for part in await deliveries.messages(o.id):
            await c.message.answer(part, parse_mode="HTML")


# ---------- Wallet ----------
@router.callback_query(F.data == "menu:wallet")
async def my_wallet(c: CallbackQuery):
    user = await ensure_user(c.from_user)
    async with SessionLocal() as s:
        txs = (await s.execute(
            select(WalletTransaction).where(WalletTransaction.user_id == c.from_user.id).order_by(WalletTransaction.id.desc()).limit(8)
        )).scalars().all()
    hist = "\n".join(f"{'➕' if t.amount_paise > 0 else '➖'} {inr(abs(t.amount_paise))} • {t.type} • {t.created_at:%d %b %H:%M}" for t in txs) or "No transactions yet."
    text = f"💰 <b>My Wallet</b>\n\nBalance: <b>{inr(user.balance_paise)}</b>\n\n<b>Recent transactions</b>\n{hist}"
    await show(c, text, kb([[Btn(text="➕ Add Balance", callback_data="menu:addbal")], [Btn(text="🏠 Main Menu", callback_data="menu:home")]]))


# ---------- Simple pages ----------
@router.callback_query(F.data == "menu:offers")
async def offers(c: CallbackQuery):
    await show(c, "🎁 <b>Offers</b>\n\nNo active offers right now. Check back soon! 🔥", back_home())


@router.callback_query(F.data == "menu:support")
async def support(c: CallbackQuery):
    await show(c, f"📞 <b>Customer Support</b>\n\nMessage us for help: @{config.SUPPORT_USERNAME}", back_home())


@router.callback_query(F.data == "menu:about")
async def about(c: CallbackQuery):
    await show(c, f"ℹ️ <b>{config.BRAND}</b>\n\nAI tool subscriptions, right inside Telegram. Fast, simple, secure.", back_home())


# ---------- Admin (temporary test command) ----------
@router.message(Command("credit"))
async def admin_credit(m: Message):
    """/credit <telegram_user_id> <rupees>  -- admin only, logged in wallet_transactions"""
    if m.from_user.id not in config.ADMIN_IDS:
        return
    try:
        _, uid, rupees = m.text.split()
        uid, paise = int(uid), int(round(float(rupees) * 100))
        assert paise > 0
    except Exception:
        return await m.answer("Use: /credit <user_id> <amount>\nExample: /credit 123456789 500")
    async with SessionLocal() as s, s.begin():
        if not await s.get(User, uid):
            return await m.answer("This user has not started the bot yet.")
        bal = await wallet.credit(s, uid, paise, "admin_adjust", f"Credited by admin {m.from_user.id}")
    await m.answer(f"✅ {inr(paise)} added. New balance: {inr(bal)}")
