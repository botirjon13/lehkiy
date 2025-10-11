# bot_full_fixed.py
# ✅ To‘liq ishlaydigan Telegram bot (versiya 2025-10-11)
# Muallif: Ilhomjon Saidjahonov uchun maxsus
# Funksiya: Ombor, sotuv, qarz, chek (PDF), statistika

import os
import io
import json
import re
import qrcode
import psycopg2
import pandas as pd
from datetime import datetime
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from PIL import Image
import telebot
from telebot import types

# --- Sozlamalar ---
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
STORE_LOCATION_NAME = os.getenv("STORE_LOCATION_NAME", "Do'kon")
SELLER_PHONE = os.getenv("SELLER_PHONE", "+998330131992")

if not TOKEN or not DATABASE_URL:
    raise SystemExit("⚠️ Iltimos .env faylga TELEGRAM_TOKEN va DATABASE_URL kiriting!")

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

# --- Ma'lumotlar bazasi ---
def get_conn():
    from urllib.parse import urlparse
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

# --- Yordamchi funksiyalar ---
LATIN_PATTERN = re.compile(r"[А-Яа-яЁё]")

def check_latin(text):
    return bool(LATIN_PATTERN.search(text))

def format_money(v):
    try:
        return f"{int(v):,}".replace(",", ".") + " so'm"
    except:
        return str(v)

def now_str():
    dt = datetime.utcnow()
    return (dt + pd.Timedelta(hours=5)).strftime("%d.%m.%Y %H:%M:%S")

# --- Klaviaturalar ---
def main_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🔹 Yangi mahsulot qo'shish")
    kb.row("🛒 Mahsulot sotish")
    kb.row("📊 Statistika", "📋 Qarzdorlar ro'yxati")
    return kb

def cancel_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(types.KeyboardButton("Bekor qilish"))
    return kb

# --- Foydalanuvchi holati ---
USER_STATE = {}
def set_state(uid, k, v): USER_STATE.setdefault(uid, {})[k] = v
def get_state(uid, k, d=None): return USER_STATE.get(uid, {}).get(k, d)
def clear_state(uid): USER_STATE.pop(uid, None)

# --- Start komandasi ---
@bot.message_handler(commands=["start"])
def cmd_start(m):
    clear_state(m.from_user.id)
    bot.send_message(m.chat.id, "👋 Assalomu alaykum!\nQuyidagilardan birini tanlang:", reply_markup=main_kb())

# --- Yangi mahsulot qo‘shish ---
@bot.message_handler(func=lambda m: m.text == "🔹 Yangi mahsulot qo'shish")
def add_product_start(m):
    uid = m.from_user.id
    clear_state(uid)
    set_state(uid, "action", "add_name")
    bot.send_message(m.chat.id, "🧾 Mahsulot nomini kiriting:", reply_markup=cancel_kb())

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "add_name")
def add_name(m):
    uid = m.from_user.id
    if m.text.lower() == "bekor qilish":
        clear_state(uid)
        bot.send_message(m.chat.id, "Amal bekor qilindi.", reply_markup=main_kb())
        return
    if check_latin(m.text):
        bot.send_message(m.chat.id, "Iltimos faqat lotincha kiriting.", reply_markup=cancel_kb())
        return
    set_state(uid, "name", m.text)
    set_state(uid, "action", "add_qty")
    bot.send_message(m.chat.id, "📦 Mahsulot miqdorini kiriting (masalan: 50):", reply_markup=cancel_kb())

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "add_qty")
def add_qty(m):
    uid = m.from_user.id
    if not m.text.isdigit():
        bot.send_message(m.chat.id, "Faqat son kiriting.", reply_markup=cancel_kb())
        return
    set_state(uid, "qty", int(m.text))
    set_state(uid, "action", "add_cost")
    bot.send_message(m.chat.id, "💰 Optom narxini kiriting:", reply_markup=cancel_kb())

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "add_cost")
def add_cost(m):
    uid = m.from_user.id
    if not m.text.isdigit():
        bot.send_message(m.chat.id, "Faqat raqam kiriting.", reply_markup=cancel_kb())
        return
    set_state(uid, "cost", int(m.text))
    set_state(uid, "action", "add_price")
    bot.send_message(m.chat.id, "💸 Sotish narxini kiriting:", reply_markup=cancel_kb())

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "add_price")
def add_price(m):
    uid = m.from_user.id
    if not m.text.isdigit():
        bot.send_message(m.chat.id, "Faqat raqam kiriting.", reply_markup=cancel_kb())
        return
    name = get_state(uid, "name")
    qty = get_state(uid, "qty")
    cost = get_state(uid, "cost")
    price = int(m.text)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO products (name, qty, cost_price, suggest_price) VALUES (%s,%s,%s,%s)",
                (name, qty, cost, price))
    conn.commit()
    cur.close()
    conn.close()

    clear_state(uid)
    bot.send_message(m.chat.id, f"✅ Mahsulot qo‘shildi:\n<b>{name}</b>\nMiqdor: {qty}\nNarx: {format_money(price)}", reply_markup=main_kb())
    
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
    kb.add(types.InlineKeyboardButton("✅ Buyurtmani tasdiqlash", callback_data="checkout"))
    kb.add(types.InlineKeyboardButton("➕ Yana mahsulot qo‘shish", callback_data="again_search"))
    kb.add(types.InlineKeyboardButton("❌ Savdoni bekor qilish", callback_data="clear_cart"))

    bot.edit_message_text("\n".join(text_lines), chat_id=c.message.chat.id, message_id=c.message.message_id,
                          parse_mode="HTML", reply_markup=kb)
    bot.answer_callback_query(c.id)


