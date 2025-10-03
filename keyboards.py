from aiogram.types import KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder

def get_start_keyboard():
    builder = ReplyKeyboardBuilder()
    # Здесь используем только именованные аргументы
    button = KeyboardButton(text="Ro'yxatdan o'tish", request_contact=True)
    builder.add(button)
    return builder.as_markup(resize_keyboard=True)

def get_confirm_sale_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(text="Tasdiqlash", callback_data="confirm_sale"))
    return kb
