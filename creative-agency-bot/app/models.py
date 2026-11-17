from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# All money is stored in paise (integer) to avoid rounding errors. 100 paise = Rs 1.


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Telegram user id
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(128), default="")
    balance_paise: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Product(Base):  # a category, e.g. ChatGPT
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    emoji: Mapped[str] = mapped_column(String(8), default="🤖")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    plans: Mapped[list["Plan"]] = relationship(back_populates="product")


class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    name: Mapped[str] = mapped_column(String(128))
    validity: Mapped[str] = mapped_column(String(64), default="")
    activation: Mapped[str] = mapped_column(String(128), default="")
    terms: Mapped[str] = mapped_column(Text, default="")
    sell_price_paise: Mapped[int] = mapped_column(Integer)  # what customer pays
    cost_paise: Mapped[int] = mapped_column(Integer, default=0)  # what supplier charges you
    stock: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = unlimited
    supplier_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    product: Mapped[Product] = relationship(back_populates="plans")


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    # FUNDS_HELD -> COMPLETED | PROCESSING | NEEDS_CHECK | REFUNDED
    status: Mapped[str] = mapped_column(String(24), default="FUNDS_HELD")
    sell_price_paise: Mapped[int] = mapped_column(Integer)
    cost_paise: Mapped[int] = mapped_column(Integer)
    supplier_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    plan: Mapped[Plan] = relationship()


class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount_paise: Mapped[int] = mapped_column(Integer)  # + credit, - debit
    balance_after_paise: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(24))  # purchase | refund | topup | admin_adjust
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Payment(Base):
    """A wallet top-up request. Only an ADMIN approving it adds money."""
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount_paise: Mapped[int] = mapped_column(Integer)
    utr: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)  # unique => same UTR can't be claimed twice
    status: Mapped[str] = mapped_column(String(16), default="PENDING")  # PENDING | APPROVED | REJECTED
    note: Mapped[str] = mapped_column(Text, default="")
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Delivery(Base):
    """Items delivered by the supplier for an order (accounts, links, codes)."""
    __tablename__ = "deliveries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ApiLog(Base):
    """Every supplier API call. The API key and delivered contents are never stored here."""
    __tablename__ = "api_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(256))
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request: Mapped[str] = mapped_column(Text, default="")
    response: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
