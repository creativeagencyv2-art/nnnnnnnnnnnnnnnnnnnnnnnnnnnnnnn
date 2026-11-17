"""Run: python -m tests.test_wallet_orders   (uses a temporary database)"""
import asyncio
import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tempfile.mkdtemp()}/test.db"

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Order, Plan, User  # noqa: E402
from app.services import orders, wallet  # noqa: E402
from app.services.catalog import seed_demo  # noqa: E402
from app.supplier import dummy  # noqa: E402
from app.supplier.base import SupplierError, SupplierTimeout  # noqa: E402


async def bal(uid):
    async with SessionLocal() as s:
        return (await s.get(User, uid)).balance_paise


async def main():
    await init_db()
    await seed_demo()
    async with SessionLocal() as s, s.begin():
        s.add(User(id=1, full_name="t"))
        plan = (await s.execute(select(Plan).limit(1))).scalar_one()
    price = plan.sell_price_paise

    # 1. insufficient funds -> nothing created
    try:
        await orders.place_order(1, plan.id, "k0")
        raise AssertionError("should fail")
    except wallet.InsufficientFunds:
        pass
    async with SessionLocal() as s:
        assert not (await s.execute(select(Order))).first(), "order must not exist"
    print("OK insufficient funds blocked")

    async with SessionLocal() as s, s.begin():
        await wallet.credit(s, 1, price * 3, "admin_adjust", "test")

    # 2. normal purchase
    out, oid = await orders.place_order(1, plan.id, "k1")
    assert out == "ok" and await bal(1) == price * 2
    print("OK purchase")

    # 3. same idempotency key twice -> no second charge
    out, _ = await orders.place_order(1, plan.id, "k1")
    assert out == "duplicate" and await bal(1) == price * 2
    print("OK duplicate blocked")

    # 4. parallel taps with same key -> exactly one charge
    res = await asyncio.gather(*[orders.place_order(1, plan.id, "k2") for _ in range(5)])
    assert sum(1 for r in res if r[0] == "ok") == 1 and await bal(1) == price
    print("OK parallel double-tap safe")

    # 5. supplier rejects -> auto refund
    class Reject(dummy.DummySupplier):
        async def place_order(self, *a):
            raise SupplierError("rejected")
    orders.supplier = Reject()
    out, _ = await orders.place_order(1, plan.id, "k3")
    assert out == "refunded" and await bal(1) == price
    print("OK failed order refunded")

    # 6. supplier timeout -> money stays held, NOT refunded
    class Timeout(dummy.DummySupplier):
        async def place_order(self, *a):
            raise SupplierTimeout()
    orders.supplier = Timeout()
    out, _ = await orders.place_order(1, plan.id, "k4")
    assert out == "needs_check" and await bal(1) == 0
    print("OK timeout held for check")

    # 7. cannot overspend
    orders.supplier = dummy.DummySupplier()
    try:
        await orders.place_order(1, plan.id, "k5")
        raise AssertionError("should fail")
    except wallet.InsufficientFunds:
        pass
    assert await bal(1) == 0
    print("ALL TESTS PASSED")


asyncio.run(main())
