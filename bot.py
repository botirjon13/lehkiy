execute("SELECT id, name, qty, cost_price, suggest_price, created_at FROM products ORDER BY id;")
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
if name == "__main__":
    init_db()
    print("Bot ishga tushmoqda...")
    bot.infinity_polling()
