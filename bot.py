# bot.py
import os
import re
import io
import json
import qrcode
import psycopg2
import pandas as pd
from datetime import datetime
from urllib.parse import urlparse
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from PIL import Image
import telebot
from telebot import types
import locale
import math

# --- Load env ---
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
STORE_LOCATION_NAME = os.getenv("STORE_LOCATION_NAME", "Do'kon")
SELLER_PHONE = os.getenv("SELLER_PHONE", "+998330131992")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Tashkent")

if not TOKEN or not DATABASE_URL:
    raise SystemExit("Iltimos TELEGRAM_TOKEN va DATABASE_URL ni .env ga qo'ying")

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

# --- DB helpers ---
def get_conn():
    url = urlparse(DATABASE_URL)
    return psycopg2.connect(
        dbname=url.path[1:],
        user=url.username,
        password=url.password,
        host=url.hostname,
        port=url.port
    )

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    sql = open("db_init.sql", "r", encoding="utf-8").read()
    cur.execute(sql)
    conn.commit()
    cur.close()
    conn.close()

# --- Utility: lots of small helpers ---
LATIN_PATTERN = re.compile(r'[А-Яа-яЁёҢғқўҳӏ]|[ЁёЉћ]', flags=re.UNICODE)

def check_latin(text: str):
    """Return True if contains Cyrillic (disallow)."""
    return bool(LATIN_PATTERN.search(text))

def format_money(v):
    try:
        return f"{int(v):,}".replace(",", ".") + " so'm"
    except:
        return str(v)

def now_str():
    # Uzbekistan timezone assumed (Asia/Tashkent)
    # For simplicity we generate naive now in UTC+5
    dt = datetime.utcnow()
    # add 5 hours
    dt = dt.replace(microsecond=0) + pd.Timedelta(hours=5)
    return dt.strftime("%d.%m.%Y %H:%M:%S")

# --- Keyboards ---
def main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    kb.row(types.KeyboardButton("🔹 Yangi mahsulot qo'shish"))
    kb.row(types.KeyboardButton("🛒 Mahsulot sotish"))
    kb.row(types.KeyboardButton("📊 Statistika"), types.KeyboardButton("📋 Qarzdorlar ro'yxati"))
    return kb

def cancel_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row(types.KeyboardButton("Bekor qilish"))
    return kb

def yes_no_inline():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Ha", callback_data="yes"), types.InlineKeyboardButton("Yo'q", callback_data="no"))
    return kb

# --- Conversation states simple dict (in-memory minimal) ---
# For production use FSM or DB-backed states. Here light-weight per-user dict:
USER_STATE = {}

def set_state(user_id, key, value):
    USER_STATE.setdefault(user_id, {})[key] = value

def get_state(user_id, key, default=None):
    return USER_STATE.get(user_id, {}).get(key, default)

def clear_state(user_id):
    USER_STATE.pop(user_id, None)

# --- Commands & handlers ---
@bot.message_handler(commands=['start'])
def cmd_start(m):
    uid = m.from_user.id
    clear_state(uid)
    txt = ("Assalomu alaykum! 👋\n\n"
           "Quyidagi menyudan tanlang:\n")
    bot.send_message(m.chat.id, txt, reply_markup=main_keyboard())

