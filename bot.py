# bot_jpeg_full.py
# To'liq, barqaror va ishlaydigan versiya.
# Asl loyihangizni buzmasdan quyidagi o'zgartirishlar kiritildi:
# - Universal text measurement helper (_measure_text)
# - Barqaror receipt_image_bytes (PNG, dynamic font sizes, Unicode-safe, buf.name)
# - checkout_confirm_format handler tuned to accept 'matn' and ('rasm','image','photo')
# - SELLER_NAME support from .env
# - Defensive try/except blocks to prevent bot from freezing

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
import pandas as pd
import tempfile
import psycopg2
import os
from datetime import datetime
import traceback
import requests
from datetime import datetime, timedelta

# --- USD kursini olish (Markaziy bank API) ---
USD_RATE_CACHE = {"rate": None, "time": None}

def get_usd_rate():
    """
    USD kursini Markaziy bank API'dan oladi (sotib olish kursi).
    24 soatda bir marta yangilanadi.
    """
    global USD_RATE_CACHE
    now = datetime.utcnow()
    # Kesh 24 soatdan eski bo'lmasa, mavjud kursni qaytaramiz
    if USD_RATE_CACHE["rate"] and USD_RATE_CACHE["time"] and now - USD_RATE_CACHE["time"] < timedelta(hours=24):
        return USD_RATE_CACHE["rate"]

    try:
        resp = requests.get("https://cbu.uz/uz/arkhiv-kursov-valyut/json/USD/")
        data = resp.json()
        if isinstance(data, list) and len(data) > 0:
            rate = float(data[0]["Rate"])
            USD_RATE_CACHE = {"rate": rate, "time": now}
            print(f"💰 USD kursi yangilandi: 1 USD = {rate} so'm")
            return rate
    except Exception as e:
        print("⚠️ Kurs olishda xato:", e)

    # Zaxira qiymat (API ishlamasa)
    return USD_RATE_CACHE["rate"] or 12800.0

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


# Allowed users (preserve original)
ALLOWED_USERS = [1262207928, 963690743, 8450201406]


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
# Bot handlers (original handlers preserved, only small integration edits)
# ---------------------------

@bot.message_handler(commands=['start'])
def cmd_start(m):
    if m.from_user.id not in ALLOWED_USERS:
        bot.send_message(m.chat.id, "❌ Sizga bu botdan foydalanish ruxsat berilmagan.")
        return

    uid = m.from_user.id
    clear_state(uid)
    txt = ("Assalomu alaykum! 👋\n\n"
           "Quyidagi menyudan tanlang:\n")
    bot.send_message(m.chat.id, txt, reply_markup=main_keyboard())


# --- Add product ---
# --- BEGIN: Excel / Manual product add handlers (INSERT or REPLACE existing start_add_product) ---

@bot.message_handler(func=lambda m: m.text == "🔹 Yangi mahsulot qo'shish")
def start_add_product_menu(m):
    """
    Yangi mahsulot qo'shish — menyu:
    - Qo'lda (avvalgi oqimga ulanadi)
    - Excel orqali (fayl qabul qilib, ommaviy yuklash)
    """
    uid = m.from_user.id
    # menyu: inline tugmalar
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✏️ Qo'lda", callback_data="addprod_manual"))
    kb.add(types.InlineKeyboardButton("📥 Excel orqali yuklash", callback_data="addprod_excel"))
    kb.add(types.InlineKeyboardButton("Bekor qilish", callback_data="cancel"))
    try:
        bot.send_message(m.chat.id, "Mahsulot qo'shish usulini tanlang:", reply_markup=kb)
    except:
        bot.send_message(uid, "Mahsulot qo'shish usulini tanlang:", reply_markup=kb)
        
@bot.message_handler(func=lambda m: m.text == "➕ Qo‘lda kiritish")
def manual_add_product(message):
    bot.send_message(message.chat.id, "Mahsulot nomini kiriting:")
    bot.register_next_step_handler(message, process_product_name)

def process_product_name(message):
    name = message.text.strip()
    if not name:
        bot.send_message(message.chat.id, "❌ Noto‘g‘ri nom. Qaytadan kiriting.")
        return manual_add_product(message)
    bot.send_message(message.chat.id, "Mahsulot miqdorini kiriting (dona):")
    bot.register_next_step_handler(message, process_product_qty, name)

def process_product_qty(message, name):
    try:
        qty = int(message.text.strip())
    except ValueError:
        bot.send_message(message.chat.id, "❌ Iltimos, faqat son kiriting.")
        return bot.register_next_step_handler(message, process_product_qty, name)
    bot.send_message(message.chat.id, "Optovik (olingan) narxini kiriting (so‘mda):")
    bot.register_next_step_handler(message, process_product_cost, name, qty)

def process_product_cost(message, name, qty):
    """
    Foydalanuvchi optovik narxni USD da kiritadi.
    Bot avtomatik kursni oladi, so‘mga aylantiradi.
    """
    try:
        cost_price_usd = float(message.text.strip().replace(",", "."))
    except ValueError:
        bot.send_message(message.chat.id, "❌ Faqat son kiriting (masalan: 12.5).")
        return bot.register_next_step_handler(message, process_product_cost, name, qty)

    usd_rate = get_usd_rate()
    cost_price_som = int(cost_price_usd * usd_rate)

    bot.send_message(
        message.chat.id,
        f"💵 Kurs: 1 USD = {usd_rate:,.0f} so'm\n"
        f"Optovik narx: {cost_price_usd:.2f} $ = {cost_price_som:,} so'm\n\n"
        f"Endi sotuv narxini kiriting (so'mda):"
    )
    bot.register_next_step_handler(message, save_product_to_db, name, qty, cost_price_som, cost_price_usd, usd_rate)

def save_product_to_db(message, name, qty, cost_price_usd, usd_rate):
    """
    Mahsulotni bazaga saqlaydi yoki yangilaydi:
    - cost_price_usd — dollar narxi
    - usd_rate — joriy kurs
    - cost_price — so‘mda narx (kurs asosida)
    """
    try:
        suggest_price = int(message.text.strip().replace(" ", ""))
    except ValueError:
        bot.send_message(message.chat.id, "❌ Faqat son kiriting (so‘mda).")
        return bot.register_next_step_handler(
            message, save_product_to_db, name, qty, cost_price_usd, usd_rate
        )

    cost_price_som = int(cost_price_usd * usd_rate)  # dollarni so'mga o'girish

    conn = get_conn()
    cur = conn.cursor()

    # Bazada borligini tekshiramiz (nom va USD narx bo‘yicha)
    cur.execute(
        "SELECT id, qty FROM products WHERE name = %s AND cost_price_usd = %s;",
        (name, cost_price_usd)
    )
    existing = cur.fetchone()

    if existing:
        # Mahsulot bor bo'lsa, faqat miqdor yangilanadi
        new_qty = existing[1] + qty
        cur.execute("UPDATE products SET qty = %s, cost_price = %s, usd_rate = %s WHERE id = %s;",
                    (new_qty, cost_price_som, usd_rate, existing[0]))
        conn.commit()
        msg = (
            f"🔁 Mahsulot yangilandi:\n"
            f"{name}\n{new_qty} dona | {cost_price_usd:.2f} $ ({cost_price_som:,} so‘m)\n"
            f"Sotuv narxi: {suggest_price:,} so‘m"
        )
    else:
        # Yangi mahsulot qo'shish
        cur.execute(
            """
            INSERT INTO products (name, qty, cost_price, cost_price_usd, usd_rate, suggest_price)
            VALUES (%s, %s, %s, %s, %s, %s);
            """,
            (name, qty, cost_price_som, cost_price_usd, usd_rate, suggest_price)
        )
        conn.commit()
        msg = (
            f"✅ Yangi mahsulot qo‘shildi:\n"
            f"{name}\n{qty} dona | {cost_price_usd:.2f} $ ({cost_price_som:,} so‘m)\n"
            f"Sotuv narxi: {suggest_price:,} so‘m"
        )

    cur.close()
    conn.close()
    bot.send_message(message.chat.id, msg)

