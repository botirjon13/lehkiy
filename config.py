import os

BOT_TOKEN = os.getenv("BOT_TOKEN")  # Токен телеграм-бота из переменных окружения
DATABASE_URL = os.getenv("DATABASE_URL")  # PostgreSQL URL (Railway и т.п.)

ADMIN_ID = int(os.getenv("ADMIN_ID", 0))  # Telegram ID администратора для отчетов