@bot.message_handler(func=lambda m: m.text == "🔹 Yangi mahsulot qo'shish")
def start_add_product(m):
    uid = m.from_user.id
    clear_state(uid)
    set_state(uid, "action", "add_product_name")
    bot.send_message(m.chat.id, "Mahsulot nomini kiriting (lotin harflarda):", reply_markup=cancel_keyboard())

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "add_product_name")
def add_product_name(m):
    uid = m.from_user.id
    text = m.text.strip()
    if text.lower() == "bekor qilish":
        clear_state(uid)
        bot.send_message(m.chat.id, "Amal bekor qilindi.", reply_markup=main_keyboard())
        return
    if check_latin(text):
        bot.send_message(m.chat.id, "Iltimos faqat lotin alifbosida kiriting. (Masalan: Olma, Kalodka)", reply_markup=cancel_keyboard())
        return
    set_state(uid, "new_product_name", text)
    set_state(uid, "action", "add_product_qty")
    bot.send_message(m.chat.id, "Mahsulot miqdorini son bilan kiriting (masalan: 100):", reply_markup=cancel_keyboard())

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "add_product_qty")
def add_product_qty(m):
    uid = m.from_user.id
    txt = m.text.strip()
    if txt.lower() == "bekor qilish":
        clear_state(uid)
        bot.send_message(m.chat.id, "Amal bekor qilindi.", reply_markup=main_keyboard())
        return
    if not txt.isdigit():
        bot.send_message(m.chat.id, "Iltimos butun son kiriting (masalan: 50).", reply_markup=cancel_keyboard())
        return
    set_state(uid, "new_product_qty", int(txt))
    set_state(uid, "action", "add_product_cost")
    bot.send_message(m.chat.id, "Optovikdan olingan narxini kiriting (so'm):", reply_markup=cancel_keyboard())

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "add_product_cost")
def add_product_cost(m):
    uid = m.from_user.id
    txt = m.text.strip().replace(" ", "").replace(",", "")
    if txt.lower() == "bekor qilish":
        clear_state(uid)
        bot.send_message(m.chat.id, "Amal bekor qilindi.", reply_markup=main_keyboard())
        return
    if not txt.isdigit():
        bot.send_message(m.chat.id, "Iltimos raqam kiriting (masalan: 120000).", reply_markup=cancel_keyboard())
        return
    set_state(uid, "new_product_cost", int(txt))
    set_state(uid, "action", "add_product_suggest")
    bot.send_message(m.chat.id, "Taxminiy sotish narxini kiriting (so'm). Sotish vaqtida o'zgartirish mumkin:", reply_markup=cancel_keyboard())

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "add_product_suggest")
def add_product_suggest(m):
    uid = m.from_user.id
    txt = m.text.strip().replace(" ", "").replace(",", "")
    if txt.lower() == "bekor qilish":
        clear_state(uid)
        bot.send_message(m.chat.id, "Amal bekor qilindi.", reply_markup=main_keyboard())
        return
    if not txt.isdigit():
        bot.send_message(m.chat.id, "Iltimos raqam kiriting (masalan: 150000).", reply_markup=cancel_keyboard())
        return
    # save to DB
    name = get_state(uid, "new_product_name")
    qty = get_state(uid, "new_product_qty")
    cost = get_state(uid, "new_product_cost")
    suggest = int(txt)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO products (name, qty, cost_price, suggest_price) VALUES (%s, %s, %s, %s) RETURNING id;",
                (name, qty, cost, suggest))
    pid = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    clear_state(uid)
    bot.send_message(m.chat.id, f"✅ Mahsulot qo'shildi: <b>{name}</b>\nID: {pid}\nMiqdor: {qty}\nNarx (opt): {format_money(cost)}\nTaklifiy: {format_money(suggest)}",
                     reply_markup=main_keyboard())

# --- Search & sell ---
@bot.message_handler(func=lambda m: m.text == "🛒 Mahsulot sotish")
def start_sell(m):
    uid = m.from_user.id
    clear_state(uid)
    set_state(uid, "action", "sell_search")
    # clear cart in DB
    clear_user_cart(uid)
    bot.send_message(m.chat.id, "Qaysi mahsulotni izlamoqchisiz? (nom yoki uning bir qismi, lotincha):", reply_markup=cancel_keyboard())

