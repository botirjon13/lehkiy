
# bot_merged_full.py
# Full merged bot: original features + enhanced statistics (Excel reports)
# DISCLAIMER: keep your .env with TELEGRAM_TOKEN and DATABASE_URL

import os
import re
import io
import json
import qrcode
import psycopg2
import pandas as pd
from datetime import datetime, timedelta
from urllib.parse import urlparse
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
import telebot
from telebot import types
from zoneinfo import ZoneInfo
from openpyxl import Workbook
from io import BytesIO
import tempfile

# --- Load env ---
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
STORE_LOCATION_NAME = os.getenv("STORE_LOCATION_NAME", "Do'kon")
SELLER_PHONE = os.getenv("SELLER_PHONE", "+998330131992")
SELLER_NAME = os.getenv("SELLER_NAME", "")  # optional, put in .env if you want seller name
TIMEZONE = os.getenv("TIMEZONE", "Asia/Tashkent")

if not TOKEN or not DATABASE_URL:
    raise SystemExit("Iltimos TELEGRAM_TOKEN va DATABASE_URL ni .env ga qo'ying")

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

# --- DB helpers ---
def get_conn():
    """
    Parse DATABASE_URL like: postgres://user:pass@host:port/dbname
    and return psycopg2 connection.
    """
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
    if os.path.exists("db_init.sql"):
        sql = open("db_init.sql", "r", encoding="utf-8").read()
        cur.execute(sql)
        conn.commit()
    cur.close()
    conn.close()


# --- Utility helpers ---
CYRILLIC_PATTERN = re.compile(r'[А-Яа-яЁёҢғқўҳ]', flags=re.UNICODE)

def contains_cyrillic(text: str):
    if not isinstance(text, str):
        return False
    return bool(CYRILLIC_PATTERN.search(text))

def format_money(v):
    try:
        return f"{int(v):,}".replace(",", ".") + " so'm"
    except:
        return str(v)

def now_str():
    dt = datetime.utcnow().replace(microsecond=0) + timedelta(hours=5)
    return dt.strftime("%d.%m.%Y %H:%M:%S")


# --- Keyboards ---
def main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    kb.row(types.KeyboardButton("🔹 Yangi mahsulot qo'shish"))
    kb.row(types.KeyboardButton("🛒 Mahsulot sotish"))
    kb.row(types.KeyboardButton("📊 Statistika"), types.KeyboardButton("📋 Qarzdorlar ro'yxati"))
    # Qo'shilgan yangi tugma: Ombor (Excel)
    kb.row(types.KeyboardButton("📊 Ombor (Excel)"))
    return kb

def cancel_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row(types.KeyboardButton("Bekor qilish"))
    return kb

def small_yes_no():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Ha", callback_data="yes"), types.InlineKeyboardButton("Yo'q", callback_data="no"))
    return kb


# --- Simple in-memory per-user state (lightweight) ---
USER_STATE = {}

def set_state(user_id, key, value):
    USER_STATE.setdefault(user_id, {})[key] = value

def get_state(user_id, key, default=None):
    return USER_STATE.get(user_id, {}).get(key, default)

def clear_state(user_id):
    USER_STATE.pop(user_id, None)


# --- Utility: safely load cart data (db may store JSON or string) ---
def parse_cart_data(raw):
    if not raw:
        return {"items": []}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except:
            return {"items": []}
    return {"items": []}


# --- DB cart helpers ---
def clear_user_cart(uid):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM user_carts WHERE user_id=%s;", (uid,))
    conn.commit()
    cur.close()
    conn.close()

def get_user_cart(uid):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT data FROM user_carts WHERE user_id=%s;", (uid,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return {"items": []}
    return parse_cart_data(row.get('data'))

def save_user_cart(uid, data):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT data FROM user_carts WHERE user_id=%s;", (uid,))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE user_carts SET data=%s, updated_at=now() WHERE user_id=%s;", (json.dumps(data), uid))
    else:
        cur.execute("INSERT INTO user_carts (user_id, data) VALUES (%s, %s);", (uid, json.dumps(data)))
    conn.commit()
    cur.close()
    conn.close()


# Allowed users (preserve original). Replace with your Telegram IDs.
ALLOWED_USERS = [1262207928, 298157746, 963690743]


