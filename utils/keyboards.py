from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def main_menu() -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 MAGAZIN", callback_data="menu_shop")],
        [
            InlineKeyboardButton(text="👤 PROFIL", callback_data="menu_profile"),
            InlineKeyboardButton(text="💬 SUPORT", callback_data="menu_support")
        ],
        [InlineKeyboardButton(text="⭐ RECENZII", callback_data="show_reviews_0")]
    ])
    return markup

def admin_main_menu() -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📁 + Categorie", callback_data="admin_cats"),
            InlineKeyboardButton(text="🗑 - Categorie", callback_data="admin_rem_cat")
        ],
        [
            InlineKeyboardButton(text="🛍 + Produs", callback_data="admin_items"),
            InlineKeyboardButton(text="🗑 - Produs", callback_data="admin_rem_item")
        ],
        [
            InlineKeyboardButton(text="➕ Adaugă Stoc", callback_data="admin_stock"),
            InlineKeyboardButton(text="🧹 Golește Stoc", callback_data="admin_rem_stock")
        ],
        [
            InlineKeyboardButton(text="📈 Istoric", callback_data="admin_history"),
            InlineKeyboardButton(text="⏳ Pending", callback_data="admin_pending")
        ],
        [
            InlineKeyboardButton(text="⏳ Precomenzi", callback_data="admin_preorders"),
            InlineKeyboardButton(text="💳 Adrese LTC", callback_data="admin_addresses")
        ],
        [InlineKeyboardButton(text="🔙 Ieșire", callback_data="menu_start")]
    ])
    return markup