def clear_user_cart(uid):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM user_carts WHERE user_id=%s", (uid,))
    conn.commit()
    cur.close()
    conn.close()

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "sell_search")
def sell_search(m):
    uid = m.from_user.id
    txt = m.text.strip()
    if txt.lower() == "bekor qilish":
        clear_state(uid)
        clear_user_cart(uid)
        bot.send_message(m.chat.id, "Savdo bekor qilindi.", reply_markup=main_keyboard())
        return
    if check_latin(txt):
        bot.send_message(m.chat.id, "Iltimos faqat lotincha kiriting.", reply_markup=cancel_keyboard())
        return
    # search DB
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, name, qty, suggest_price FROM products WHERE name ILIKE %s ORDER BY id;",
                (f"%{txt}%",))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    if not rows:
        bot.send_message(m.chat.id, "Mahsulot topilmadi. Yana urinib ko'ring yoki 'Bekor qilish' ni tanlang.", reply_markup=cancel_keyboard())
        return
    # show matches with inline buttons "Savatga qo'shish"
    kb = types.InlineKeyboardMarkup()
    for r in rows:
        txtbtn = f"{r['name']} -> {format_money(r['suggest_price'])} ({r['qty']} dona)"
        kb.add(types.InlineKeyboardButton(txtbtn, callback_data=f"addcart|{r['id']}"))
    kb.add(types.InlineKeyboardButton("Savatchaga o‘tish", callback_data="view_cart"))
    kb.add(types.InlineKeyboardButton("Yana izlash", callback_data="again_search"))
    bot.send_message(m.chat.id, "Topilgan mahsulotlar:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("addcart|"))
def cb_addcart(c):
    uid = c.from_user.id
    _, pid = c.data.split("|")
    pid = int(pid)
    # get product info
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, name, qty, suggest_price FROM products WHERE id=%s;", (pid,))
    p = cur.fetchone()
    cur.close()
    conn.close()
    if not p:
        bot.answer_callback_query(c.id, "Mahsulot topilmadi.")
        return
    # ask qty and price
    set_state(uid, "action", "addcart_fill")
    set_state(uid, "addcart_pid", pid)
    bot.send_message(c.message.chat.id, f"Mahsulot: <b>{p['name']}</b>\nMavjud: {p['qty']}\nTaklifiy narx: {format_money(p['suggest_price'])}\n\nSotiladigan miqdorni kiriting (son):", parse_mode="HTML", reply_markup=cancel_keyboard())
    bot.answer_callback_query(c.id)

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "addcart_fill")
def addcart_fill(m):
    uid = m.from_user.id
    txt = m.text.strip()
    if txt.lower() == "bekor qilish":
        clear_state(uid)
        bot.send_message(m.chat.id, "Amal bekor qilindi.", reply_markup=main_keyboard())
        return
    if not txt.isdigit():
        bot.send_message(m.chat.id, "Iltimos butun son kiriting (masalan: 2).", reply_markup=cancel_keyboard())
        return
    qty = int(txt)
    pid = get_state(uid, "addcart_pid")
    # get product sugg price to prefill price
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, name, qty, suggest_price FROM products WHERE id=%s;", (pid,))
    p = cur.fetchone()
    cur.close()
    conn.close()
    if not p:
        bot.send_message(m.chat.id, "Mahsulot topilmadi.", reply_markup=main_keyboard())
        clear_state(uid)
        return
    if qty > p['qty']:
        bot.send_message(m.chat.id, f"Mavjud miqdor yetarli emas. Mavjud: {p['qty']}", reply_markup=cancel_keyboard())
        return
    set_state(uid, "addcart_qty", qty)
    set_state(uid, "action", "addcart_price")
    bot.send_message(m.chat.id, f"Sotiladigan narxni kiriting (so'm). Taklifiy: {format_money(p['suggest_price'])}", reply_markup=cancel_keyboard())
@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "addcart_price")
def addcart_price(m):
    uid = m.from_user.id
    txt = m.text.strip().replace(" ", "").replace(",", "")
    if txt.lower() == "bekor qilish":
        clear_state(uid)
        bot.send_message(m.chat.id, "Amal bekor qilindi.", reply_markup=main_keyboard())
        return
    if not txt.isdigit():
        bot.send_message(m.chat.id, "Iltimos raqam kiriting (masalan: 120000).", reply_markup=cancel_keyboard())
        return
    price = int(txt)
    pid = get_state(uid, "addcart_pid")
    qty = get_state(uid, "addcart_qty")

    # Mahsulot ma’lumotlarini olish
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT name FROM products WHERE id=%s;", (pid,))
    pname = cur.fetchone()['name']

    # Savatchaga qo‘shish
    cur.execute("SELECT data FROM user_carts WHERE user_id=%s;", (uid,))
    row = cur.fetchone()
    if row:
        data = row['data']
    else:
        data = {"items": []}

    item = {"product_id": pid, "name": pname, "qty": qty, "price": price}
    data['items'].append(item)

    if row:
        cur.execute("UPDATE user_carts SET data=%s, updated_at=now() WHERE user_id=%s;", (json.dumps(data), uid))
    else:
        cur.execute("INSERT INTO user_carts (user_id, data) VALUES (%s, %s);", (uid, json.dumps(data)))
    conn.commit()
    cur.close()
    conn.close()

    clear_state(uid)

    # 🔹 Inline tugmalarni yaratish
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("➕ Yana mahsulot qo‘shish", callback_data="again_search"))
    kb.add(types.InlineKeyboardButton("🧺 Savatchaga o‘tish", callback_data="view_cart"))
    kb.add(types.InlineKeyboardButton("❌ Savdoni bekor qilish", callback_data="clear_cart"))

    bot.send_message(
        m.chat.id,
        f"✅ Mahsulot savatchaga qo‘shildi:\n<b>{pname}</b>\nMiqdor: {qty}\nNarx: {format_money(price)}",
        parse_mode="HTML",
        reply_markup=kb
    )
