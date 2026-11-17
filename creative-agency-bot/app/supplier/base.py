from dataclasses import dataclass, field


class SupplierError(Exception):
    """Supplier definitely did NOT place the order. Safe to refund the customer."""


class SupplierTimeout(Exception):
    """Outcome unknown (timeout / network / server error). Do NOT refund until checked."""


@dataclass
class SupplierResult:
    supplier_order_id: str
    status: str  # COMPLETED | PROCESSING | FAILED
    delivered: list = field(default_factory=list)  # [{"title": ..., "content": ...}]
    raw_status: str = ""


@dataclass
class SupplierProduct:
    ref: str
    title: str
    usd_price: float
    stock: int | None
    raw: dict = field(default_factory=dict)


class SupplierClient:
    """The ONLY place that talks to the external store API."""

    requires_ref = False  # True => plans must be linked to a supplier product id

    async def place_order(self, supplier_ref: str | None, our_order_id: int) -> SupplierResult:
        raise NotImplementedError

    async def get_order(self, supplier_order_id: str) -> SupplierResult:
        raise NotImplementedError

    async def get_balance(self) -> tuple[float | None, dict]:
        raise NotImplementedError

    async def list_products(self) -> list[SupplierProduct]:
        raise NotImplementedError
