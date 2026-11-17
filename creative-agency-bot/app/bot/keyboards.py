from aiogram.types import InlineKeyboardButton as Btn, InlineKeyboardMarkup


def kb(rows: list[list[Btn]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def main_menu() -> InlineKeyboardMarkup:
    return kb([
        [Btn(text="🛍️ Buy AI Subscriptions", callback_data="menu:buy")],
        [Btn(text="📦 My Orders", callback_data="menu:orders"), Btn(text="💰 My Wallet", callback_data="menu:wallet")],
        [Btn(text="➕ Add Balance", callback_data="menu:addbal"), Btn(text="🎁 Offers", callback_data="menu:offers")],
        [Btn(text="📞 Customer Support", callback_data="menu:support"), Btn(text="ℹ️ About Us", callback_data="menu:about")],
    ])


def back_home() -> InlineKeyboardMarkup:
    return kb([[Btn(text="🏠 Main Menu", callback_data="menu:home")]])