@bot.callback_query_handler(func=lambda c: c.data == "view_cart")
def cb_view_cart(c):
    uid = c.from_user.id
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT data FROM user_carts WHERE user_id=%s;", (uid,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row or not row['data'] or not row['data'].get('items'):
        bot.answer_callback_query(c.id, "Savatcha bo‘sh")
        bot.send_message(c.message.chat.id, "Savatcha bo‘sh. Yana mahsulot qidirish uchun 'Mahsulot sotish' ni tanlang.", reply_markup=main_keyboard())
        return
    data = row['data']
    items = data['items']
    text_lines = ["🧾 <b>Savatcha</b>\n"]
    total = 0
    for i, it in enumerate(items, 1):
        line_total = it['qty'] * it['price']
        total += line_total
        text_lines.append(f"{i}. {it['name']} — {it['qty']} x {format_money(it['price'])} = {format_money(line_total)}")
    text_lines.append(f"\nUmumiy: <b>{format_money(total)}</b>")
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Buyurtmani tasdiqlash", callback_data="checkout"))
    kb.add(types.InlineKeyboardButton("Mahsulotni tahrirlash", callback_data="edit_cart"))
    kb.add(types.InlineKeyboardButton("Bekor qilish va bo‘shatish", callback_data="clear_cart"))
    bot.edit_message_text("\n".join(text_lines), chat_id=c.message.chat.id, message_id=c.message.message_id, parse_mode="HTML", reply_markup=kb)
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c: c.data == "clear_cart")
def cb_clear_cart(c):
    uid = c.from_user.id
    clear_user_cart(uid)
    bot.edit_message_text("Savatcha tozalandi.", chat_id=c.message.chat.id, message_id=c.message.message_id)
    bot.answer_callback_query(c.id, "Savatcha bo‘shatildi.")
    bot.send_message(c.message.chat.id, "Asosiy menyu:", reply_markup=main_keyboard())

