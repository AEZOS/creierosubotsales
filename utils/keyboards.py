from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def main_menu() -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Magazin", callback_data="menu_shop")],
        [
            InlineKeyboardButton(text="👤 Profil", callback_data="menu_profile"),
            InlineKeyboardButton(text="💬 Suport", callback_data="menu_support")
        ]

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
            InlineKeyboardButton(text="📈 Istoric Vânzări", callback_data="admin_history"),
            InlineKeyboardButton(text="⏳ Precomenzi", callback_data="admin_preorders")
        ],
        [InlineKeyboardButton(text="🔙 Ieșire", callback_data="menu_start")]
    ])
    return markup



