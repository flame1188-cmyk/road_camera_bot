"""
Патч для существующего bot.py: добавляет кнопку открытия Mini App.

Скопируйте функцию ниже в ваш bot.py и вызовите её в обработчике /start.

Требования:
- python-telegram-bot >= 20.0
- URL вашего Mini App (https://yourdomain.ru) должен быть задан
  в настройках бота через @BotFather → Bot Settings → Menu Button → Configure
  menu button
"""
from __future__ import annotations

import os
from telegram import (
    Update,
    WebAppInfo,
    KeyboardButton,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
)
from telegram.ext import ContextTypes


# URL вашего развёрнутого Mini App (без закрывающего слеша)
MINIAPP_URL = os.getenv("MINIAPP_URL", "https://yourdomain.ru")


async def setup_miniapp_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Устанавливает Menu Button (кнопка слева от текстового поля ввода),
    открывающую Mini App.
    """
    await context.bot.set_chat_menu_button(
        chat_id=update.effective_chat.id,
        menu_button=MenuButtonWebApp(
            text="Открыть аналитику",
            web_app=WebAppInfo(url=MINIAPP_URL),
        ),
    )


def get_miniapp_keyboard() -> ReplyKeyboardMarkup:
    """
    Возвращает keyboard с кнопкой, открывающей Mini App.
    Используйте в reply_markup обработчиков /start, /help.
    """
    keyboard = [
        [
            KeyboardButton(
                text="📊 Открыть аналитику ДТП",
                web_app=WebAppInfo(url=MINIAPP_URL),
            )
        ],
        [KeyboardButton(text="❓ Помощь")],
    ]
    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def get_miniapp_inline_button() -> InlineKeyboardMarkup:
    """
    Возвращает inline-кнопку для встраивания в любое сообщение.
    """
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                text="📊 Открыть аналитику ДТП",
                web_app=WebAppInfo(url=MINIAPP_URL),
            )
        ]]
    )


# ============================================================
# Пример использования в обработчике /start
# ============================================================
async def start_handler_with_miniapp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Замените ваш существующий /start на эту реализацию."""
    await setup_miniapp_menu(update, context)

    welcome_text = (
        "👋 Добро пожно пожаловать в бот статистики ДТП!\n\n"
        "📊 Нажмите кнопку ниже, чтобы открыть интерактивную аналитику "
        "с картами, графиками и выгрузкой Excel.\n\n"
        "Или используйте команды:\n"
        "• /dtp — выгрузка через inline-кнопки\n"
        "• /regions — список регионов\n"
        "• /help — справка"
    )

    await update.message.reply_text(
        welcome_text,
        reply_markup=get_miniapp_keyboard(),
    )


# ============================================================
# WebApp data handler — обрабатывает данные, отправленные из Mini App
# ============================================================
async def web_app_data_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Если Mini App отправляет данные обратно в бот через
    tg.sendData() / Telegram.WebApp.sendData(), они приходят сюда.
    """
    data = update.message.web_app_data.data
    await update.message.reply_text(
        f"Получены данные из Mini App:\n{data}"
    )
