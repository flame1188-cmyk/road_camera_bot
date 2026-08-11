"""bot.keyboards — построение inline-клавиатур.

Содержит:
  • build_region_keyboard — выбор региона с пагинацией
  • build_period_keyboard — выбор периода (месяц/квартал/полугодие/год)

Выделено из единого bot.py (Phase 3-2). 100% pure.
"""
from bot._state import *

def build_region_keyboard(
    regions: list[dict[str, str]],
    page: int = 0,
) -> InlineKeyboardMarkup:
    """Строит клавиатуру выбора региона с пагинацией."""
    total = len(regions)
    total_pages = max(1, (total + REGIONS_PER_PAGE - 1) // REGIONS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start = page * REGIONS_PER_PAGE
    end = min(start + REGIONS_PER_PAGE, total)
    page_regions = regions[start:end]

    buttons = []

    # Кнопки регионов
    for r in page_regions:
        # Короткая метка: название + код
        label = r["name"]
        if len(label) > 35:
            label = label[:33] + ".."
        buttons.append([InlineKeyboardButton(
            f"{label} ({r['code']})",
            callback_data=f"r:{r['code']}",
        )])

    # Навигация
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("<< Назад", callback_data=f"rp:{page - 1}"))
    nav_row.append(InlineKeyboardButton(
        f"{page + 1}/{total_pages}",
        callback_data="rp:noop",
    ))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Вперёд >>", callback_data=f"rp:{page + 1}"))
    buttons.append(nav_row)

    buttons.append([InlineKeyboardButton("Отмена", callback_data="cancel")])

    return InlineKeyboardMarkup(buttons)


def build_period_keyboard(year: int) -> InlineKeyboardMarkup:
    """Строит клавиатуру выбора периода."""
    buttons = []

    # Строка 1: годовые периоды
    buttons.append([
        InlineKeyboardButton(f"Весь {year} год", callback_data=f"py:{year}"),
        InlineKeyboardButton("Полугодие 1", callback_data=f"ph:1:{year}"),
        InlineKeyboardButton("Полугодие 2", callback_data=f"ph:2:{year}"),
    ])

    # Строка 2: кварталы
    buttons.append([
        InlineKeyboardButton(f"I кв", callback_data=f"pq:1:{year}"),
        InlineKeyboardButton(f"II кв", callback_data=f"pq:2:{year}"),
        InlineKeyboardButton(f"III кв", callback_data=f"pq:3:{year}"),
        InlineKeyboardButton(f"IV кв", callback_data=f"pq:4:{year}"),
    ])

    # Строка 3: произвольное количество месяцев
    buttons.append([
        InlineKeyboardButton(f"За 2 мес", callback_data=f"pn:2:{year}"),
        InlineKeyboardButton(f"За 4 мес", callback_data=f"pn:4:{year}"),
        InlineKeyboardButton(f"За 5 мес", callback_data=f"pn:5:{year}"),
        InlineKeyboardButton(f"За 7 мес", callback_data=f"pn:7:{year}"),
    ])
    buttons.append([
        InlineKeyboardButton(f"За 8 мес", callback_data=f"pn:8:{year}"),
        InlineKeyboardButton(f"За 9 мес", callback_data=f"pn:9:{year}"),
        InlineKeyboardButton(f"За 10 мес", callback_data=f"pn:10:{year}"),
        InlineKeyboardButton(f"За 11 мес", callback_data=f"pn:11:{year}"),
    ])

    # Строки 5-6: месяцы (по 6 в строке)
    for row_start in (1, 7):
        row = []
        for m in range(row_start, row_start + 6):
            row.append(InlineKeyboardButton(
                MONTH_SHORT[m], callback_data=f"pm:{m}:{year}",
            ))
        buttons.append(row)

    # Навигация по годам
    buttons.append([
        InlineKeyboardButton(f"<< {year - 1}", callback_data=f"yy:{year - 1}"),
        InlineKeyboardButton(str(year), callback_data="yy:noop"),
        InlineKeyboardButton(f"{year + 1} >>", callback_data=f"yy:{year + 1}"),
    ])

    # Кнопка «Назад»
    buttons.append([InlineKeyboardButton("<< Назад к регионам", callback_data="back")])
    buttons.append([InlineKeyboardButton("Отмена", callback_data="cancel")])

    return InlineKeyboardMarkup(buttons)


