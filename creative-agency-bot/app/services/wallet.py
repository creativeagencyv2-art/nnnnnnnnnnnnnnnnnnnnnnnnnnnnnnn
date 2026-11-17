from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, WalletTransaction


class InsufficientFunds(Exception):
    pass


async def _balance(s: AsyncSession, user_id: int) -> int:
    return (await s.execute(select(User.balance_paise).where(User.id == user_id))).scalar_one()


async def credit(s: AsyncSession, user_id: int, amount: int, type_: str, note: str = "", order_id: int | None = None) -> int:
    assert amount > 0
    await s.execute(update(User).where(User.id == user_id).values(balance_paise=User.balance_paise + amount))
    bal = await _balance(s, user_id)
    s.add(WalletTransaction(user_id=user_id, amount_paise=amount, balance_after_paise=bal, type=type_, note=note, order_id=order_id))
    return bal


async def debit(s: AsyncSession, user_id: int, amount: int, type_: str, note: str = "", order_id: int | None = None) -> int:
    assert amount > 0
    # Atomic: only succeeds if balance is enough, so two parallel taps can never overspend.
    res = await s.execute(
        update(User).where(User.id == user_id, User.balance_paise >= amount).values(balance_paise=User.balance_paise - amount)
    )
    if res.rowcount == 0:
        raise InsufficientFunds()
    bal = await _balance(s, user_id)
    s.add(WalletTransaction(user_id=user_id, amount_paise=-amount, balance_after_paise=bal, type=type_, note=note, order_id=order_id))
    return bal
