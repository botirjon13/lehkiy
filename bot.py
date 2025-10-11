send_message(m.chat.id, "Hozircha qarzdorlar yo'q.", reply_markup=main_keyboard()); return
    kb = types.InlineKeyboardMarkup(); kb.add(types.InlineKeyboardButton("Ro'yxatni Excel ko'rinishida yuborish", callback_data="debts_excel"))
    text_lines = ["📋 Qarzdorlar ro'yxati:"]
    for r in rows:
        text_lines.append(f"- {r['name']} {r['phone']} — {format_money(r['amount'])} ({r['created_at'].strftime('%d.%m.%Y')})")
    bot.send_message(m.chat.id, "\n".join(text_lines), reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "debts_excel")
def cb_debts_excel(c):
    conn = get_conn(); cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT d.id, c.name, c.phone, d.amount, d.created_at FROM debts d JOIN customers c ON d.customer_id=c.id;")
    rows = cur.fetchall(); cur.close(); conn.close()
    df = pd.DataFrame(rows); buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer: df.to_excel(writer, index=False, sheet_name="Debts")
    buf.seek(0); bot.send_document(c.message.chat.id, buf, caption="Qarzdorlar (Excel)"); bot.answer_callback_query(c.id)

@bot.message_handler(func=lambda m: True)
def fallback(m):
    txt = m.text or ""
    if contains_cyrillic(txt):
        bot.send_message(m.chat.id, "Iltimos, faqat lotin alifbosida yozing. Bot faqat lotin yozuvini qabul qiladi.", reply_markup=main_keyboard())
    else:
        bot.send_message(m.chat.id, "Menyu orqali tanlang yoki /start ni bosing.", reply_markup=main_keyboard())

# --- Run ---
if name == "__main__":
    init_db()
    print("✅ Bot ishga tushdi!")
    try:
        bot.infinity_polling()
    except Exception as e:
        print("Polling exception:", e)
        raise
