from app.supplier.base import SupplierClient, SupplierResult


class DummySupplier(SupplierClient):
    """Fake supplier for testing. Always succeeds."""

    async def place_order(self, supplier_ref, our_order_id):
        return SupplierResult(supplier_order_id=f"DEMO-{our_order_id}", status="COMPLETED",
                              delivered=[{"title": "Demo item", "content": "demo-content-123"}])

    async def get_order(self, supplier_order_id):
        return SupplierResult(supplier_order_id=supplier_order_id, status="COMPLETED")

    async def get_balance(self):
        return 0.0, {"balance": 0.0}

    async def list_products(self):
        raise NotImplementedError("Dummy supplier has no products. Add SILVEX_API_KEY to .env.")