@bot.callback_query_handler(func=lambda c: c.data in ("addprod_manual", "addprod_excel", "cancel"))
def cb_addprod_menu(c):
    uid = c.from_user.id

    if c.data == "addprod_manual":
        # Qo‘lda mahsulot kiritish oqimi
        clear_state(uid)
        set_state(uid, "action", "add_product_manual")

        try:
            bot.edit_message_text(
                "Mahsulot nomini kiriting (lotin harflarda):",
                chat_id=c.message.chat.id,
                message_id=c.message.message_id
            )
        except:
            bot.send_message(uid, "Mahsulot nomini kiriting (lotin harflarda):")

        # 🔥 Shu qator juda muhim — foydalanuvchi javob berganda `process_product_name()` chaqiriladi
        bot.register_next_step_handler(c.message, process_product_name)

        bot.answer_callback_query(c.id)
        return

    if c.data == "addprod_excel":
        # Excel yuklash oqimi
        clear_state(uid)
        set_state(uid, "action", "add_product_excel_wait")
        try:
            bot.edit_message_text(
                "Iltimos Excel (.xlsx) faylni yuboring. Ustunlar nomi bo‘lishi mumkin:\n"
                "name/nom, qty/soni, cost_price/opt_narx, suggest_price/sotuv_narx.",
                chat_id=c.message.chat.id,
                message_id=c.message.message_id
            )
        except:
            bot.send_message(
                uid,
                "Iltimos Excel (.xlsx) faylni yuboring. Ustunlar nomi bo‘lishi mumkin:\n"
                "name/nom, qty/soni, cost_price/opt_narx, suggest_price/sotuv_narx."
            )
        bot.answer_callback_query(c.id)
        return

    # Bekor qilish
    try:
        bot.edit_message_text("Amal bekor qilindi.", chat_id=c.message.chat.id, message_id=c.message.message_id)
    except:
        bot.send_message(uid, "Amal bekor qilindi.", reply_markup=main_keyboard())

    clear_state(uid)
    bot.answer_callback_query(c.id)

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "add_product_excel_wait", content_types=['document'])
def handle_excel_upload(m):
    """
    Excel fayl qabul qilinganda ishlaydi.
    - Faylni yuklab oladi
    - pandas bilan o'qiydi
    - ustunlarni xaritalaydi (ing/uzb variantlarini qabul qiladi)
    - har bir qatorni qayta ishlaydi: agar name & cost_price mos bo'lsa -> qty +=, aks holda -> yangi product
    - natijani userga yuboradi
    """
    uid = m.from_user.id
    doc = m.document
    # tekshiruv: fayl turi xlsx bo'lsa davom etamiz
    if not doc or not (doc.mime_type in ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/octet-stream") or doc.file_name.lower().endswith((".xlsx", ".xls"))):
        bot.send_message(m.chat.id, "Iltimos .xlsx fayl yuboring.", reply_markup=cancel_keyboard())
        return

    bot.send_message(m.chat.id, "Fayl qabul qilindi, qayta ishlanmoqda... Iltimos kuting.")
    try:
        # yuklab olish
        file_info = bot.get_file(doc.file_id)
        downloaded = bot.download_file(file_info.file_path)
        xbuf = io.BytesIO(downloaded)
        # pandas bilan o'qish
        try:
            df = pd.read_excel(xbuf, engine="openpyxl")
        except Exception as e:
            # qayta urinib ko'rish (xlrd)?
            try:
                df = pd.read_excel(xbuf)
            except Exception as e2:
                bot.send_message(m.chat.id, f"Excel faylni o'qib bo'lmadi: {e}.", reply_markup=main_keyboard())
                clear_state(uid)
                return

        # Ustun nomlarini kichik harfga o'tkazish va strip qilish
        orig_cols = list(df.columns)
        cols_map = {c: c.strip().lower() for c in orig_cols}

        df.rename(columns={k: cols_map[k] for k in cols_map}, inplace=True)

        # Potentsial nom variantlari
        name_keys = ["name","nom","product","product_name","mahсулот","mahsol","mahsulot nomi"]
        qty_keys = ["qty","quantity","soni","qty (dona)","miqdor","son"]
        cost_keys = ["cost_price","cost","opt_narx","opt narx","optprice","optovik narxi",
    "optovik_narxi","optovik","optoviknarx","opt_narxi","opt_narx "]
        suggest_keys = ["suggest_price","sell_price","price","sotuv_narx","sotuv narx","sotish narxi",
    "sotish_narxi","taklif_price","taklifnarx","taklif narx","sotuvnarxi"]

        # topish funksiyasi
        def find_col(keys):
            for k in keys:
                if k in df.columns:
                    return k
            return None

        col_name = find_col(name_keys)
        col_qty = find_col(qty_keys)
        col_cost = find_col(cost_keys)
        col_suggest = find_col(suggest_keys)

        if not col_name:
            bot.send_message(m.chat.id, "Excel faylda mahsulot nomi topilmadi. Iltimos 'name' yoki 'nom' ustunli fayl yuboring.", reply_markup=main_keyboard())
            clear_state(uid); return
        if not col_qty:
            bot.send_message(m.chat.id, "Excel faylda miqdor (qty/soni) ustuni topilmadi.", reply_markup=main_keyboard())
            clear_state(uid); return
        if not col_cost:
            bot.send_message(m.chat.id, "Excel faylda optovik narx (cost_price) ustuni topilmadi.", reply_markup=main_keyboard())
            clear_state(uid); return

        # tozalash va turlarga o'tkazish
        df = df[[col_name, col_qty, col_cost] + ([col_suggest] if col_suggest else [])].copy()
        df = df.dropna(subset=[col_name])
        df[col_name] = df[col_name].astype(str).str.strip()
        # qty -> int
        def to_int_safe(x):
            try:
                if pd.isna(x):
                    return 0
                if isinstance(x, str):
                    x = x.replace(",", "").strip()
                return int(float(x))
            except:
                return 0
        df[col_qty] = df[col_qty].apply(to_int_safe)
        df[col_cost] = df[col_cost].apply(to_int_safe)
        if col_suggest:
            df[col_suggest] = df[col_suggest].apply(to_int_safe)
        else:
            df["suggest_temp"] = 0
            col_suggest = "suggest_temp"

        # qayta ishlash: DB ga kiritish
        conn = get_conn()
        cur = conn.cursor()
        inserted = 0
        updated = 0
        skipped = 0
        errors = []

        for idx, row in df.iterrows():
            pname = (row[col_name] or "").strip()
            pqty = int(row[col_qty] or 0)
            pcost = int(row[col_cost] or 0)
            psuggest = int(row[col_suggest] or 0)

            if not pname or pqty <= 0:
                skipped += 1
                continue

            # 1) Agar name va cost_price bir xil bo'lsa -> qty +=
            try:
                cur.execute("SELECT id, qty FROM products WHERE name ILIKE %s AND cost_price = %s LIMIT 1;", (pname, pcost))
                existing = cur.fetchone()
                if existing:
                    prod_id = existing[0]
                    cur.execute("UPDATE products SET qty = qty + %s, suggest_price = COALESCE(%s, suggest_price) WHERE id=%s;", (pqty, psuggest if psuggest>0 else None, prod_id))
                    updated += 1
                else:
                    # yangi yozuv
                    cur.execute("INSERT INTO products (name, qty, cost_price, suggest_price) VALUES (%s, %s, %s, %s);", (pname, pqty, pcost, psuggest if psuggest>0 else None))
                    inserted += 1
            except Exception as e:
                errors.append(f"Qator {idx+2}: {e}")  # +2 (header+0)
                continue

        conn.commit()
        cur.close()
        conn.close()

        # natija haqida xabar
        summary_lines = [
            "✅ Excel yuklash tugadi.",
            f"📥 Yangi qo'shilganlar: {inserted}",
            f"🔁 Mavjudlarga qo'shilganlar (qty yangilandi): {updated}",
            f"⏭️ O'tkazib yuborilgan (nom yoki qty noto'g'ri): {skipped}",
        ]
        if errors:
            summary_lines.append("\n⚠️ Ba'zi xatolar:")
            for e in errors[:10]:
                summary_lines.append(e)
            if len(errors) > 10:
                summary_lines.append(f"... va yana {len(errors)-10} ta xato.")

        bot.send_message(m.chat.id, "\n".join(summary_lines), reply_markup=main_keyboard())

    except Exception as e:
        traceback.print_exc()
        bot.send_message(m.chat.id, f"Xatolik yuz berdi: {e}", reply_markup=main_keyboard())
    finally:
        clear_state(uid)


@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "add_product_excel_wait", content_types=['text','photo','audio','video','voice','sticker'])
def handle_excel_wrong_type(m):
    # foydalanuvchi fayl o'rniga boshqa narsani yuborgan bo'lsa
    bot.send_message(m.chat.id, "Iltimos .xlsx formatidagi Excel fayl yuboring yoki 'Bekor qilish' tugmasi bilan chiqib keting.", reply_markup=cancel_keyboard())