@bot.callback_query_handler(func=lambda c: c.data == "edit_cart")
def cb_edit_cart(c):
    uid = c.from_user.id
    # For simplicity offer to remove last or clear — advanced editing can be added
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Oxirgi mahsulotni o‘chirish", callback_data="remove_last"))
    kb.add(types.InlineKeyboardButton("Butun savatchani bo‘shatish", callback_data="clear_cart"))
    bot.answer_callback_query(c.id)
    bot.send_message(c.message.chat.id, "Tahrir variantlari:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "remove_last")
def cb_remove_last(c):
    uid = c.from_user.id
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT data FROM user_carts WHERE user_id=%s;", (uid,))
    row = cur.fetchone()
    if not row:
        bot.answer_callback_query(c.id, "Savatcha bo‘sh")
        return
    data = row['data']
    if not data.get('items'):
        bot.answer_callback_query(c.id, "Savatcha bo‘sh")
        return
    removed = data['items'].pop()
    if data['items']:
        cur.execute("UPDATE user_carts SET data=%s, updated_at=now() WHERE user_id=%s;", (json.dumps(data), uid))
    else:
        cur.execute("DELETE FROM user_carts WHERE user_id=%s;", (uid,))
    conn.commit()
    cur.close()
    conn.close()
    bot.answer_callback_query(c.id, f"Oxirgi mahsulot o‘chirildi: {removed['name']}")
    bot.send_message(c.message.chat.id, "Savatcha yangilandi.", reply_markup=main_keyboard())

# --- Checkout flow ---
@bot.callback_query_handler(func=lambda c: c.data == "checkout")
def cb_checkout(c):
    uid = c.from_user.id
    # ask customer tanlash yoki yangi qo'shish
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row("Mavjud mijozni tanlash", "Yangi mijoz qo'shish")
    kb.row("Bekor qilish")
    set_state(uid, "action", "checkout_choose_customer")
    bot.send_message(c.message.chat.id, "Mijozni tanlang:", reply_markup=kb)
    bot.answer_callback_query(c.id)

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "checkout_choose_customer")
def checkout_choose_customer(m):
    uid = m.from_user.id
    text = m.text.strip()
    if text == "Bekor qilish":
        clear_state(uid)
        bot.send_message(m.chat.id, "Amal bekor qilindi.", reply_markup=main_keyboard())
        return
    if text == "Yangi mijoz qo'shish":
        set_state(uid, "action", "checkout_new_customer_name")
        bot.send_message(m.chat.id, "Mijoz ismi (lotincha):", reply_markup=cancel_keyboard())
        return
    if text == "Mavjud mijozni tanlash":
        set_state(uid, "action", "checkout_search_customer")
        bot.send_message(m.chat.id, "Mijoz telefon yoki ismini kiriting:", reply_markup=cancel_keyboard())
        return
    bot.send_message(m.chat.id, "Iltimos menyudan tanlang.", reply_markup=cancel_keyboard())

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "checkout_new_customer_name")
def checkout_new_customer_name(m):
    uid = m.from_user.id
    text = m.text.strip()
    if text.lower() == "bekor qilish":
        clear_state(uid)
        bot.send_message(m.chat.id, "Amal bekor qilindi.", reply_markup=main_keyboard())
        return
    if check_latin(text):
        bot.send_message(m.chat.id, "Iltimos lotincha kiriting.", reply_markup=cancel_keyboard())
        return
    set_state(uid, "new_customer_name", text)
    set_state(uid, "action", "checkout_new_customer_phone")
    bot.send_message(m.chat.id, "Mijoz telefon raqamini kiriting (+998...):", reply_markup=cancel_keyboard())

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "checkout_new_customer_phone")
def checkout_new_customer_phone(m):
    uid = m.from_user.id
    phone = m.text.strip()
    if phone.lower() == "bekor qilish":
        clear_state(uid)
        bot.send_message(m.chat.id, "Amal bekor qilindi.", reply_markup=main_keyboard())
        return
    # simple phone normalization
    set_state(uid, "new_customer_phone", phone)
    # save customer
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO customers (name, phone) VALUES (%s, %s) RETURNING id;", (get_state(uid, "new_customer_name"), phone))
    cust_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    set_state(uid, "checkout_customer_id", cust_id)
    # continue to payment choice
    set_state(uid, "action", "checkout_payment")
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row("Naqd", "Qarz")
    kb.row("Bekor qilish")
    bot.send_message(m.chat.id, "To'lov turini tanlang:", reply_markup=kb)

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "checkout_search_customer")
def checkout_search_customer(m):
    uid = m.from_user.id
    txt = m.text.strip()
    if txt.lower() == "bekor qilish":
        clear_state(uid)
        bot.send_message(m.chat.id, "Amal bekor qilindi.", reply_markup=main_keyboard())
        return
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, name, phone FROM customers WHERE phone ILIKE %s OR name ILIKE %s LIMIT 20;", (f"%{txt}%", f"%{txt}%"))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    if not rows:
        bot.send_message(m.chat.id, "Mijoz topilmadi, yangi mijoz qo'shish uchun 'Yangi mijoz qo'shish' ni tanlang.", reply_markup=cancel_keyboard())
        return
    kb = types.InlineKeyboardMarkup()
    for r in rows:
        kb.add(types.InlineKeyboardButton(f"{r['name']} — {r['phone']}", callback_data=f"choose_cust|{r['id']}"))
    bot.send_message(m.chat.id, "Topilgan mijozlar:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("choose_cust|"))
def cb_choose_cust(c):
    uid = c.from_user.id
    _, cid = c.data.split("|")
    set_state(uid, "checkout_customer_id", int(cid))
    set_state(uid, "action", "checkout_payment")
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row("Naqd", "Qarz")
    kb.row("Bekor qilish")
    bot.send_message(c.message.chat.id, "To'lov turini tanlang:", reply_markup=kb)
    bot.answer_callback_query(c.id)

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "checkout_payment")
def checkout_payment(m):
    uid = m.from_user.id
    txt = m.text.strip().lower()
    if txt == "bekor qilish":
        clear_state(uid)
        bot.send_message(m.chat.id, "Amal bekor qilindi.", reply_markup=main_keyboard())
        return
    if txt not in ("naqd", "qarz"):
        bot.send_message(m.chat.id, "Iltimos 'Naqd' yoki 'Qarz' ni tanlang.", reply_markup=cancel_keyboard())
        return
    payment = txt
    set_state(uid, "checkout_payment_type", payment)
    # confirm order summary and ask format (matn yoki PDF)
    set_state(uid, "action", "checkout_confirm_format")
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row("Matn", "PDF")
    kb.row("Bekor qilish")
    bot.send_message(m.chat.id, "Chekni qaysi ko'rinishda olasiz? (Matn yoki PDF):", reply_markup=kb)

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "checkout_confirm_format")
def checkout_confirm_format(m):
    uid = m.from_user.id
    txt = m.text.strip().lower()
    if txt == "bekor qilish":
        clear_state(uid)
        bot.send_message(m.chat.id, "Amal bekor qilindi.", reply_markup=main_keyboard())
        return
    fmt = txt
    if fmt not in ("matn", "pdf"):
        bot.send_message(m.chat.id, "Iltimos 'Matn' yoki 'PDF' ni tanlang.", reply_markup=cancel_keyboard())
        return
    # perform sale: create sale record, decrease stock, create sale_items, maybe debt
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT data FROM user_carts WHERE user_id=%s;", (uid,))
    row = cur.fetchone()
    if not row or not row['data'] or not row['data'].get('items'):
        bot.send_message(m.chat.id, "Savatcha bo'sh - sotish imkoni yo'q.", reply_markup=main_keyboard())
        clear_state(uid)
        return
    data = row['data']
    items = data['items']
    total = sum([it['qty'] * it['price'] for it in items])
    cust_id = get_state(uid, "checkout_customer_id")
    payment = get_state(uid, "checkout_payment_type")
    # create sale
    cur.execute("INSERT INTO sales (customer_id, total_amount, payment_type, seller_phone) VALUES (%s, %s, %s, %s) RETURNING id, created_at;",
                (cust_id, total, payment, SELLER_PHONE))
    sale = cur.fetchone()
    sale_id = sale['id']
    created_at = sale['created_at']
    # insert sale_items and reduce stock
    for it in items:
        cur.execute("INSERT INTO sale_items (sale_id, product_id, name, qty, price, total) VALUES (%s,%s,%s,%s,%s,%s);",
                    (sale_id, it['product_id'], it['name'], it['qty'], it['price'], it['qty'] * it['price']))
        # reduce product qty
        cur.execute("UPDATE products SET qty = qty - %s WHERE id=%s;", (it['qty'], it['product_id']))
    # if qarz, add to debts table
    if payment == "qarz":
        cur.execute("INSERT INTO debts (customer_id, sale_id, amount) VALUES (%s, %s, %s);", (cust_id, sale_id, total))
    # clear cart
    cur.execute("DELETE FROM user_carts WHERE user_id=%s;", (uid,))
    conn.commit()
    cur.close()
    conn.close()
    clear_state(uid)
    # produce receipt
    if fmt == "matn":
        text = receipt_text(sale_id)
        bot.send_message(m.chat.id, text, parse_mode="HTML", reply_markup=main_keyboard())
    else:
        pdf_bytes = receipt_pdf_bytes(sale_id)
        bot.send_document(m.chat.id, pdf_bytes, caption="Sizning chek (PDF)", reply_markup=main_keyboard())