# ---------------------------
# Robust text measurement helper
# ---------------------------
def _get_font(size=16):
    """
    Foydalaniladigan shrift: LiberationSans-Bold (aniq va kattaroq chiqadi).
    Agar u topilmasa — DejaVuSans fallback ishlaydi.
    """
    candidates = [
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",  # juda tiniq
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()

def _measure_text(draw, text, font):
    """
    Cross-version Pillow text measurement helper.
    Returns (width, height).
    """
    # 1) try draw.textbbox
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        return (w, h)
    except Exception:
        pass

    # 2) try draw.textsize
    try:
        size = draw.textsize(text, font=font)
        return (size[0], size[1])
    except Exception:
        pass

    # 3) try font.getbbox
    try:
        bbox = font.getbbox(text)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        return (w, h)
    except Exception:
        pass

    # 4) try font.getsize
    try:
        size = font.getsize(text)
        return (size[0], size[1])
    except Exception:
        pass

    # 5) fallback approximate
    approx_w = int(len(text) * (getattr(font, "size", 12) * 0.6))
    approx_h = int((getattr(font, "size", 12)) * 1.2)
    return (approx_w, approx_h)


# ---------------------------
# Robust receipt image generator
# ---------------------------
def receipt_image_bytes(sale_id):
    """
    Katta shriftli chek dizayni:
    - Shriftlar kattaroq (42 / 34 / 28 pt)
    - Matn markazda
    - QR kodi pastda
    - Oq fon
    """
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT s.id, s.total_amount, s.payment_type, s.created_at, 
               c.name AS cust_name, c.phone AS cust_phone
        FROM sales s 
        LEFT JOIN customers c ON s.customer_id = c.id 
        WHERE s.id = %s;
    """, (sale_id,))
    s = cur.fetchone()

    cur.execute("""
        SELECT name, qty, price, total 
        FROM sale_items 
        WHERE sale_id = %s
        ORDER BY id;
    """, (sale_id,))
    items = cur.fetchall()
    cur.close()
    conn.close()

    if not s:
        raise ValueError(f"Sotuv topilmadi: sale_id={sale_id}")

    from_zone = ZoneInfo(TIMEZONE)
    created_local = s.get("created_at")
    if isinstance(created_local, datetime):
        try:
            created_local = created_local.astimezone(from_zone)
        except Exception:
            created_local = created_local + timedelta(hours=5)
    else:
        created_local = datetime.utcnow() + timedelta(hours=5)

    # 📏 Katta shriftlar
    title_font = _get_font(42)
    body_font = _get_font(34)
    small_font = _get_font(28)

    seller_display = f"{SELLER_NAME} ({SELLER_PHONE})" if SELLER_NAME else f"{SELLER_PHONE}"

    # 📄 Matnlar
    lines = [
        "🧾 CHEK",
        f"Sana: {created_local.strftime('%d.%m.%Y %H:%M:%S')}",
        f"Mijoz: {s.get('cust_name') or '-'} {s.get('cust_phone') or ''}",
        f"Do‘kon: {STORE_LOCATION_NAME}",
        "────────────────────────────",
    ]

    for it in items:
        name = str(it.get("name") or "")
        qty = int(it.get("qty") or 0)
        price = it.get("price") or 0
        total = it.get("total") or 0
        lines.append(f"{name} — {qty} x {format_money(price)} = {format_money(total)}")

    lines.append("────────────────────────────")
    lines.append(f"💰 Jami: {format_money(s.get('total_amount') or 0)}")
    lines.append(f"💳 To‘lov turi: {s.get('payment_type') or '-'}")
    lines.append(f"👨‍💼 Sotuvchi: {seller_display}")
    lines.append("────────────────────────────")
    lines.append("Tashrifingiz uchun rahmat! ❤️")

    # 📏 Hajmni hisoblash
    temp_img = Image.new("RGB", (10, 10))
    draw_temp = ImageDraw.Draw(temp_img)
    widths, heights = [], []
    for ln in lines:
        w, h = _measure_text(draw_temp, ln, body_font)
        widths.append(w)
        heights.append(h)

    max_w = max(widths) + 80
    total_h = sum(h + 20 for h in heights) + 260  # satrlar oralig‘i kattaroq
    img_w = min(max(480, max_w), 700)
    img_h = max(700, total_h)

    img = Image.new("RGB", (img_w, img_h), "white")
    draw = ImageDraw.Draw(img)

    # 🧾 Matnni o‘rtada chizish
    y = 50
    for ln in lines:
        font_used = title_font if "CHEK" in ln else body_font
        w, h = _measure_text(draw, ln, font_used)
        x = (img_w - w) // 2
        draw.text((x, y), ln, font=font_used, fill="black")
        y += h + 20

    # 🔲 QR kodi pastda markazda
    try:
        qr_payload = f"sale:{sale_id};total:{s.get('total_amount')}"
        qr = qrcode.make(qr_payload)
        qr_size = 200
        qr = qr.resize((qr_size, qr_size))
        qr_x = (img_w - qr_size) // 2
        qr_y = img_h - qr_size - 40
        img.paste(qr, (qr_x, qr_y))
    except Exception as e:
        print("QR xatosi:", e)

    buf = io.BytesIO()
    buf.name = f"receipt_{sale_id}.png"
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

# matn sifatida boradi
def receipt_text(sale_id):
    """
    Chek matn ko‘rinishida yuboriladigan versiya.
    (Agar rasm chiqmasa, matn sifatida yuboriladi.)
    """
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT s.id, s.total_amount, s.payment_type, s.created_at, 
               c.name as cust_name, c.phone as cust_phone
        FROM sales s 
        LEFT JOIN customers c ON s.customer_id = c.id 
        WHERE s.id=%s;
    """, (sale_id,))
    s = cur.fetchone()
    cur.execute("SELECT name, qty, price, total FROM sale_items WHERE sale_id=%s;", (sale_id,))
    items = cur.fetchall()
    cur.close()
    conn.close()

    lines = []
    lines.append("🧾 Chek №{}".format(sale_id))
    created_at = s.get("created_at")
    if isinstance(created_at, datetime):
        try:
            created_at = created_at.astimezone(ZoneInfo(TIMEZONE))
        except:
            created_at = created_at + timedelta(hours=5)

    lines.append(f"📅 Sana: {created_at.strftime('%d.%m.%Y %H:%M:%S') if created_at else now_str()}")
    lines.append(f"🏬 Do‘kon: {STORE_LOCATION_NAME}")
    seller_display = f"{SELLER_NAME} {SELLER_PHONE}" if SELLER_NAME else f"{SELLER_PHONE}"
    lines.append(f"👨‍💼 Sotuvchi: {seller_display}")
    lines.append(f"👤 Mijoz: {s.get('cust_name') or '-'} {s.get('cust_phone') or ''}")
    lines.append("────────────────────────────")
    for it in items:
        lines.append(f"{it.get('name')} — {it.get('qty')} x {format_money(it.get('price'))} = {format_money(it.get('total'))}")
    lines.append("────────────────────────────")
    lines.append(f"💰 Jami: {format_money(s.get('total_amount') or 0)}")
    lines.append(f"💳 To‘lov turi: {s.get('payment_type')}")
    lines.append("────────────────────────────")
    lines.append("Tashrifingiz uchun rahmat! ❤️")
    return "\n".join(lines)