# --- END: Excel / Manual product add handlers ---

# --- Search & Sell ---
@bot.message_handler(func=lambda m: m.text and (m.text.strip().lower() == "🛒 mahsulot sotish" or ("mahsulot" in m.text.lower() and "sot" in m.text.lower())))
def start_sell(m):
    uid = m.from_user.id
    clear_state(uid)
    clear_user_cart(uid)
    set_state(uid, "action", "sell_search")
    bot.send_message(m.chat.id, "Qaysi mahsulotni izlamoqchisiz? (nom yoki uning bir qismi, lotincha):", reply_markup=cancel_keyboard())

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "sell_search")
def sell_search(m):
    uid = m.from_user.id
    txt = (m.text or "").strip()
    if txt.lower() == "bekor qilish":
        clear_state(uid); clear_user_cart(uid); bot.send_message(m.chat.id, "Savdo bekor qilindi.", reply_markup=main_keyboard()); return
    if contains_cyrillic(txt):
        bot.send_message(m.chat.id, "Iltimos faqat lotincha kiriting.", reply_markup=cancel_keyboard()); return

    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, name, qty, suggest_price FROM products WHERE name ILIKE %s AND qty > 0 ORDER BY id;", (f"%{txt}%",))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    if not rows:
        bot.send_message(m.chat.id, "Mahsulot topilmadi. Yana urinib ko'ring yoki 'Bekor qilish' ni tanlang.", reply_markup=cancel_keyboard()); return

    kb = types.InlineKeyboardMarkup()
    for r in rows:
        label = f"{r['name']} -> {format_money(r['suggest_price'])} ({r['qty']} dona)"
        kb.add(types.InlineKeyboardButton(label, callback_data=f"addcart|{r['id']}"))
    kb.add(types.InlineKeyboardButton("🧺 Savatchaga o‘tish", callback_data="view_cart"))
    kb.add(types.InlineKeyboardButton("🔎 Yana izlash", callback_data="again_search"))
    bot.send_message(m.chat.id, "Topilgan mahsulotlar:", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("addcart|"))
def cb_addcart(c):
    uid = c.from_user.id
    try:
        _, pid = c.data.split("|")
        pid = int(pid)
    except:
        bot.answer_callback_query(c.id, "Noto'g'ri ma'lumot"); return

    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, name, qty, suggest_price FROM products WHERE id=%s;", (pid,))
    p = cur.fetchone()
    cur.close()
    conn.close()
    if not p:
        bot.answer_callback_query(c.id, "Mahsulot topilmadi."); return

    set_state(uid, "addcart_pid", pid)
    set_state(uid, "action", "addcart_qty")
    bot.send_message(c.message.chat.id, f"Mahsulot: <b>{p['name']}</b>\nMavjud: {p['qty']}\nTaklifiy narx: {format_money(p['suggest_price'])}\n\nSotiladigan miqdorni kiriting (son):", parse_mode="HTML", reply_markup=cancel_keyboard())
    bot.answer_callback_query(c.id)

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "addcart_qty")
def addcart_fill(m):
    uid = m.from_user.id
    txt = (m.text or "").strip()
    if txt.lower() == "bekor qilish":
        clear_state(uid); bot.send_message(m.chat.id, "Amal bekor qilindi.", reply_markup=main_keyboard()); return
    if not txt.isdigit():
        bot.send_message(m.chat.id, "Iltimos butun son kiriting (masalan: 2).", reply_markup=cancel_keyboard()); return
    qty = int(txt)
    pid = get_state(uid, "addcart_pid")
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, name, qty, suggest_price FROM products WHERE id=%s;", (pid,))
    p = cur.fetchone()
    cur.close()
    conn.close()
    if not p:
        bot.send_message(m.chat.id, "Mahsulot topilmadi.", reply_markup=main_keyboard()); clear_state(uid); return
    if qty > p['qty']:
        bot.send_message(m.chat.id, f"Mavjud miqdor yetarli emas. Mavjud: {p['qty']}", reply_markup=cancel_keyboard()); return
    set_state(uid, "addcart_qty", qty)
    set_state(uid, "action", "addcart_price")
    bot.send_message(m.chat.id, f"Sotiladigan narxni kiriting (so'm). Taklifiy: {format_money(p['suggest_price'])}", reply_markup=cancel_keyboard())

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "addcart_price")
def addcart_price(m):
    uid = m.from_user.id
    txt = (m.text or "").strip().replace(" ", "").replace(",", "")
    if txt.lower() == "bekor qilish":
        clear_state(uid); bot.send_message(m.chat.id, "Amal bekor qilindi.", reply_markup=main_keyboard()); return
    if not txt.isdigit():
        bot.send_message(m.chat.id, "Iltimos raqam kiriting (masalan: 120000).", reply_markup=cancel_keyboard()); return

    price = int(txt)
    pid = get_state(uid, "addcart_pid")
    qty = get_state(uid, "addcart_qty")

    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT name FROM products WHERE id=%s;", (pid,))
    p_row = cur.fetchone()
    if not p_row:
        cur.close(); conn.close()
        bot.send_message(m.chat.id, "Mahsulot topilmadi.", reply_markup=main_keyboard()); clear_state(uid); return
    pname = p_row['name']

    cur.execute("SELECT data FROM user_carts WHERE user_id=%s;", (uid,))
    existing = cur.fetchone()
    if existing and existing.get('data'):
        data = parse_cart_data(existing['data'])
    else:
        data = {"items": []}

    data['items'].append({"product_id": pid, "name": pname, "qty": qty, "price": price})

    # save
    if existing:
        cur.execute("UPDATE user_carts SET data=%s, updated_at=now() WHERE user_id=%s;", (json.dumps(data), uid))
    else:
        cur.execute("INSERT INTO user_carts (user_id, data) VALUES (%s, %s);", (uid, json.dumps(data)))
    conn.commit()
    cur.close()
    conn.close()

    clear_state(uid)

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("➕ Yana mahsulot qo‘shish", callback_data="again_search"))
    kb.add(types.InlineKeyboardButton("🧺 Savatchaga o‘tish", callback_data="view_cart"))
    kb.add(types.InlineKeyboardButton("❌ Savdoni bekor qilish", callback_data="cancel_sale"))
    bot.send_message(m.chat.id, f"✅ Mahsulot savatchaga qo‘shildi: <b>{pname}</b>\nMiqdor: {qty}\nNarx: {format_money(price)}", parse_mode="HTML", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data == "again_search")
