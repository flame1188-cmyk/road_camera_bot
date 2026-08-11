"""bot.analysis.menu — построение главного меню действий.

Содержит:
  • _build_menu_keyboard — клавиатура главного меню по кэшированным данным

Зависимости:
  • bot.analysis.state._get_current_cards — для получения текущих карточек

Выделено из единого bot/analysis.py (Phase 3-4). 100% pure.
"""
from __future__ import annotations

from bot._state import *
from bot.analysis.state import _get_current_cards


def _build_menu_keyboard(
    context: ContextTypes.DEFAULT_TYPE,
) -> tuple[str, "InlineKeyboardMarkup"] | tuple[None, None]:
    """
    Строит клавиатуру главного меню по кэшированным данным.
    Возвращает (text, keyboard) или (None, None) если данных нет.
    Переиспользуется после каждого сценария для возврата в меню.
    """
    reg_name = context.user_data.get("analytics_reg_name", "")
    period = context.user_data.get("analytics_period")
    current_cards = _get_current_cards(context)

    if not period or not current_cards:
        return None, None

    card_count = len(current_cards)

    prev_year = period.year - 1
    prev_label = period.label.replace(str(period.year), str(prev_year))

    buttons = []
    buttons.append([InlineKeyboardButton(
        f"\U0001F4CA Анализ ({prev_label})",
        callback_data="do_analytics",
    )])

    # Кнопка "Анализ с ИИ" — доступна если есть любой LLM (бесплатный или платный)
    if is_any_llm_available():
        if is_paid_llm_available():
            # Есть оба провайдера — покажем подменю выбора
            buttons.append([InlineKeyboardButton(
                f"\U0001F916 Анализ с ИИ ({prev_label})",
                callback_data="choose_ai_method",
            )])
        else:
            # Только бесплатный — сразу запускаем
            buttons.append([InlineKeyboardButton(
                f"\U0001F916 Анализ с ИИ ({prev_label})",
                callback_data="do_analytics_ai",
            )])

    buttons.append([InlineKeyboardButton(
        "\U0001F525 Очаги ДТП",
        callback_data="do_concentration",
    )])
    buttons.append([InlineKeyboardButton(
        "\U0001F4CD Статистика по точке",
        callback_data="do_point_stats",
    )])
    buttons.append([InlineKeyboardButton(
        "\U0001F5FA HTML-карта ДТП",
        callback_data="do_html_map",
    )])
    buttons.append([InlineKeyboardButton(
        "\U0001F504 Сменить данные",
        callback_data="change_data",
    )])

    keyboard = InlineKeyboardMarkup(buttons)

    text = (
        f"\u2705 Данные: <b>{reg_name}</b> — {period.label}\n"
        f"ДТП: {card_count}\n\n"
        f"Выберите действие:\n\n"
        f"\U0001F4CA <b>Без ИИ</b> — математический анализ (таблицы, проценты)\n"
    )

    if is_any_llm_available():
        if is_paid_llm_available():
            text += (
                f"\U0001F916 <b>С ИИ</b> — анализ нейросетью (бесплатный или полный)\n"
            )
        else:
            text += (
                f"\U0001F916 <b>С ИИ</b> — анализ + резюме от нейросети\n"
            )

    text += (
        f"\U0001F525 <b>Очаги ДТП</b> — места концентрации аварийности\n"
        f"\U0001F4CD <b>По точке</b> — статистика ДТП по координатам\n"
        f"\U0001F5FA <b>HTML-карта</b> — интерактивная карта всех ДТП\n"
        f"\U0001F504 <b>Сменить данные</b> — новая выгрузка\n\n"
        f"Или /dtp для новой выгрузки."
    )

    return text, keyboard