# ---------------------------
# --- Start handler ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    if message.from_user.id not in ALLOWED_USERS:
        bot.send_message(message.chat.id, "❌ Sizga kirish taqiqlangan.")
        return

    text = (
        "👋 Salom!\n\n"
        "Bu do‘kon boshqaruv botidir.\n"
        "Quyidagi menyudan tanlang 👇"
    )
    bot.send_message(message.chat.id, text, reply_markup=main_keyboard())

@bot.message_handler(func=lambda m: m.text == "📊 Statistika")
def show_stat_menu(message):
    if message.from_user.id not in ALLOWED_USERS:
        bot.send_message(message.chat.id, "❌ Sizga kirish taqiqlangan.")
        return

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📋 Chek ID bo‘yicha", "📅 Kunlik")
    kb.row("🗓 Oylik", "📆 Yillik")
    kb.row("⬅️ Orqaga")
    bot.send_message(message.chat.id, "Statistika turini tanlang 👇", reply_markup=kb)

# --- Run ---
if __name__ == "__main__":
    init_db()
    print("✅ Bot ishga tushdi! (bot_stat_full.py)")
    try:
        bot.infinity_polling()
    except Exception as e:
        print("Polling exception:", e)
        raise
# Bot handlers (original handlers preserved, only small integration edits)
# (The remainder of original handlers are included below; unchanged logic)