def cb_again_search(c):
    uid = c.from_user.id
    set_state(uid, "action", "sell_search")
    try:
        bot.edit_message_text("Qaysi mahsulotni izlamoqchisiz? (nom yoki uning bir qismi, lotincha):", chat_id=c.message.chat.id, message_id=c.message.message_id)
    except:
        bot.send_message(c.message.chat.id, "Qaysi mahsulotni izlamoqchisiz? (nom yoki uning bir qismi, lotincha):", reply_markup=cancel_keyboard())
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c: c.data == "cancel_sale")
def cb_cancel_sale(c):
    uid = c.from_user.id
    clear_user_cart(uid)
    clear_state(uid)
    try:
        bot.edit_message_text("❌ Savdo bekor qilindi va savatcha tozalandi.", chat_id=c.message.chat.id, message_id=c.message.message_id)
    except:
        bot.send_message(c.message.chat.id, "❌ Savdo bekor qilindi va savatcha tozalandi.")
    bot.send_message(c.message.chat.id, "Asosiy menyu:", reply_markup=main_keyboard())
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c: c.data == "view_cart")
def cb_view_cart(c):
    uid = c.from_user.id
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT data FROM user_carts WHERE user_id=%s;", (uid,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row or not row.get('data') or not parse_cart_data(row.get('data')).get('items'):
        bot.answer_callback_query(c.id, "Savatcha bo‘sh")
        bot.send_message(c.message.chat.id, "Savatcha bo‘sh. Yana mahsulot qidirish uchun 'Mahsulot sotish' ni tanlang.", reply_markup=main_keyboard())
        return
    data = parse_cart_data(row.get('data'))
    items = data.get('items', [])
    total = sum(it['qty'] * it['price'] for it in items)
    text_lines = ["🧾 <b>Savatcha</b>\n"]
    for i, it in enumerate(items, 1):
        text_lines.append(f"{i}. {it['name']} — {it['qty']} x {format_money(it['price'])} = {format_money(it['qty']*it['price'])}")
    text_lines.append(f"\nUmumiy: <b>{format_money(total)}</b>")
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Buyurtmani tasdiqlash", callback_data="checkout"))
    kb.add(types.InlineKeyboardButton("Mahsulotni tahrirlash", callback_data="edit_cart"))
    kb.add(types.InlineKeyboardButton("Bekor qilish va bo‘shatish", callback_data="clear_cart"))
    try:
        bot.edit_message_text("\n".join(text_lines), chat_id=c.message.chat.id, message_id=c.message.message_id, parse_mode="HTML", reply_markup=kb)
    except:
        bot.send_message(c.message.chat.id, "\n".join(text_lines), parse_mode="HTML", reply_markup=kb)
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c: c.data == "clear_cart")
def cb_clear_cart(c):
    uid = c.from_user.id
    clear_user_cart(uid)
    try:
        bot.edit_message_text("Savatcha tozalandi.", chat_id=c.message.chat.id, message_id=c.message.message_id)
    except:
        bot.send_message(c.message.chat.id, "Savatcha tozalandi.")
    bot.answer_callback_query(c.id, "Savatcha bo‘shatildi.")
    bot.send_message(c.message.chat.id, "Asosiy menyu:", reply_markup=main_keyboard())

@bot.callback_query_handler(func=lambda c: c.data == "edit_cart")
def cb_edit_cart(c):
    uid = c.from_user.id
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Oxirgi mahsulotni o‘chirish", callback_data="remove_last"))
    kb.add(types.InlineKeyboardButton("Butun savatchani bo‘shatish", callback_data="clear_cart"))
    try:
        bot.edit_message_text("Tahrir variantlari:", chat_id=c.message.chat.id, message_id=c.message.message_id, reply_markup=kb)
    except:
        bot.send_message(c.message.chat.id, "Tahrir variantlari:", reply_markup=kb)
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c: c.data == "remove_last")
def cb_remove_last(c):
    uid = c.from_user.id
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT data FROM user_carts WHERE user_id=%s;", (uid,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close(); bot.answer_callback_query(c.id, "Savatcha bo‘sh"); bot.send_message(c.message.chat.id, "Savatcha bo‘sh.", reply_markup=main_keyboard()); return
    data = parse_cart_data(row.get('data'))
    if not data.get('items'):
        cur.close(); conn.close(); bot.answer_callback_query(c.id, "Savatcha bo‘sh"); bot.send_message(c.message.chat.id, "Savatcha bo‘sh.", reply_markup=main_keyboard()); return
    removed = data['items'].pop()
    if data['items']:
        cur.execute("UPDATE user_carts SET data=%s, updated_at=now() WHERE user_id=%s;", (json.dumps(data), uid))
    else:
        cur.execute("DELETE FROM user_carts WHERE user_id=%s;", (uid,))
    conn.commit()
    cur.close()
    conn.close()
    bot.answer_callback_query(c.id, f"Oxirgi mahsulot o‘chirildi: {removed.get('name')}")
    bot.send_message(c.message.chat.id, "Savatcha yangilandi.", reply_markup=main_keyboard())


