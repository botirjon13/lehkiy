from aiogram.types import KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder

def get_start_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton("Ro'yxatdan o'tish", request_contact=True))
    return builder.as_markup(resize_keyboard=True)

def get_confirm_sale_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("Tasdiqlash", callback_data="confirm_sale"))
    return kb
