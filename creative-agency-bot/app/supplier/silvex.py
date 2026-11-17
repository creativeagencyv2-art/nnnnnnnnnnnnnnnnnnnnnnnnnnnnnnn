"""Silvex AI reseller API client.
Documented (from the supplier's docs): x-api-key header; GET /me /balance /products /orders /orders/ID;
POST /order {"productId","qty"} -> {ok, orderId, status, total, balance, delivered[{title,content}]};
errors 401 (bad key), 404 (unknown product/order), 400 (insufficient balance / no stock / invalid request).
NOT documented: the response shape of /products, /balance and /orders/ID -> parsed defensively below,
and verified by the admin command /api_check before any real selling."""
import asyncio
import json
import logging
from urllib.parse import quote

import aiohttp

from app.supplier.base import SupplierClient, SupplierError, SupplierProduct, SupplierResult, SupplierTimeout

log = logging.getLogger("silvex")

ID_KEYS = ("id", "_id", "productId", "product_id")
NAME_KEYS = ("title", "name", "productName")
PRICE_KEYS = ("price", "priceUsd", "price_usd", "usd", "cost")
STOCK_KEYS = ("stock", "available", "quantity", "qty", "inventory", "stockCount")


def norm_status(value) -> str:
    s = str(value or "").strip().lower()
    if s in ("completed", "complete", "delivered", "success", "done"):
        return "COMPLETED"
    if any(w in s for w in ("fail", "cancel", "reject", "refund")):
        return "FAILED"
    return "PROCESSING"


def _first(d: dict, keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def redact(obj):
    """Remove delivered secrets before logging."""
    if isinstance(obj, dict):
        return {k: ("[redacted]" if k == "content" else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


def _delivered(data: dict) -> list[dict]:
    items = data.get("delivered") or []
    out = []
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict):
            out.append({"title": str(it.get("title", "")), "content": str(it.get("content", ""))})
        else:
            out.append({"title": "", "content": str(it)})
    return out


class SilvexClient(SupplierClient):
    requires_ref = True

    def __init__(self, base_url: str, api_key: str, timeout: float = 20):
        self.base_url = base_url.rstrip("/")
        self._key = api_key
        self.timeout = timeout

    # ----- low level -----
    async def _log(self, method, path, req, status, resp_text):
        try:
            from app.db import SessionLocal
            from app.models import ApiLog

            try:
                shown = json.dumps(redact(json.loads(resp_text)))[:2000]
            except Exception:
                shown = (resp_text or "")[:500]
            async with SessionLocal() as s, s.begin():
                s.add(ApiLog(method=method, path=path, status_code=status,
                             request=json.dumps(req) if req else "", response=shown))
        except Exception:
            log.exception("could not write api log")

    @staticmethod
    def _msg(data, text) -> str:
        if isinstance(data, dict):
            for k in ("error", "message", "msg", "detail"):
                if data.get(k):
                    return str(data[k])[:200]
        return (text or "request failed")[:200]

    async def _request(self, method: str, path: str, body: dict | None = None):
        url = self.base_url + path
        headers = {"x-api-key": self._key}
        status, text = None, ""
        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout, connect=10)
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.request(method, url, json=body, headers=headers) as r:
                    status, text = r.status, await r.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            await self._log(method, path, body, None, f"network error: {type(e).__name__}")
            raise SupplierTimeout(f"Network error: {type(e).__name__}")
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        await self._log(method, path, body, status, text)

        if status == 200:
            if isinstance(data, dict) and data.get("ok") is False:
                raise SupplierError(self._msg(data, text))
            if data is None:
                raise SupplierTimeout("Supplier returned a non-JSON response")
            return data
        if status == 401:
            raise SupplierError("Supplier rejected our API key (401)")
        if status == 404:
            raise SupplierError("Supplier: product or order not found (404)")
        if status == 400:
            raise SupplierError(self._msg(data, text))
        raise SupplierTimeout(f"Supplier server error ({status})")  # outcome unknown

    # ----- public -----
    async def place_order(self, supplier_ref, our_order_id):
        if not supplier_ref:
            raise SupplierError("This plan is not linked to a supplier product")
        # NOTE: the API has no idempotency key, so a POST is NEVER retried automatically.
        data = await self._request("POST", "/order", {"productId": supplier_ref, "qty": 1})
        order_id = data.get("orderId")
        if not order_id:
            raise SupplierTimeout("Order response had no orderId")  # money may be spent: needs a human check
        delivered = _delivered(data)
        status = norm_status(data.get("status")) if data.get("status") else ("COMPLETED" if delivered else "PROCESSING")
        if status == "FAILED":
            status = "PROCESSING"  # never auto-refund on an ambiguous word; the poller escalates to an admin
        return SupplierResult(str(order_id), status, delivered, str(data.get("status", "")))

    async def get_order(self, supplier_order_id):
        data = await self._request("GET", f"/orders/{quote(str(supplier_order_id), safe='')}")
        node = data.get("order") if isinstance(data.get("order"), dict) else data
        raw = str(node.get("status", ""))
        return SupplierResult(str(supplier_order_id), norm_status(raw), _delivered(node), raw)

    async def get_balance(self):
        data = await self._request("GET", "/balance")
        if isinstance(data, dict):
            for k, v in data.items():
                if "balance" in k.lower() and isinstance(v, (int, float)):
                    return float(v), data
        return None, data if isinstance(data, dict) else {"response": data}

    async def fetch_products_raw(self):
        return await self._request("GET", "/products")

    async def list_products(self):
        data = await self.fetch_products_raw()
        items = data
        if isinstance(data, dict):
            items = next((data[k] for k in ("products", "items", "data", "result") if isinstance(data.get(k), list)), None)
            if items is None:
                items = next((v for v in data.values() if isinstance(v, list)), [])
        out, first_keys = [], None
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict):
                continue
            first_keys = first_keys or sorted(it.keys())
            ref, title, price = _first(it, ID_KEYS), _first(it, NAME_KEYS), _first(it, PRICE_KEYS)
            try:
                price = float(price)
            except (TypeError, ValueError):
                continue
            if ref is None or not title:
                continue
            stock = _first(it, STOCK_KEYS)
            try:
                stock = int(stock) if stock is not None else None
            except (TypeError, ValueError):
                stock = None
            out.append(SupplierProduct(str(ref), str(title), price, stock, {}))
        if not out and items:
            raise SupplierError(f"Could not read products. Fields found on first item: {first_keys}")
        return out