# --- Checkout flow ---
@bot.callback_query_handler(func=lambda c: c.data == "checkout")
def cb_checkout(c):
    uid = c.from_user.id
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row("Mavjud mijozni tanlash", "Yangi mijoz qo'shish")
    kb.row("Bekor qilish")
    set_state(uid, "action", "checkout_choose_customer")
    try:
        bot.send_message(c.message.chat.id, "Mijozni tanlang:", reply_markup=kb)
    except:
        bot.send_message(c.from_user.id, "Mijozni tanlang:", reply_markup=kb)
    bot.answer_callback_query(c.id)

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "checkout_choose_customer")
def checkout_choose_customer(m):
    uid = m.from_user.id
    text = (m.text or "").strip()
    if text == "Bekor qilish":
        clear_state(uid); bot.send_message(m.chat.id, "Amal bekor qilindi.", reply_markup=main_keyboard()); return
    if text == "Yangi mijoz qo'shish":
        set_state(uid, "action", "checkout_new_customer_name"); bot.send_message(m.chat.id, "Mijoz ismi (lotincha):", reply_markup=cancel_keyboard()); return
    if text == "Mavjud mijozni tanlash":
        set_state(uid, "action", "checkout_search_customer"); bot.send_message(m.chat.id, "Mijoz telefon yoki ismini kiriting:", reply_markup=cancel_keyboard()); return
    bot.send_message(m.chat.id, "Iltimos menyudan tanlang.", reply_markup=cancel_keyboard())

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "checkout_new_customer_name")
def checkout_new_customer_name(m):
    uid = m.from_user.id
    text = (m.text or "").strip()
    if text.lower() == "bekor qilish":
        clear_state(uid); bot.send_message(m.chat.id, "Amal bekor qilindi.", reply_markup=main_keyboard()); return
    if contains_cyrillic(text):
        bot.send_message(m.chat.id, "Iltimos lotincha kiriting.", reply_markup=cancel_keyboard()); return
    set_state(uid, "new_customer_name", text); set_state(uid, "action", "checkout_new_customer_phone"); bot.send_message(m.chat.id, "Mijoz telefon raqamini kiriting (+998...):", reply_markup=cancel_keyboard())

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "checkout_new_customer_phone")
def checkout_new_customer_phone(m):
    uid = m.from_user.id
    phone = (m.text or "").strip()
    if phone.lower() == "bekor qilish":
        clear_state(uid); bot.send_message(m.chat.id, "Amal bekor qilindi.", reply_markup=main_keyboard()); return
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO customers (name, phone) VALUES (%s, %s) RETURNING id;", (get_state(uid, "new_customer_name"), phone))
    cust_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    set_state(uid, "checkout_customer_id", cust_id)
    set_state(uid, "action", "checkout_payment")
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row("Naqd", "Qarz")
    kb.row("Bekor qilish")
    bot.send_message(m.chat.id, "To'lov turini tanlang:", reply_markup=kb)

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "checkout_search_customer")
def checkout_search_customer(m):
    uid = m.from_user.id
    txt = (m.text or "").strip()
    if txt.lower() == "bekor qilish":
        clear_state(uid); bot.send_message(m.chat.id, "Amal bekor qilindi.", reply_markup=main_keyboard()); return
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, name, phone FROM customers WHERE phone ILIKE %s OR name ILIKE %s LIMIT 20;", (f"%{txt}%", f"%{txt}%"))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    if not rows:
        bot.send_message(m.chat.id, "Mijoz topilmadi, yangi mijoz qo'shish uchun 'Yangi mijoz qo'shish' ni tanlang.", reply_markup=cancel_keyboard()); return
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
    kb.row("Naqd", "Qarz"); kb.row("Bekor qilish")
    bot.send_message(c.message.chat.id, "To'lov turini tanlang:", reply_markup=kb)
    bot.answer_callback_query(c.id)

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "checkout_payment")
def checkout_payment(m):
    """
    Yangi oqim: foydalanuvchidan 'Naqd' yoki 'Qarz' olinadi,
    so'ng formatni so'ramasdan darhol sotuv yaratiladi va
    HAM matnli, HAM rasmli cheklar avtomatik yuboriladi.
    """
    uid = m.from_user.id
    txt = (m.text or "").strip().lower()
    if txt == "bekor qilish":
        clear_state(uid)
        bot.send_message(m.chat.id, "Amal bekor qilindi.", reply_markup=main_keyboard())
        return
    if txt not in ("naqd", "qarz"):
        bot.send_message(m.chat.id, "Iltimos 'Naqd' yoki 'Qarz' ni tanlang.", reply_markup=cancel_keyboard())
        return

    # Saqlaymiz
    set_state(uid, "checkout_payment_type", txt)

    # --- Yuklangan savatchani tekshirish ---
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT data FROM user_carts WHERE user_id=%s;", (uid,))
    row = cur.fetchone()
    if not row or not row.get('data') or not parse_cart_data(row.get('data')).get('items'):
        bot.send_message(m.chat.id, "Savatcha bo'sh - sotish imkoni yo'q.", reply_markup=main_keyboard())
        clear_state(uid)
        cur.close()
        conn.close()
        return

    data = parse_cart_data(row.get('data'))
    items = data.get('items', [])
    total = sum(it['qty'] * it['price'] for it in items)
    cust_id = get_state(uid, "checkout_customer_id")
    payment = txt

    try:
        # --- Sotuvni yaratish ---
        cur.execute("""
            INSERT INTO sales (customer_id, total_amount, payment_type, seller_phone)
            VALUES (%s, %s, %s, %s)
            RETURNING id, created_at;
        """, (cust_id, total, payment, SELLER_PHONE))
        sale = cur.fetchone()
        sale_id = sale['id']

        for it in items:
            cur.execute("""
                INSERT INTO sale_items (sale_id, product_id, name, qty, price, total)
                VALUES (%s,%s,%s,%s,%s,%s);
            """, (sale_id, it['product_id'], it['name'], it['qty'], it['price'], it['qty'] * it['price']))
            cur.execute("UPDATE products SET qty = qty - %s WHERE id=%s;", (it['qty'], it['product_id']))

        if payment == "qarz":
            cur.execute("INSERT INTO debts (customer_id, sale_id, amount) VALUES (%s, %s, %s);", (cust_id, sale_id, total))

        # Savatchani o'chiramiz
        cur.execute("DELETE FROM user_carts WHERE user_id=%s;", (uid,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        traceback.print_exc()
        bot.send_message(m.chat.id, f"Xatolik: {e}", reply_markup=main_keyboard())
        clear_state(uid)
        cur.close()
        conn.close()
        return
    finally:
        try:
            cur.close()
            conn.close()
        except:
            pass

    clear_state(uid)

    # --- Endi HAM matnli, HAM rasmli cheklarni yuboramiz ---
    try:
        # 1) Matnli chek
        bot.send_message(m.chat.id, receipt_text(sale_id), parse_mode="HTML")
    except Exception as e:
        print("Matnli chek yuborishda xato:", e)

    try:
        # 2) Rasmli chek
        img = receipt_image_bytes(sale_id)
        if img:
            img.seek(0)
            bot.send_photo(m.chat.id, img, caption="🧾 Sizning chek (rasm)")
    except Exception as e:
        print("Rasmli chek yuborishda xato:", e)
        # Agar rasm yuborolmasa, kamida matnli chek bor bo'ladi (yuqorida yuborilgan bo'lsa)

    # Tugatib asosiy menyu qaytaramiz
    bot.send_message(m.chat.id, "Savdo muvaffaqiyatli amalga oshirildi.✅✅✅", reply_markup=main_keyboard())

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "checkout_confirm_format")
def checkout_confirm_format(m):
    """
    This handler accepts user input 'Matn' or 'Rasm' (or synonyms).
    If 'Rasm' selected, robust receipt_image_bytes() will be used.
    """
    uid = m.from_user.id
    fmt = (m.text or "").strip().lower()
    if fmt == "bekor qilish":
        clear_state(uid)
        bot.send_message(m.chat.id, "Amal bekor qilindi.", reply_markup=main_keyboard())
        return

    # Accept multiple synonyms for image
    if fmt not in ("matn", "rasm", "image", "photo"):
        bot.send_message(m.chat.id, "Iltimos 'Matn' yoki 'Rasm' ni tanlang.", reply_markup=cancel_keyboard())
        return

    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT data FROM user_carts WHERE user_id=%s;", (uid,))
    row = cur.fetchone()
    if not row or not row.get('data') or not parse_cart_data(row.get('data')).get('items'):
        bot.send_message(m.chat.id, "Savatcha bo'sh - sotish imkoni yo'q.", reply_markup=main_keyboard())
        clear_state(uid)
        cur.close()
        conn.close()
        return

    data = parse_cart_data(row.get('data'))
    items = data.get('items', [])
    total = sum(it['qty'] * it['price'] for it in items)
    cust_id = get_state(uid, "checkout_customer_id")
    payment = get_state(uid, "checkout_payment_type")

    cur.execute("""
        INSERT INTO sales (customer_id, total_amount, payment_type, seller_phone)
        VALUES (%s, %s, %s, %s)
        RETURNING id, created_at;
    """, (cust_id, total, payment, SELLER_PHONE))
    sale = cur.fetchone()
    sale_id = sale['id']

    for it in items:
        cur.execute("""
            INSERT INTO sale_items (sale_id, product_id, name, qty, price, total)
            VALUES (%s,%s,%s,%s,%s,%s);
        """, (sale_id, it['product_id'], it['name'], it['qty'], it['price'], it['qty'] * it['price']))
        cur.execute("UPDATE products SET qty = qty - %s WHERE id=%s;", (it['qty'], it['product_id']))

    if payment == "qarz":
        cur.execute("INSERT INTO debts (customer_id, sale_id, amount) VALUES (%s, %s, %s);", (cust_id, sale_id, total))

    cur.execute("DELETE FROM user_carts WHERE user_id=%s;", (uid,))
    conn.commit()
    cur.close()
    conn.close()
    clear_state(uid)

    # Send receipt: text or image
    try:
        if fmt == "matn":
            bot.send_message(m.chat.id, receipt_text(sale_id), parse_mode="HTML", reply_markup=main_keyboard())
        else:
            img = receipt_image_bytes(sale_id)
            if not img:
                bot.send_message(m.chat.id, receipt_text(sale_id), parse_mode="HTML", reply_markup=main_keyboard())
            else:
                img.seek(0)
                bot.send_photo(m.chat.id, img, caption="🧾 Sizning chek (rasm)", reply_markup=main_keyboard())
    except Exception as e:
        # log and fallback
        print("Error generating/sending receipt image:", e)
        bot.send_message(m.chat.id, receipt_text(sale_id), parse_mode="HTML", reply_markup=main_keyboard())


# --- Stock export, stats, debts handlers (kept similar to original) ---
def export_stock_image():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, name, qty, cost_price, suggest_price FROM products ORDER BY id;")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    lines = ["Ombor holati"]
    if not rows:
        lines.append("Omborda hech qanday mahsulot yo'q.")
    else:
        for r in rows:
            lines.append(f"{r['id']}. {r['name']} — {r['qty']} dona — {r['cost_price_usd']:.2f} $ ({format_money(r['cost_price'])}) — taklif: {format_money(r['suggest_price'])}")

    font = _get_font(16)
    temp = Image.new("RGB", (1000, 2000), "white")
    d = ImageDraw.Draw(temp)
    w, h = _measure_text(d, "\n".join(lines), font)
    img_w = max(700, w + 40)
    img_h = h + 40
    img = Image.new("RGB", (img_w, img_h), "white")
    draw = ImageDraw.Draw(img)
    y = 20
    for ln in lines:
        draw.text((20, y), ln, font=font, fill="black")
        _, hh = _measure_text(draw, ln, font)
        y += hh + 6

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    buf.seek(0)
    return buf

def stats_image_bytes(period):
    lines = [f"Hisobot: {period}", f"Sana: {now_str()}", "", "Eslatma: to'liq statistikani yaratish uchun serverda ko'proq ma'lumot yig'ilishi kerak."]
    font = _get_font(16)
    temp = Image.new("RGB", (800, 300), "white")
    d = ImageDraw.Draw(temp)
    w, h = _measure_text(d, "\n".join(lines), font)
    img_w = max(600, w + 40)
    img_h = h + 40
    img = Image.new("RGB", (img_w, img_h), "white")
    draw = ImageDraw.Draw(img)
    y = 20
    for ln in lines:
        draw.text((20, y), ln, font=font, fill="black")
        _, hh = _measure_text(draw, ln, font)
        y += hh + 6
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    buf.seek(0)
    return buf
  
@bot.message_handler(func=lambda m: m.text == "📊 Statistika")
def cmd_statistics(m):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Sotuvlar tarixi (ID bo'yicha qidirish)", callback_data="stat_search_id"))
    kb.add(types.InlineKeyboardButton("Kunlik", callback_data="stat_daily"))
    kb.add(types.InlineKeyboardButton("Oylik", callback_data="stat_monthly"))
    kb.add(types.InlineKeyboardButton("Yillik", callback_data="stat_yearly"))
    kb.add(types.InlineKeyboardButton("Ombor holati (excel/pdf)", callback_data="stock_export"))
    bot.send_message(m.chat.id, "Statistika variantlari:", reply_markup=kb)

# --- REPLACE existing stats handler with this block ---
from datetime import date, time

def _period_range_for(period_key):
    try:
        tz = ZoneInfo(TIMEZONE)
    except Exception:
        tz = None
    now = datetime.now(tz) if tz else datetime.utcnow() + timedelta(hours=5)
    today = now.date()
    if period_key == "daily":
        start = datetime.combine(today, time.min)
        end = start + timedelta(days=1)
    elif period_key == "monthly":
        start = datetime.combine(date(today.year, today.month, 1), time.min)
        if today.month == 12:
            end = datetime.combine(date(today.year+1, 1, 1), time.min)
        else:
            end = datetime.combine(date(today.year, today.month+1, 1), time.min)
    elif period_key == "yearly":
        start = datetime.combine(date(today.year, 1, 1), time.min)
        end = datetime.combine(date(today.year+1, 1, 1), time.min)
    else:
        raise ValueError("Unknown period")
    if tz:
        start = start.replace(tzinfo=tz)
        end = end.replace(tzinfo=tz)
    return start, end

def generate_stats_df(start_dt, end_dt):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT si.product_id, si.name AS product_name,
               SUM(si.qty) AS sold_qty,
               SUM(si.total) AS total_sold,
               COALESCE(p.cost_price,0) AS cost_price
        FROM sale_items si
        JOIN sales s ON s.id = si.sale_id
        LEFT JOIN products p ON p.id = si.product_id
        WHERE s.created_at >= %s AND s.created_at < %s
        GROUP BY si.product_id, si.name, p.cost_price
        ORDER BY si.name;
    """, (start_dt, end_dt))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        return pd.DataFrame(columns=["product_id","name","sold_qty","cost_price","total_sold","total_cost","profit"])

    df = pd.DataFrame(rows)
    df["sold_qty"] = df["sold_qty"].astype(int)
    df["total_sold"] = df["total_sold"].astype(int)
    df["cost_price"] = df["cost_price"].astype(int)
    df["total_cost"] = df["sold_qty"] * df["cost_price"]
    df["profit"] = df["total_sold"] - df["total_cost"]
    df = df.rename(columns={"product_name":"name"})
    df = df[["product_id","name","sold_qty","cost_price","total_sold","total_cost","profit"]]
    return df

def make_excel_from_df(df, title, start_dt, end_dt):
    import io
    import pandas as pd
    from datetime import datetime

    def now_str():
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        meta = pd.DataFrame([{
            "Hisobot": title,
            "Sana boshi": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "Sana oxiri": end_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "Yaratildi": now_str()
        }])
        meta.to_excel(writer, index=False, sheet_name="Meta")

        if df.empty:
            pd.DataFrame([{"Xabar": "Ushbu davrda hech qanday mahsulot sotilmagan."}]).to_excel(
                writer, index=False, sheet_name="Hisobot"
            )
        else:
            df.to_excel(writer, index=False, sheet_name="Hisobot")
            ws = writer.sheets["Hisobot"]
            start_row = len(df) + 2
            ws.cell(row=start_row, column=2, value="Jami")
            ws.cell(row=start_row, column=3, value=int(df["sold_qty"].sum()))
            ws.cell(row=start_row, column=5, value=int(df["total_sold"].sum()))
            ws.cell(row=start_row, column=6, value=int(df["total_cost"].sum()))
            ws.cell(row=start_row, column=7, value=int(df["profit"].sum()))

    out.seek(0)
    return out

def generate_sale_excel_by_id(sale_id):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT s.id AS sale_id, s.created_at, s.total_amount, s.payment_type, c.name as cust_name, c.phone as cust_phone
        FROM sales s
        LEFT JOIN customers c ON c.id = s.customer_id
        WHERE s.id = %s;
    """, (sale_id,))
    sale = cur.fetchone()
    if not sale:
        cur.close(); conn.close()
        return None
    cur.execute("""
        SELECT si.product_id, si.name, si.qty, si.price, si.total, COALESCE(p.cost_price,0) AS cost_price
        FROM sale_items si
        LEFT JOIN products p ON p.id = si.product_id
        WHERE si.sale_id = %s;
    """, (sale_id,))
    items = cur.fetchall()
    cur.close()
    conn.close()

    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        sale_meta = pd.DataFrame([{
            "Sale ID": sale["sale_id"],
            "Sana": sale["created_at"].strftime("%Y-%m-%d %H:%M:%S") if sale["created_at"] else "",
            "Mijoz": sale.get("cust_name") or "",
            "Telefon": sale.get("cust_phone") or "",
            "To'lov turi": sale.get("payment_type") or "",
            "Jami summa": sale.get("total_amount") or 0
        }])
        sale_meta.to_excel(writer, index=False, sheet_name="Sale")
        if not items:
            pd.DataFrame([{"Xabar":"Ushbu chekda elementlar yo'q"}]).to_excel(writer, index=False, sheet_name="Items")
        else:
            df_items = pd.DataFrame(items)
            df_items.to_excel(writer, index=False, sheet_name="Items")
        writer.save()
    out.seek(0)
    return out

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("stat_"))
def cb_stat(c):
    try:
        cmd = c.data
        # ID search flow
        if cmd == "stat_search_id":
            set_state(c.from_user.id, "action", "stat_search_by_id")
            bot.send_message(c.message.chat.id, "Sotuv ID ni kiriting:", reply_markup=cancel_keyboard())
            bot.answer_callback_query(c.id)
            return

        if cmd == "stock_export":
            kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            kb.row("Excel", "Rasm"); kb.row("Bekor qilish")
            set_state(c.from_user.id, "action", "stock_export_choose_format")
            bot.send_message(c.from_user.id, "Qaysi formatda olmoqchisiz?", reply_markup=kb)
            bot.answer_callback_query(c.id)
            return

        # map to period
        period_map = {"stat_daily":"daily", "stat_monthly":"monthly", "stat_yearly":"yearly"}
        if cmd not in period_map:
            bot.answer_callback_query(c.id, "Noma'lum buyruq")
            return

        period_key = period_map[cmd]
        start_dt, end_dt = _period_range_for(period_key)
        df = generate_stats_df(start_dt, end_dt)
        title = f"{period_key.title()} hisobot"
        excel_buf = make_excel_from_df(df, title, start_dt, end_dt)
        filename = f"hisobot_{period_key}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        bot.send_document(c.message.chat.id, excel_buf, visible_file_name=filename, caption=f"{title}: {start_dt.strftime('%Y-%m-%d')} — {(end_dt - timedelta(seconds=1)).strftime('%Y-%m-%d')}")
        bot.answer_callback_query(c.id)
    except Exception as e:
        print("cb_stat error:", e)
        traceback.print_exc()
        try:
            bot.answer_callback_query(c.id, "Xatolik yuz berdi")
        except:
            pass

