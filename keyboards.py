from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

def get_start_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("Ro'yxatdan o'tish", request_contact=True))
    return kb

def get_confirm_sale_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("Tasdiqlash", callback_data="confirm_sale"))
    return kb
