from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.models import Payment, User
from app.services import wallet


class PaymentError(Exception):
    pass


async def create_payment(user_id: int, amount_paise: int, utr: str) -> Payment:
    async with SessionLocal() as s:
        if (await s.execute(select(Payment.id).where(Payment.utr == utr))).first():
            raise PaymentError("This UTR has already been used. Send the correct UTR or contact support.")
        pending = (await s.execute(
            select(func.count(Payment.id)).where(Payment.user_id == user_id, Payment.status == "PENDING")
        )).scalar_one()
        if pending >= 3:
            raise PaymentError("You already have 3 payments waiting for verification. Please wait.")
    try:
        async with SessionLocal() as s, s.begin():
            p = Payment(user_id=user_id, amount_paise=amount_paise, utr=utr)
            s.add(p)
            await s.flush()
            return p
    except IntegrityError:
        raise PaymentError("This UTR has already been used.")


async def list_pending(limit: int = 10) -> list[tuple[Payment, User]]:
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(Payment, User).join(User, User.id == Payment.user_id)
            .where(Payment.status == "PENDING").order_by(Payment.id).limit(limit)
        )).all()
        return [(p, u) for p, u in rows]


async def approve(payment_id: int, admin_id: int) -> dict | None:
    """Adds money to the wallet exactly once. Returns None if already processed."""
    async with SessionLocal() as s, s.begin():
        p = await s.get(Payment, payment_id)
        if not p or p.status != "PENDING":
            return None
        info = {"user_id": p.user_id, "amount_paise": p.amount_paise, "utr": p.utr, "id": p.id}
        res = await s.execute(  # atomic: only one admin tap can win
            update(Payment).where(Payment.id == payment_id, Payment.status == "PENDING")
            .values(status="APPROVED", reviewed_by=admin_id, reviewed_at=datetime.utcnow())
        )
        if res.rowcount == 0:
            return None
        info["balance"] = await wallet.credit(
            s, p.user_id, p.amount_paise, "topup", f"Payment #{p.id} UTR {p.utr} (approved by {admin_id})"
        )
        return info


async def reject(payment_id: int, admin_id: int) -> dict | None:
    async with SessionLocal() as s, s.begin():
        p = await s.get(Payment, payment_id)
        if not p or p.status != "PENDING":
            return None
        info = {"user_id": p.user_id, "amount_paise": p.amount_paise, "utr": p.utr, "id": p.id}
        res = await s.execute(
            update(Payment).where(Payment.id == payment_id, Payment.status == "PENDING")
            .values(status="REJECTED", note=f"Rejected. UTR was {p.utr}", utr=None,
                    reviewed_by=admin_id, reviewed_at=datetime.utcnow())
        )
        return info if res.rowcount else None