@bot.message_handler(func=lambda m: get_state(m.from_user.id, "action") == "stat_search_by_id")
def stat_search_by_id_handler(m):
    uid = m.from_user.id
    txt = (m.text or "").strip()
    if txt.lower() == "bekor qilish":
        clear_state(uid)
        bot.send_message(m.chat.id, "Amal bekor qilindi.", reply_markup=main_keyboard())
        return
    if not txt.isdigit():
        bot.send_message(m.chat.id, "Iltimos to'g'ri ID kiriting (son).", reply_markup=cancel_keyboard())
        return
    sale_id = int(txt)
    clear_state(uid)
    excel_buf = generate_sale_excel_by_id(sale_id)
    if not excel_buf:
        bot.send_message(m.chat.id, f"Sotuv topilmadi: ID={sale_id}", reply_markup=main_keyboard())
        return
    filename = f"chek_{sale_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    bot.send_document(m.chat.id, excel_buf, visible_file_name=filename, caption=f"Chek №{sale_id} hisobot (Excel)", reply_markup=main_keyboard())

# ---------------------------
# NEW: Export all products as Excel (triggered by menu button "📊 Ombor (Excel)")
# ---------------------------
@bot.message_handler(func=lambda m: m.text == "📊 Ombor (Excel)")
def export_products_excel_handler(m):
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT id, name, qty, cost_price, suggest_price, created_at FROM products ORDER BY id;")
        rows = cur.fetchall()
        cur.close()
        conn.close()

        if not rows:
            bot.send_message(m.chat.id, "📦 Omborda hech qanday mahsulot yo‘q.", reply_markup=main_keyboard())
            return

        # DataFrame yaratamiz
        df = pd.DataFrame(rows)

        # O'zbekcha sarlavhalar qo'yamiz
        df.rename(columns={
            "id": "№",
            "name": "Mahsulot nomi",
            "qty": "Miqdor (dona)",
            "cost_price": "Narx (so‘m)",
            "suggest_price": "Taklif narxi (so‘m)",
            "created_at": "Qo‘shilgan sana"
        }, inplace=True)

        # Raqamlarni formatlaymiz (butun son sifatida)
        df["Narx (so‘m)"] = df["Narx (so‘m)"].astype(float).round(0).astype(int)
        df["Taklif narxi (so‘m)"] = df["Taklif narxi (so‘m)"].astype(float).round(0).astype(int)

        # Excel fayl yaratish
        import tempfile, os
        from datetime import datetime

        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
            file_path = tmp.name

        # Pandas ExcelWriter orqali formatlab yozamiz
        with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Ombor")

        file_name = f"ombor_{datetime.now().strftime('%Y-%m-%d')}.xlsx"

        with open(file_path, "rb") as f:
            bot.send_document(m.chat.id, f, caption=f"📊 Ombor ro‘yxati ({file_name})", reply_markup=main_keyboard())

        os.remove(file_path)

    except Exception as e:
        bot.send_message(m.chat.id, f"❌ Xatolik: {e}", reply_markup=main_keyboard())