def receipt_text(sale_id):
    # gather sale info
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT s.id, s.total_amount, s.payment_type, s.created_at, c.name as cust_name, c.phone as cust_phone FROM sales s LEFT JOIN customers c ON s.customer_id=c.id WHERE s.id=%s;", (sale_id,))
    s = cur.fetchone()
    cur.execute("SELECT name, qty, price, total FROM sale_items WHERE sale_id=%s;", (sale_id,))
    items = cur.fetchall()
    cur.close()
    conn.close()
    lines = []
    lines.append("🏷️ <b>Chek</b>")
    lines.append(f"Vaqt: {s['created_at'].strftime('%d.%m.%Y %H:%M:%S')}")
    lines.append(f"Do'kon: {STORE_LOCATION_NAME}")
    lines.append(f"Sotuvchi: {SELLER_PHONE}")
    lines.append(f"Mijoz: {s['cust_name'] or '-'} {s['cust_phone'] or ''}")
    lines.append("--------------")
    for it in items:
        lines.append(f"{it['name']} — {it['qty']} x {format_money(it['price'])} = {format_money(it['total'])}")
    lines.append("--------------")
    lines.append(f"Jami: <b>{format_money(s['total_amount'])}</b>")
    lines.append(f"To'lov turi: {s['payment_type']}")
    # location qr (we will attach placeholder text)
    lines.append(f"Do'kon lokatsiyasi: (kodi yoki link) — kiritilgan kod: {STORE_LOCATION_NAME}")
    # nice emojis
    text = "\n".join(lines)
    return text

def receipt_pdf_bytes(sale_id):
    # create PDF in-memory with reportlab
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT s.id, s.total_amount, s.payment_type, s.created_at, c.name as cust_name, c.phone as cust_phone FROM sales s LEFT JOIN customers c ON s.customer_id=c.id WHERE s.id=%s;", (sale_id,))
    s = cur.fetchone()
    cur.execute("SELECT name, qty, price, total FROM sale_items WHERE sale_id=%s;", (sale_id,))
    items = cur.fetchall()
    cur.close()
    conn.close()
    buf = io.BytesIO()
    p = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    x_margin = 20*mm
    y = height - 20*mm
    p.setFont("Helvetica-Bold", 14)
    p.drawString(x_margin, y, "🧾 Chek")
    p.setFont("Helvetica", 10)
    y -= 10*mm
    p.drawString(x_margin, y, f"Vaqt: {s['created_at'].strftime('%d.%m.%Y %H:%M:%S')}")
    y -= 6*mm
    p.drawString(x_margin, y, f"Do'kon: {STORE_LOCATION_NAME}")
    y -= 6*mm
    p.drawString(x_margin, y, f"Sotuvchi: {SELLER_PHONE}")
    y -= 6*mm
    p.drawString(x_margin, y, f"Mijoz: {s['cust_name'] or '-'}  {s['cust_phone'] or ''}")
    y -= 8*mm
    p.line(x_margin, y, width - x_margin, y)
    y -= 6*mm
    for it in items:
        text = f"{it['name']} — {it['qty']} x {format_money(it['price'])} = {format_money(it['total'])}"
        p.drawString(x_margin, y, text)
        y -= 6*mm
        if y < 40*mm:
            p.showPage()
            y = height - 20*mm
    y -= 2*mm
    p.line(x_margin, y, width - x_margin, y)
    y -= 8*mm
    p.setFont("Helvetica-Bold", 12)
    p.drawString(x_margin, y, f"Jami: {format_money(s['total_amount'])}")
    y -= 12*mm
    p.setFont("Helvetica", 10)
    p.drawString(x_margin, y, f"To'lov turi: {s['payment_type']}")
    y -= 10*mm
    p.drawString(x_margin, y, f"Do'kon lokatsiyasi kodi: {STORE_LOCATION_NAME}")
    # add QR code for store location
    qr = qrcode.make(f"Store:{STORE_LOCATION_NAME}")
    qr_io = io.BytesIO()
    qr.save(qr_io, format="PNG")
    qr_io.seek(0)
    p.drawInlineImage(Image.open(qr_io), width - 60*mm, y-10*mm, 40*mm, 40*mm)
    p.showPage()
    p.save()
    buf.seek(0)
    return buf

