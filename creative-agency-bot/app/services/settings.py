from app.db import SessionLocal
from app.models import Setting


async def get(key: str, default: str | None = None) -> str | None:
    async with SessionLocal() as s:
        row = await s.get(Setting, key)
        return row.value if row else default


async def set(key: str, value: str) -> None:
    async with SessionLocal() as s, s.begin():
        row = await s.get(Setting, key)
        if row:
            row.value = value
        else:
            s.add(Setting(key=key, value=value))
