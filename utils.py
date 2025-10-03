def format_sale_items(items: dict) -> str:
    text = ""
    for product, data in items.items():
        text += f"{product}: {data['quantity']} x {data['price']} = {data['quantity'] * data['price']} so'm\n"
    return text

def format_report(records):
    total = sum(record['total_amount'] for record in records)
    text = f"Sotuvlar hisobot (tanlangan davr uchun):\n\n"
    for record in records:
        text += f"{record['sale_time'].strftime('%Y-%m-%d %H:%M')} - {record['total_amount']} so'm\n"
    text += f"\nJami: {total} so'm"
    return text