# --- Statistics and debts ---
@bot.message_handler(func=lambda m: m.text == "📊 Statistika")
def cmd_statistics(m):
    uid = m.from_user.id
    # show options
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Sotuvlar tarixi (ID bo'yicha qidirish)", callback_data="stat_search_id"))
    kb.add(types.InlineKeyboardButton("Kunlik", callback_data="stat_daily"))
    kb.add(types.InlineKeyboardButton("Oylik", callback_data="stat_monthly"))
    kb.add(types.InlineKeyboardButton("Yillik", callback_data="stat_yearly"))
    kb.add(types.InlineKeyboardButton("Ombor holati (excel/pdf)", callback_data="stock_export"))
    bot.send_message(m.chat.id, "Statistika variantlari:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("stat_"))
def cb_stat(c):
    cmd = c.data
    if cmd == "stat_search_id":
        set_state(c.from_user.id, "action", "stat_search_by_id")
        bot.send_message(c.message.chat.id, "Sotuv ID ni kiriting:", reply_markup=cancel_keyboard())
    elif cmd in ("stat_daily","stat_monthly","stat_yearly"):
        period = cmd.split("_")[1]
        # generate PDF report for the period (for simplicity take last day/month/year)
        pdf = stats_pdf_bytes(period)
        bot.send_document(c.message.chat.id, pdf, caption=f"{period} hisobot (PDF)")
    elif cmd == "stock_export":
        # ask format
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        kb.row("Excel", "PDF")
        kb.row("Bekor qilish")
        set_state(c.from_user.id, "action", "stock_export_choose_format")
        bot.send_message(c.message.chat.id, "Qaysi formatda olmoqchisiz?", reply_markup=kb)
    bot.answer_callback_query(c.id)

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "stat_search_by_id")
def stat_search_by_id(m):
    uid = m.from_user.id
    txt = m.text.strip()
    if txt.lower() == "bekor qilish":
        clear_state(uid)
        bot.send_message(m.chat.id, "Amal bekor qilindi.", reply_markup=main_keyboard())
        return
    if not txt.isdigit():
        bot.send_message(m.chat.id, "Iltimos ID ni son bilan kiriting.", reply_markup=cancel_keyboard())
        return
    sid = int(txt)
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT s.id, s.total_amount, s.payment_type, s.created_at, c.name, c.phone FROM sales s LEFT JOIN customers c ON s.customer_id=c.id WHERE s.id=%s;", (sid,))
    s = cur.fetchone()
    if not s:
        bot.send_message(m.chat.id, "Bunday sotuv topilmadi.", reply_markup=main_keyboard())
        cur.close()
        conn.close()
        clear_state(uid)
        return
    cur.execute("SELECT name, qty, price, total FROM sale_items WHERE sale_id=%s;", (sid,))
    items = cur.fetchall()
    cur.close()
    conn.close()
    lines = [f"Sotuv ID: {s['id']}", f"Vaqt: {s['created_at'].strftime('%d.%m.%Y %H:%M:%S')}", f"Jami: {format_money(s['total_amount'])}", f"To'lov: {s['payment_type']}", "Tovarlar:"]
    for it in items:
        lines.append(f"- {it['name']} {it['qty']} x {format_money(it['price'])} = {format_money(it['total'])}")
    bot.send_message(m.chat.id, "\n".join(lines), reply_markup=main_keyboard())
    clear_state(uid)

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "stock_export_choose_format")
def stock_export_choose_format(m):
    uid = m.from_user.id
    txt = m.text.strip().lower()
    if txt == "bekor qilish":
        clear_state(uid)
        bot.send_message(m.chat.id, "Amal bekor qilindi.", reply_markup=main_keyboard())
        return
    if txt not in ("excel", "pdf"):
        bot.send_message(m.chat.id, "Iltimos Excel yoki PDF ni tanlang.", reply_markup=cancel_keyboard())
        return
    if txt == "excel":
        excel_bytes = export_stock_excel()
        bot.send_document(m.chat.id, excel_bytes, caption="Ombor holati (Excel)", reply_markup=main_keyboard())
    else:
        pdf_bytes = export_stock_pdf()
        bot.send_document(m.chat.id, pdf_bytes, caption="Ombor holati (PDF)", reply_markup=main_keyboard())
    clear_state(uid)

