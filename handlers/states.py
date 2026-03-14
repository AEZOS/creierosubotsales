from aiogram.fsm.state import State, StatesGroup

class AdminCategory(StatesGroup):
    waiting_for_name = State()
    waiting_for_image = State()

class AdminItem(StatesGroup):
    waiting_for_category = State()
    waiting_for_name = State()
    waiting_for_description = State()
    waiting_for_price_ron = State()
    waiting_for_image = State()

class AdminStock(StatesGroup):
    waiting_for_item = State()
    waiting_for_image = State()
    waiting_for_bundle = State()

class AdminRemoval(StatesGroup):
    waiting_for_cat_confirm = State()
    waiting_for_item_confirm = State()
    waiting_for_stock_confirm = State()

