from app import config
from app.supplier.dummy import DummySupplier

USING_REAL = bool(config.SILVEX_API_KEY)

if USING_REAL:
    from app.supplier.silvex import SilvexClient

    supplier = SilvexClient(config.SILVEX_BASE_URL, config.SILVEX_API_KEY)
else:
    supplier = DummySupplier()
