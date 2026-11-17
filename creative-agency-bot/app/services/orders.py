from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.models import Order, Plan
from app.services import deliveries, wallet
from app.supplier.base import SupplierError, SupplierTimeout
from app.supplier import supplier


class OrderError(Exception):
    pass


async def place_order(user_id: int, plan_id: int, idem_key: str) -> tuple[str, int | None]:
    """Returns (outcome, order_id). outcome: ok | processing | needs_check | refunded | duplicate.
    Raises OrderError / wallet.InsufficientFunds for problems before any money moved."""
    async with SessionLocal() as s:
        dup = (await s.execute(select(Order.id).where(Order.idempotency_key == idem_key))).scalar_one_or_none()
        if dup:
            return "duplicate", dup

    # Step 1: hold the money + create the order in ONE database transaction.
    try:
        async with SessionLocal() as s, s.begin():
            plan = await s.get(Plan, plan_id)
            if not plan or not plan.active:
                raise OrderError("This plan is not available right now.")
            if plan.stock is not None and plan.stock <= 0:
                raise OrderError("This plan is out of stock.")
            if getattr(supplier, "requires_ref", False) and not plan.supplier_ref:
                raise OrderError("This plan is not available right now.")
            order = Order(
                user_id=user_id, plan_id=plan.id, idempotency_key=idem_key,
                sell_price_paise=plan.sell_price_paise, cost_paise=plan.cost_paise,
            )
            s.add(order)
            await s.flush()
            await wallet.debit(s, user_id, plan.sell_price_paise, "purchase", f"Order #{order.id}", order.id)
            if plan.stock is not None:
                plan.stock -= 1
            order_id, supplier_ref = order.id, plan.supplier_ref
    except IntegrityError:
        return "duplicate", None

    # Step 2: call the supplier (outside the DB transaction).
    try:
        result = await supplier.place_order(supplier_ref, order_id)
    except SupplierTimeout:
        await _set(order_id, status="NEEDS_CHECK", note="Supplier timeout - verify before refund")
        return "needs_check", order_id
    except SupplierError as e:
        await _refund(order_id, str(e))
        return "refunded", order_id
    except Exception as e:  # unknown error: do not guess, keep money held for review
        await _set(order_id, status="NEEDS_CHECK", note=f"Unexpected error: {e!r}")
        return "needs_check", order_id

    status = "COMPLETED" if result.status == "COMPLETED" else "PROCESSING"
    await deliveries.save(order_id, result.delivered)
    await _set(order_id, status=status, supplier_order_id=result.supplier_order_id)
    return ("ok" if status == "COMPLETED" else "processing"), order_id


async def _set(order_id: int, **fields) -> None:
    async with SessionLocal() as s, s.begin():
        order = await s.get(Order, order_id)
        for k, v in fields.items():
            setattr(order, k, v)


async def _refund(order_id: int, reason: str) -> None:
    async with SessionLocal() as s, s.begin():
        order = await s.get(Order, order_id)
        if order.status == "REFUNDED":
            return
        order.status, order.note = "REFUNDED", reason
        plan = await s.get(Plan, order.plan_id)
        if plan.stock is not None:
            plan.stock += 1
        await wallet.credit(s, order.user_id, order.sell_price_paise, "refund", f"Refund order #{order.id}", order.id)


async def admin_refund(order_id: int) -> dict | None:
    """Admin refunds an order stuck in NEEDS_CHECK after checking the supplier's Orders page."""
    async with SessionLocal() as s:
        o = await s.get(Order, order_id)
        if not o or o.status != "NEEDS_CHECK":
            return None
        info = {"user_id": o.user_id, "amount_paise": o.sell_price_paise, "id": o.id}
    await _refund(order_id, "Refunded by admin after review")
    return info
