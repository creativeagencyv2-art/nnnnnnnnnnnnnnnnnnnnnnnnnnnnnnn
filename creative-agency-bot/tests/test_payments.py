"""Run: python -m tests.test_payments   (temporary database)"""
import asyncio
import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tempfile.mkdtemp()}/test.db"

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import User  # noqa: E402
from app.services import payments  # noqa: E402


async def bal(uid):
    async with SessionLocal() as s:
        return (await s.get(User, uid)).balance_paise


async def main():
    await init_db()
    async with SessionLocal() as s, s.begin():
        s.add(User(id=1, full_name="t"))

    p = await payments.create_payment(1, 50000, "111111111111")
    assert await bal(1) == 0
    print("OK pending payment adds NO money")

    try:
        await payments.create_payment(1, 50000, "111111111111")
        raise AssertionError
    except payments.PaymentError:
        print("OK duplicate UTR blocked")

    res = await asyncio.gather(*[payments.approve(p.id, 99) for _ in range(5)])
    assert sum(1 for r in res if r) == 1 and await bal(1) == 50000
    print("OK 5 parallel approvals => money added once")

    assert await payments.reject(p.id, 99) is None
    print("OK approved payment cannot be rejected")

    p2 = await payments.create_payment(1, 10000, "222222222222")
    assert await payments.reject(p2.id, 99) and await bal(1) == 50000
    print("OK reject adds no money")

    for i in range(3):
        await payments.create_payment(1, 10000, f"33333333333{i}")
    try:
        await payments.create_payment(1, 10000, "444444444444")
        raise AssertionError
    except payments.PaymentError:
        print("OK max 3 pending per user")
    print("ALL PAYMENT TESTS PASSED")


asyncio.run(main())
