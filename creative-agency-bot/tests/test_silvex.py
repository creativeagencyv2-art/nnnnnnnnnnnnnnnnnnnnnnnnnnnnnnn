"""Run: python -m tests.test_silvex
Uses a LOCAL fake server that follows the documented endpoints. It NEVER contacts the real supplier.
The /products and /orders/ID response shapes are ASSUMED here (the docs do not show them)."""
import asyncio
import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tempfile.mkdtemp()}/test.db"

from aiohttp import web  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import ApiLog, Order, Plan, User  # noqa: E402
from app.services import deliveries, orders, poller, settings, sync, wallet  # noqa: E402
from app.supplier.base import SupplierError, SupplierTimeout  # noqa: E402
from app.supplier.silvex import SilvexClient  # noqa: E402

KEY = "test-key-123"
STATE = {"mode": "ok", "order_status": "Completed"}


def auth(request):
    if request.headers.get("x-api-key") != KEY:
        raise web.HTTPUnauthorized(text='{"error":"bad key"}')


async def products(request):
    auth(request)
    return web.json_response({"ok": True, "products": [
        {"id": "p1", "name": "NordVpn 3M Links", "price": 1.5, "stock": 19},
        {"id": "p2", "name": "Netflix Premium 1M", "price": 4.0},
        {"id": "p3", "name": "Mystery Tool", "price": 0.5, "stock": 3},
    ]})


async def order(request):
    auth(request)
    body = await request.json()
    mode = STATE["mode"]
    if mode == "timeout":
        await asyncio.sleep(2)
    if mode == "500":
        return web.Response(status=500, text="boom")
    if mode == "400":
        return web.json_response({"ok": False, "error": "Insufficient balance"}, status=400)
    if mode == "pending":
        return web.json_response({"ok": True, "orderId": "S-200", "status": "Pending", "total": 1.5, "balance": 9, "delivered": []})
    return web.json_response({"ok": True, "orderId": "S-100", "status": "Completed", "total": 1.5, "balance": 9,
                              "delivered": [{"title": "NordVpn", "content": "user:pass-SECRET"}], "echo": body})


async def get_order(request):
    auth(request)
    return web.json_response({"ok": True, "order": {"status": STATE["order_status"],
                              "delivered": [{"title": "Manual", "content": "manual-secret"}]}})


async def balance(request):
    auth(request)
    return web.json_response({"ok": True, "balance": 20.5})


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))


async def main():
    app = web.Application()
    app.add_routes([web.get("/products", products), web.post("/order", order),
                    web.get("/orders/{id}", get_order), web.get("/balance", balance)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    base = f"http://127.0.0.1:{port}"

    await init_db()
    client = SilvexClient(base, KEY, timeout=1)
    orders.supplier = client

    # balance + products + wrong key
    bal, _ = await client.get_balance()
    assert bal == 20.5
    prods = await client.list_products()
    assert [p.ref for p in prods] == ["p1", "p2", "p3"] and prods[1].stock is None
    try:
        await SilvexClient(base, "wrong").list_products()
        raise AssertionError
    except SupplierError as e:
        assert "401" in str(e) and KEY not in str(e)
    print("OK balance / products / bad key")

    # sync needs a rate first
    try:
        await sync.sync_products()
        raise AssertionError
    except sync.SyncError:
        pass
    await settings.set("usd_inr", "90")
    await settings.set("markup_pct", "30")
    sync.supplier = client
    rep = await sync.sync_products()
    assert rep.added == 3 and rep.loss == []
    async with SessionLocal() as s:
        p1 = (await s.execute(select(Plan).where(Plan.supplier_ref == "p1"))).scalar_one()
        assert p1.cost_paise == 13500 and p1.sell_price_paise == 17600 and p1.stock == 19  # 1.5*90=135 -> +30% = 175.5 -> 176
        cats = {p.product.name for p in (await s.execute(select(Plan))).scalars()} if False else None
    rep2 = await sync.sync_products()
    assert rep2.added == 0 and rep2.updated == 3
    print("OK sync: costs in INR, markup, no overwrite, idempotent")

    # purchase with delivery
    async with SessionLocal() as s, s.begin():
        s.add(User(id=7, full_name="t"))
        await wallet.credit(s, 7, 10_000_000, "admin_adjust", "test")
    pid = p1.id
    out, oid = await orders.place_order(7, pid, "a1")
    assert out == "ok"
    msgs = await deliveries.messages(oid)
    assert len(msgs) == 1 and "user:pass-SECRET" in msgs[0]
    print("OK instant order delivered and stored")

    # secrets never in api_logs, key never logged
    async with SessionLocal() as s:
        logs = " ".join(r.response + r.request for r in (await s.execute(select(ApiLog))).scalars())
    assert "SECRET" not in logs and KEY not in logs
    print("OK api_logs contain no secrets")

    # supplier says insufficient balance -> refund
    STATE["mode"] = "400"
    before = (await _bal(7))
    out, oid = await orders.place_order(7, pid, "a2")
    assert out == "refunded" and await _bal(7) == before
    print("OK supplier 400 -> customer refunded")

    # timeout and 500 -> hold, no refund
    for mode in ("timeout", "500"):
        STATE["mode"] = mode
        before = await _bal(7)
        out, oid = await orders.place_order(7, pid, f"a-{mode}")
        assert out == "needs_check" and await _bal(7) == before - 17600
    print("OK timeout / 500 -> money held for admin check")
    info = await orders.admin_refund(oid)
    assert info and await _bal(7) == before
    assert await orders.admin_refund(oid) is None
    print("OK admin refund works once")

    # pending (manual) order -> poller delivers later
    STATE["mode"] = "pending"
    out, oid = await orders.place_order(7, pid, "a3")
    assert out == "processing"
    bot = FakeBot()
    STATE["order_status"] = "Pending"
    await poller.check_once(bot)
    assert bot.sent == []
    STATE["order_status"] = "Completed"
    await poller.check_once(bot)
    assert any("manual-secret" in t for _, t in bot.sent) and all(c == 7 for c, _ in bot.sent)
    async with SessionLocal() as s:
        assert (await s.get(Order, oid)).status == "COMPLETED"
    n = len(bot.sent)
    await poller.check_once(bot)
    assert len(bot.sent) == n
    print("OK pending order delivered by poller, only once")

    # plan without supplier id cannot be bought
    async with SessionLocal() as s, s.begin():
        from app.models import Product
        pr = Product(name="X"); s.add(pr); await s.flush()
        s.add(Plan(product_id=pr.id, name="Unlinked", sell_price_paise=100, cost_paise=50))
    async with SessionLocal() as s:
        unl = (await s.execute(select(Plan).where(Plan.name == "Unlinked"))).scalar_one()
    try:
        await orders.place_order(7, unl.id, "a4")
        raise AssertionError
    except orders.OrderError:
        pass
    print("OK unlinked plan blocked before any money moves")
    await runner.cleanup()
    print("ALL SILVEX TESTS PASSED")


async def _bal(uid):
    async with SessionLocal() as s:
        return (await s.get(User, uid)).balance_paise


asyncio.run(main())
