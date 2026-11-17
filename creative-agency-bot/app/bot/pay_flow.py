import io
import re
from urllib.parse import quote

import segno
from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.types import InlineKeyboardButton as Btn

from app import config
from app.bot.handlers import ensure_user, inr, show
from app.bot.keyboards import back_home, kb
from app.services import payments
from app.services.payments import PaymentError

router = Router()
AMOUNTS = [100, 200, 500, 1000, 2000, 5000]
MIN_RS, MAX_RS = 10, 50000


class PayStates(StatesGroup):
    custom_amount = State()
    utr = State()


def payment_text(p, u) -> str:
    name = u.full_name or "-"
    uname = f"@{u.username}" if u.username else "-"
    return (
        f"💳 <b>Payment #{p.id}</b>\n"
        f"User: {name} ({uname})\nID: <code>{u.id}</code>\n"
        f"Amount: <b>{inr(p.amount_paise)}</b>\nUTR: <code>{p.utr}</code>\n"
        f"Time (UTC): {p.created_at:%d %b %H:%M}\n\n"
        f"⚠️ First check in your bank/UPI app that this UTR and amount were received."
    )


def pay_kb(payment_id: int):
    return kb([[Btn(text="✅ Approve", callback_data=f"pay:ok:{payment_id}"), Btn(text="❌ Reject", callback_data=f"pay:no:{payment_id}")]])


@router.callback_query(F.data == "menu:addbal")
async def add_balance(c: CallbackQuery, state: FSMContext):
    await state.clear()
    rows = [[Btn(text=f"₹{a:,}", callback_data=f"amt:{a}") for a in AMOUNTS[i:i + 3]] for i in range(0, len(AMOUNTS), 3)]
    rows.append([Btn(text="✏️ Custom Amount", callback_data="amt:custom")])
    rows.append([Btn(text="🏠 Main Menu", callback_data="menu:home")])
    await show(c, "➕ <b>Add Balance</b>\n\nHow much balance do you want to add?", kb(rows))


@router.callback_query(F.data.startswith("amt:"))
async def choose_amount(c: CallbackQuery, state: FSMContext):
    value = c.data.split(":")[1]
    if value == "custom":
        await state.set_state(PayStates.custom_amount)
        await c.message.answer(f"Enter the amount (between ₹{MIN_RS} and ₹{MAX_RS:,}), numbers only:")
        return await c.answer()
    await send_qr(c, state, int(value))


@router.message(StateFilter(PayStates.custom_amount))
async def custom_amount(m: Message, state: FSMContext):
    try:
        rupees = int(float((m.text or "").strip()))
        assert MIN_RS <= rupees <= MAX_RS
    except Exception:
        return await m.answer(f"❌ Send a number between ₹{MIN_RS} and ₹{MAX_RS:,}. Example: 750")
    await send_qr(m, state, rupees)


async def send_qr(event, state: FSMContext, rupees: int):
    msg = event.message if isinstance(event, CallbackQuery) else event
    if not config.UPI_ID:
        await msg.answer("❌ UPI is not set up yet. Please contact support.")
        return await state.clear()
    await state.update_data(amount_paise=rupees * 100)
    link = f"upi://pay?pa={config.UPI_ID}&pn={quote(config.BRAND)}&am={rupees}&cu=INR&tn={quote('Wallet topup')}"
    buf = io.BytesIO()
    segno.make(link, error="m").save(buf, kind="png", scale=8, border=4)
    caption = (
        f"💳 <b>Pay ₹{rupees:,}</b>\n\n"
        f"UPI ID: <code>{config.UPI_ID}</code>\n"
        f"Amount: <b>₹{rupees:,}</b> (send exactly this amount)\n\n"
        "1️⃣ Scan the QR or pay to the UPI ID\n"
        "2️⃣ After paying, tap <b>I Have Paid</b> below\n"
        "3️⃣ Send the 12-digit UTR / Transaction ID\n\n"
        "Our team will verify it and add the balance."
    )
    markup = kb([[Btn(text="✅ I Have Paid", callback_data="paid")], [Btn(text="❌ Cancel", callback_data="menu:home")]])
    await msg.answer_photo(BufferedInputFile(buf.getvalue(), "upi_qr.png"), caption=caption, reply_markup=markup, parse_mode="HTML")
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.callback_query(F.data == "paid")
async def paid(c: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("amount_paise"):
        await c.answer("Session expired. Please tap Add Balance again.", show_alert=True)
        return
    await state.set_state(PayStates.utr)
    await c.message.answer("🔢 Now send your <b>12-digit UTR / UPI Transaction ID</b> (numbers only):", parse_mode="HTML")
    await c.answer()


@router.message(StateFilter(PayStates.utr))
async def get_utr(m: Message, state: FSMContext):
    utr = (m.text or "").strip().replace(" ", "")
    if not re.fullmatch(r"\d{12}", utr):
        return await m.answer("❌ A UTR is a 12-digit number. Check your payment app and send it again.")
    data = await state.get_data()
    await ensure_user(m.from_user)
    try:
        p = await payments.create_payment(m.from_user.id, data["amount_paise"], utr)
    except PaymentError as e:
        return await m.answer(f"❌ {e}")
    await state.clear()
    await m.answer(
        f"✅ Payment request #{p.id} received.\nAmount: <b>{inr(p.amount_paise)}</b>\nUTR: <code>{utr}</code>\n\n"
        "Your balance will be added as soon as it is verified, and you will get a message.",
        reply_markup=back_home(), parse_mode="HTML",
    )
    user = await ensure_user(m.from_user)
    for admin_id in config.ADMIN_IDS:
        try:
            await m.bot.send_message(admin_id, "🔔 <b>New payment request</b>\n\n" + payment_text(p, user), reply_markup=pay_kb(p.id), parse_mode="HTML")
        except Exception:
            pass