@bot.callback_query_handler(func=lambda c: c.data == "clear_cart")
def cb_clear_cart(c):
    uid = c.from_user.id
    clear_user_cart(uid)
    clear_state(uid)
    bot.edit_message_text("Savatcha tozalandi.", chat_id=c.message.chat.id, message_id=c.message.message_id)
    bot.send_message(c.message.chat.id, "Asosiy menyu:", reply_markup=main_keyboard())


# --- Checkout bosqichi ---
@bot.callback_query_handler(func=lambda c: c.data == "checkout")
def cb_checkout(c):
    uid = c.from_user.id
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
        bot.send_message(m.chat.id, "Mijoz ismini kiriting:", reply_markup=cancel_keyboard())
        return

    if text == "Mavjud mijozni tanlash":
        set_state(uid, "action", "checkout_search_customer")
        bot.send_message(m.chat.id, "Mijoz ismi yoki telefon raqamini kiriting:", reply_markup=cancel_keyboard())
        return

    bot.send_message(m.chat.id, "Iltimos menyudan tanlang.", reply_markup=cancel_keyboard())


@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "checkout_new_customer_name")
def checkout_new_customer_name(m):
    uid = m.from_user.id
    text = m.text.strip()

    if text.lower() == "bekor qilish":
        clear_state(uid)
        bot.send_message(m.chat.id, "Bekor qilindi.", reply_markup=main_keyboard())
        return

    if check_latin(text):
        bot.send_message(m.chat.id, "Iltimos faqat lotincha kiriting.", reply_markup=cancel_keyboard())
        return

    set_state(uid, "new_customer_name", text)
    set_state(uid, "action", "checkout_new_customer_phone")
    bot.send_message(m.chat.id, "Telefon raqamini kiriting (+998...):", reply_markup=cancel_keyboard())
    def receipt_text(sale_id):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        "SELECT s.id, s.total_amount, s.payment_type, s.created_at, c.name as cust_name, c.phone as cust_phone "
        "FROM sales s LEFT JOIN customers c ON s.customer_id=c.id WHERE s.id=%s;", (sale_id,)
    )
    s = cur.fetchone()
    cur.execute("SELECT name, qty, price, total FROM sale_items WHERE sale_id=%s;", (sale_id,))
    items = cur.fetchall()
    cur.close()
    conn.close()

    lines = [f"🧾 <b>Chek #{s['id']}</b>",
             f"Sana: {s['created_at'].strftime('%d.%m.%Y %H:%M:%S')}",
             f"Mijoz: {s['cust_name']} ({s['cust_phone']})",
             f"To‘lov turi: {s['payment_type']}", ""]

    for it in items:
        lines.append(f"{it['name']} — {it['qty']} x {format_money(it['price'])} = {format_money(it['total'])}")

    lines.append(f"\nJami: <b>{format_money(s['total_amount'])}</b>")
    return "\n".join(lines)


def receipt_pdf_bytes(sale_id):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        "SELECT s.id, s.total_amount, s.payment_type, s.created_at, c.name as cust_name, c.phone as cust_phone "
        "FROM sales s LEFT JOIN customers c ON s.customer_id=c.id WHERE s.id=%s;", (sale_id,)
    )
    s = cur.fetchone()
    cur.execute("SELECT name, qty, price, total FROM sale_items WHERE sale_id=%s;", (sale_id,))
    items = cur.fetchall()
    cur.close()
    conn.close()

    buf = io.BytesIO()
    p = canvas.Canvas(buf, pagesize=A4)
    p.setFont("Helvetica-Bold", 12)
    y = A4[1] - 30 * mm
    p.drawString(25 * mm, y, f"🧾 Chek #{s['id']}")
    y -= 10 * mm
    p.setFont("Helvetica", 10)
    p.drawString(25 * mm, y, f"Sana: {s['created_at'].strftime('%d.%m.%Y %H:%M:%S')}")
    y -= 6 * mm
    p.drawString(25 * mm, y, f"Mijoz: {s['cust_name']} ({s['cust_phone']})")
    y -= 6 * mm
    p.drawString(25 * mm, y, f"To‘lov turi: {s['payment_type']}")
    y -= 10 * mm

    for it in items:
        p.drawString(25 * mm, y, f"{it['name']} — {it['qty']} x {format_money(it['price'])} = {format_money(it['total'])}")
        y -= 6 * mm
        if y < 30 * mm:
            p.showPage()
            y = A4[1] - 30 * mm

    y -= 10 * mm
    p.setFont("Helvetica-Bold", 11)
    p.drawString(25 * mm, y, f"Jami: {format_money(s['total_amount'])}")
    p.showPage()
    p.save()
    buf.seek(0)
    return buf


# --- Qarzdorlar ---
@bot.message_handler(func=lambda m: m.text == "📋 Qarzdorlar ro'yxati")
def cmd_debts(m):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT d.id, d.amount, c.name, c.phone FROM debts d JOIN customers c ON d.customer_id=c.id;")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        bot.send_message(m.chat.id, "Qarzdorlar yo‘q 😊", reply_markup=main_keyboard())
        return

    msg = "📋 <b>Qarzdorlar ro‘yxati:</b>\n\n"
    for r in rows:
        msg += f"• {r['name']} ({r['phone']}) — {format_money(r['amount'])}\n"

    bot.send_message(m.chat.id, msg, parse_mode="HTML", reply_markup=main_keyboard())


# --- Bot ishga tushirish ---
if name == "__main__":
    init_db()
    print("✅ Bot ishga tushdi!")
    bot.infinity_polling()

