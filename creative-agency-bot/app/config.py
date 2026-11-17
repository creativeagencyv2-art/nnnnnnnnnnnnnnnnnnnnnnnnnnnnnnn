import os
from dotenv import load_dotenv

load_dotenv()


def _ids(value: str) -> set[int]:
    return {int(x) for x in value.replace(" ", "").split(",") if x.strip().isdigit()}


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = _ids(os.getenv("ADMIN_IDS", ""))
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "support")
UPI_ID = os.getenv("UPI_ID", "")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./creative_agency.db")
BRAND = "Creative Agency"

# Supplier (Silvex AI reseller API). The key lives ONLY in .env, never in code.
SILVEX_API_KEY = os.getenv("SILVEX_API_KEY", "").strip()
SILVEX_BASE_URL = os.getenv("SILVEX_BASE_URL", "https://silvex-ai.com/api/public/reseller").rstrip("/")