def export_stock_excel():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, name, qty, cost_price, suggest_price, created_at FROM products ORDER BY id;")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Ombor")
    buf.seek(0)
    return buf

def export_stock_pdf():
    # create simple pdf list
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, name, qty, cost_price, suggest_price FROM products ORDER BY id;")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    buf = io.BytesIO()
    p = canvas.Canvas(buf, pagesize=A4)
    x = 20*mm
    y = A4[1] - 20*mm
    p.setFont("Helvetica-Bold", 12)
    p.drawString(x, y, "Ombor holati")
    y -= 10*mm
    p.setFont("Helvetica", 9)
    for r in rows:
        line = f"{r['id']}. {r['name']} — {r['qty']} dona — opt narx: {format_money(r['cost_price'])} — taklif: {format_money(r['suggest_price'])}"
        p.drawString(x, y, line)
        y -= 6*mm
        if y < 30*mm:
            p.showPage()
            y = A4[1] - 20*mm
    p.showPage()
    p.save()
    buf.seek(0)
    return buf

def stats_pdf_bytes(period):
    # simple sample stats - implement real aggregation as needed
    buf = io.BytesIO()
    p = canvas.Canvas(buf, pagesize=A4)
    p.setFont("Helvetica-Bold", 14)
    p.drawString(30*mm, A4[1]-30*mm, f"Hisobot: {period}")
    p.setFont("Helvetica", 10)
    p.drawString(30*mm, A4[1]-40*mm, f"Sana: {now_str()}")
    p.drawString(30*mm, A4[1]-50*mm, "Eslatma: to'liq statistikani yaratish uchun serverda ko'proq ma'lumot yig'ilishi kerak.")
    p.showPage()
    p.save()
    buf.seek(0)
    return buf

# --- Debts list ---
@bot.message_handler(func=lambda m: m.text == "📋 Qarzdorlar ro'yxati")
def cmd_debts(m):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT d.id, d.amount, d.created_at, c.name, c.phone FROM debts d JOIN customers c ON d.customer_id=c.id ORDER BY d.created_at DESC;")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    if not rows:
        bot.send_message(m.chat.id, "Hozircha qarzdorlar yo'q.", reply_markup=main_keyboard())
        return
    # offer excel export or show list
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Ro'yxatni Excel ko'rinishida yuborish", callback_data="debts_excel"))
    text_lines = ["📋 Qarzdorlar ro'yxati:"]
    for r in rows:
        text_lines.append(f"- {r['name']} {r['phone']} — {format_money(r['amount'])} ({r['created_at'].strftime('%d.%m.%Y')})")
    bot.send_message(m.chat.id, "\n".join(text_lines), reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "debts_excel")
def cb_debts_excel(c):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT d.id, c.name, c.phone, d.amount, d.created_at FROM debts d JOIN customers c ON d.customer_id=c.id;")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Debts")
    buf.seek(0)
    bot.send_document(c.message.chat.id, buf, caption="Qarzdorlar (Excel)")
    bot.answer_callback_query(c.id)

# --- fallback text handler to catch non-matching input and to warn about Cyrillic ---
@bot.message_handler(func=lambda m: True)
def fallback(m):
    # if message contains Cyrillic, warn
    if check_latin(m.text):
        bot.send_message(m.chat.id, "Iltimos, faqat lotin alifbosida yozing. Bot faqat lotin yozuvini qabul qiladi.", reply_markup=main_keyboard())
    else:
        bot.send_message(m.chat.id, "Menyu orqali tanlang yoki /start ni bosing.", reply_markup=main_keyboard())

# --- Run init ---
if __name__ == "__main__":
    init_db()
    print("Bot ishga tushmoqda...")
    bot.infinity_polling()