@bot.message_handler(func=lambda m: m.text == "📋 Qarzdorlar ro'yxati")
def cmd_debts(m):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT d.id, d.amount, d.created_at, c.name, c.phone
        FROM debts d
        JOIN customers c ON d.customer_id = c.id
        ORDER BY d.created_at DESC;
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        bot.send_message(m.chat.id, "✅ Hozircha qarzdorlar yo‘q.", reply_markup=main_keyboard())
        return

    # --- Matnli ro‘yxatni chiroyli chiqarish ---
    text_lines = ["📋 <b>Qarzdorlar ro‘yxati:</b>\n"]
    for i, r in enumerate(rows, start=1):
        sana = r['created_at'].strftime("%d.%m.%Y") if r['created_at'] else "-"
        text_lines.append(f"{i}. <b>{r['name']}</b> ({r['phone']})\n💰 {format_money(r['amount'])} — 📅 {sana}\n")

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("⬇️ Excel faylni yuklab olish", callback_data="debts_excel"))

    bot.send_message(m.chat.id, "\n".join(text_lines), parse_mode="HTML", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data == "debts_excel")
def cb_debts_excel(c):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT c.name AS Mijoz, c.phone AS Telefon, d.amount AS Qarz_summasi, d.created_at AS Sana
        FROM debts d
        JOIN customers c ON d.customer_id = c.id
        ORDER BY d.created_at DESC;
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        bot.answer_callback_query(c.id, "Qarzdorlar topilmadi.")
        return

    df = pd.DataFrame(rows)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Qarzdorlar")
    buf.seek(0)

    file_name = f"qarzdorlar_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
    bot.send_document(
        c.message.chat.id,
        buf,
        visible_file_name=file_name,
        caption="📊 Qarzdorlar ro‘yxati (Excel formatida)"
    )

    bot.answer_callback_query(c.id, "Excel fayl yuborildi ✅")


@bot.message_handler(func=lambda m: True)
def fallback(m):
    txt = m.text or ""
    if contains_cyrillic(txt):
        bot.send_message(m.chat.id, "Iltimos, faqat lotin alifbosida yozing. Bot faqat lotin yozuvini qabul qiladi.", reply_markup=main_keyboard())
    else:
        bot.send_message(m.chat.id, "Menyu orqali tanlang yoki /start ni bosing.", reply_markup=main_keyboard())


# --- Run ---


# ------------------ ADDED: Statistics generation + daily auto-send ------------------
import threading
import time as _time
from datetime import date, time as _timeobj

def _period_range_for(period_key):
    try:
        tz = ZoneInfo(TIMEZONE)
    except Exception:
        tz = None
    now = datetime.now(tz) if tz else datetime.utcnow() + timedelta(hours=5)
    today = now.date()
    if period_key == "daily":
        start = datetime.combine(today, _timeobj.min)
        end = start + timedelta(days=1)
    elif period_key == "monthly":
        start = datetime.combine(date(today.year, today.month, 1), _timeobj.min)
        if today.month == 12:
            end = datetime.combine(date(today.year+1, 1, 1), _timeobj.min)
        else:
            end = datetime.combine(date(today.year, today.month+1, 1), _timeobj.min)
    elif period_key == "yearly":
        start = datetime.combine(date(today.year, 1, 1), _timeobj.min)
        end = datetime.combine(date(today.year+1, 1, 1), _timeobj.min)
    else:
        raise ValueError("Unknown period")
    if tz:
        start = start.replace(tzinfo=tz)
        end = end.replace(tzinfo=tz)
    return start, end

def generate_stats_df(start_dt, end_dt):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT si.product_id, si.name AS product_name,
               SUM(si.qty) AS sold_qty,
               SUM(si.total) AS total_sold,
               COALESCE(p.cost_price,0) AS cost_price
        FROM sale_items si
        JOIN sales s ON s.id = si.sale_id
        LEFT JOIN products p ON p.id = si.product_id
        WHERE s.created_at >= %s AND s.created_at < %s
        GROUP BY si.product_id, si.name, p.cost_price
        ORDER BY si.name;
    """, (start_dt, end_dt))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        return pd.DataFrame(columns=["product_id","name","sold_qty","cost_price","total_sold","total_cost","profit"])

    df = pd.DataFrame(rows)
    df = df.rename(columns={"product_name":"name"})
    df["sold_qty"] = df["sold_qty"].astype(int)
    df["total_sold"] = df["total_sold"].astype(int)
    df["cost_price"] = df["cost_price"].astype(int)
    df["total_cost"] = df["sold_qty"] * df["cost_price"]
    df["profit"] = df["total_sold"] - df["total_cost"]
    df = df[["product_id","name","sold_qty","cost_price","total_sold","total_cost","profit"]]
    return df

def make_excel_from_df(df, title, start_dt, end_dt):
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        meta = pd.DataFrame([{
            "Hisobot": title,
            "Sana boshi": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "Sana oxiri": end_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "Yaratildi": now_str()
        }])
        meta.to_excel(writer, index=False, sheet_name="Meta")
        if df.empty:
            pd.DataFrame([{"Xabar":"Ushbu davrda hech qanday mahsulot sotilmagan."}]).to_excel(writer, index=False, sheet_name="Hisobot")
        else:
            df.to_excel(writer, index=False, sheet_name="Hisobot")
            ws = writer.sheets["Hisobot"]
            start_row = len(df) + 3
            ws.cell(row=start_row, column=2, value="Jami")
            ws.cell(row=start_row, column=3, value=int(df["sold_qty"].sum()))
            ws.cell(row=start_row, column=5, value=int(df["total_sold"].sum()))
            ws.cell(row=start_row, column=6, value=int(df["total_cost"].sum()))
            ws.cell(row=start_row, column=7, value=int(df["profit"].sum()))
    out.seek(0)
    return out

def daily_report_thread():
    """Thread that sends yesterday's report once every day at ~00:05 server time."""
    # small initial delay to allow bot to start
    _time.sleep(5)
    while True:
        try:
            # compute yesterday range
            tz = None
            try:
                tz = ZoneInfo(TIMEZONE)
            except:
                tz = None
            nowz = datetime.now(tz) if tz else datetime.utcnow() + timedelta(hours=5)
            yesterday = (nowz.date() - timedelta(days=1))
            start = datetime.combine(yesterday, _timeobj.min)
            end = start + timedelta(days=1)
            if tz:
                start = start.replace(tzinfo=tz)
                end = end.replace(tzinfo=tz)
            df = generate_stats_df(start, end)
            title = f"Daily automated report for {start.strftime('%Y-%m-%d')}"
            buf = make_excel_from_df(df, title, start, end)
            filename = f"auto_report_{start.strftime('%Y%m%d')}.xlsx"
            # send to allowed users
            for admin_id in ALLOWED_USERS:
                try:
                    buf.seek(0)
                    bot.send_document(admin_id, buf, visible_file_name=filename, caption=f"Avtomatik kunlik hisobot: {start.strftime('%Y-%m-%d')}")
                except Exception:
                    # individual failure should not stop others
                    pass
            # sleep until next day ~00:05 (calculate seconds)
            nowz = datetime.now(tz) if tz else datetime.utcnow() + timedelta(hours=5)
            next_run = datetime.combine(nowz.date() + timedelta(days=1), _timeobj(hour=0, minute=5))
            if tz:
                next_run = next_run.replace(tzinfo=tz)
            sleep_seconds = max(60, (next_run - nowz).total_seconds())
            _time.sleep(sleep_seconds)
        except Exception:
            # avoid thread death
            _time.sleep(60)

# helper to start thread; will be called in __main__
def start_daily_report_thread():
    t = threading.Thread(target=daily_report_thread, daemon=True)
    t.start()

# ------------------ END ADDED BLOCK ------------------
if __name__ == "__main__":
    init_db()
    print("✅ Bot ishga tushdi!")
    try:
        start_daily_report_thread()
        bot.infinity_polling()
    except Exception as e:
        print("Polling exception:", e)
        raise
