"""
Road Camera Assessment Bot
Telegram бот для анализа участков дороги и выгрузки данных ДТП с stat.gibdd.ru.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime

# SSL patch
import httpx
_orig = httpx.AsyncClient.__init__


def _patched(self, *a, **kw):
    kw.setdefault('verify', False)
    _orig(self, *a, **kw)


httpx.AsyncClient.__init__ = _patched

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import (
    validate_config,
    ALLOWED_USER_IDS,
    ENABLE_NEWS_SEARCH,
    GOOGLE_MAPS_API_KEY,
    LLM_API_KEY,
    MAPILLARY_ACCESS_TOKEN,
    TELEGRAM_BOT_TOKEN,
    VLM_API_KEY,
    VLM_API_URL,
    VLM_MODEL,
    YANDEX_API_KEY,
)
from gibdd.api_client import fetch_dtp_data, fetch_regions, extract_accident_cards
from gibdd.parser import build_file1_data, build_file2_data
from gibdd.excel_gen import (
    generate_both_files,
    generate_analytics_file,
    generate_concentration_file,
)
from gibdd.analytics import (
    calculate_metrics,
    compare_metrics,
    build_analytics_message,
    build_analytics_excel_data,
    get_analytics_column_names,
    extract_raw_supplement,
    _get_camera_status,
)
from gibdd.concentration import (
    calculate_concentration_points,
    build_concentration_excel_data,
    build_concentration_detail_data,
    get_concentration_column_names,
    get_detail_column_names,
    _is_off_road,
)
from gibdd.request_parser import (
    parse_user_message,
    ensure_regions_loaded,
    ParsedPeriod,
    find_region,
)
from road.analyzer import parse_coordinates, analyze_road_section

# ========================
# Логирование
# ========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

REGIONS_PER_PAGE = 8
MONTH_SHORT = {
    1: "Янв", 2: "Фев", 3: "Мар", 4: "Апр",
    5: "Май", 6: "Июн", 7: "Июл", 8: "Авг",
    9: "Сен", 10: "Окт", 11: "Ноя", 12: "Дек",
}


# ========================
# Вспомогательные функции
# ========================

def is_user_allowed(user_id: int) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS


def _make_progress_bar(current: int, total: int) -> str:
    filled = "●"
    empty = "○"
    done = int(current / total * 10)
    return f"[{filled * done}{empty * (10 - done)}]"


def _get_regions(context: ContextTypes.DEFAULT_TYPE) -> list[dict[str, str]]:
    return context.bot_data.get("regions", [])


async def _load_regions_if_needed(context: ContextTypes.DEFAULT_TYPE) -> list[dict[str, str]]:
    regions = _get_regions(context)
    if not regions:
        regions = await ensure_regions_loaded()
        context.bot_data["regions"] = regions
    return regions


# ========================
# Клавиатуры
# ========================

def build_region_keyboard(regions: list[dict[str, str]], page: int = 0) -> InlineKeyboardMarkup:
    total = len(regions)
    total_pages = max(1, (total + REGIONS_PER_PAGE - 1) // REGIONS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * REGIONS_PER_PAGE
    end = min(start + REGIONS_PER_PAGE, total)

    buttons = []
    for r in regions[start:end]:
        label = r["name"]
        if len(label) > 35:
            label = label[:33] + ".."
        buttons.append([InlineKeyboardButton(f"{label} ({r['code']})", callback_data=f"r:{r['code']}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("<< Назад", callback_data=f"rp:{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="rp:noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Вперёд >>", callback_data=f"rp:{page + 1}"))
    buttons.append(nav_row)
    buttons.append([InlineKeyboardButton("Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(buttons)


def build_period_keyboard(year: int) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(f"Весь {year} год", callback_data=f"py:{year}"),
         InlineKeyboardButton("Полугодие 1", callback_data=f"ph:1:{year}"),
         InlineKeyboardButton("Полугодие 2", callback_data=f"ph:2:{year}")],
        [InlineKeyboardButton("I кв", callback_data=f"pq:1:{year}"),
         InlineKeyboardButton("II кв", callback_data=f"pq:2:{year}"),
         InlineKeyboardButton("III кв", callback_data=f"pq:3:{year}"),
         InlineKeyboardButton("IV кв", callback_data=f"pq:4:{year}")],
        [InlineKeyboardButton(f"9 мес ({MONTH_SHORT[1]}-{MONTH_SHORT[9]})", callback_data=f"p9:{year}")],
        [InlineKeyboardButton(MONTH_SHORT[m], callback_data=f"pm:{m}:{year}") for m in range(1, 7)],
        [InlineKeyboardButton(MONTH_SHORT[m], callback_data=f"pm:{m}:{year}") for m in range(7, 13)],
        [InlineKeyboardButton(f"<< {year - 1}", callback_data=f"yy:{year - 1}"),
         InlineKeyboardButton(str(year), callback_data="yy:noop"),
         InlineKeyboardButton(f"{year + 1} >>", callback_data=f"yy:{year + 1}")],
        [InlineKeyboardButton("<< Назад к регионам", callback_data="back")],
        [InlineKeyboardButton("Отмена", callback_data="cancel")],
    ]
    return InlineKeyboardMarkup(buttons)


# ========================
# Command handlers
# ========================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_user_allowed(update.effective_user.id):
        await update.message.reply_text("У вас нет доступа к этому боту.")
        return
    await update.message.reply_text(
        "Привет! Я бот для анализа дорог и выгрузки данных ДТП с stat.gibdd.ru.\n\n"
        "ГИБДД — данные ДТП:\n"
        "  /dtp — выгрузка через кнопки (регион → период)\n"
        "  /help — справка по запросам\n"
        "  /regions — список регионов\n\n"
        "Оценка участков дороги:\n"
        "  /check <координаты> — анализ участка\n"
        "  /road <координаты> — то же самое\n"
        "  /roadhelp — справка по оценке\n\n"
        "После выгрузки ДТП бот предложит:\n"
        "  📊 Анализ — сравнение периодов\n"
        "  🤖 Анализ с ИИ — анализ + резюме нейросети\n"
        "  🔥 Очаги ДТП — места концентрации"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_user_allowed(update.effective_user.id):
        return
    await update.message.reply_text(
        "Справка по использованию бота\n\n"
        "--- Данные ДТП (stat.gibdd.ru) ---\n"
        "/dtp → кнопки: регион → период\n"
        "Текстом: «Вологодская область за 2025 год»\n"
        "Строгий: «2.2024 1101»\n\n"
        "--- Аналитика ---\n"
        "📊 Анализ — математическое сравнение\n"
        "🤖 AI-анализ — + резюме нейросети\n"
        "🔥 Очаги ДТП — концентрация аварийности\n\n"
        "--- Оценка дорог ---\n"
        "/check 55.7558 37.6176 — анализ участка\n"
        "Или отправьте ссылку на Яндекс/Google Карты\n\n"
        "--- Команды ---\n"
        "/dtp /help /regions /check /road /roadhelp"
    )


async def cmd_dtp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_user_allowed(update.effective_user.id):
        return
    await _show_region_keyboard(update, context, page=0)


async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_user_allowed(update.effective_user.id):
        return
    text = update.message.text or ""
    args = text.split(maxsplit=1)[1] if len(text.split()) > 1 else ""
    if not args:
        await update.message.reply_text(
            "Использование: /check <координаты>\n"
            "Пример: /check 55.7558 37.6176\n"
            "Или отправьте ссылку на Яндекс/Google Карты"
        )
        return
    await _do_road_check(update, context, args)


async def cmd_road(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_user_allowed(update.effective_user.id):
        return
    text = update.message.text or ""
    args = text.split(maxsplit=1)[1] if len(text.split()) > 1 else ""
    if not args:
        await update.message.reply_text(
            "Использование: /road <координаты>\n"
            "Пример: /road 55.7558 37.6176"
        )
        return
    await _do_road_check(update, context, args)


async def cmd_roadhelp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_user_allowed(update.effective_user.id):
        return
    await update.message.reply_text(
        "Оценка участка дороги для установки камеры\n\n"
        "Формат координат:\n"
        "  /check 55.7558 37.6176\n"
        "  /road 55.7558,37.6176\n\n"
        "Ссылки:\n"
        "  https://yandex.ru/maps/?ll=37.6176%2C55.7558\n"
        "  https://maps.google.com/?q=55.7558,37.6176\n\n"
        "Бот проанализирует:\n"
        "  - Изображения с Яндекс Карт и Mapillary\n"
        "  - Данные OpenStreetMap (дорога, освещение, переходы)\n"
        "  - Экспертная оценка через VLM (нейросеть)\n"
        "  - Рекомендации по установке комплекса"
    )


async def cmd_regions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_user_allowed(update.effective_user.id):
        return
    msg = await update.message.reply_text("Загружаю список регионов...")
    regions = await _load_regions_if_needed(context)
    if not regions:
        await msg.edit_text("Не удалось загрузить список регионов.")
        return
    lines = [f"<b>Код — Регион</b> ({len(regions)} всего)\n"]
    for r in regions:
        lines.append(f"<code>{r['code']}</code> — {r['name']}")
    for i in range(0, len(lines), 40):
        await update.message.reply_text("\n".join(lines[i:i + 40]), parse_mode="HTML")
    await msg.delete()


async def _show_region_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0) -> None:
    msg = await update.message.reply_text("Загружаю список регионов...")
    regions = await _load_regions_if_needed(context)
    if not regions:
        await msg.edit_text("Не удалось загрузить список регионов.")
        return
    keyboard = build_region_keyboard(regions, page)
    await msg.edit_text("Выберите регион:", reply_markup=keyboard)


# ========================
# Road assessment
# ========================

async def _do_road_check(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    coords = parse_coordinates(text)
    if not coords:
        await update.message.reply_text(
            "Не удалось извлечь координаты.\n"
            "Пример: /check 55.7558 37.6176\n"
            "Или отправьте ссылку на Яндекс/Google Карты"
        )
        return

    lat, lon = coords
    chat_id = update.effective_chat.id

    status = await update.message.reply_text(
        f"🔍 Анализ участка дороги\nКоординаты: {lat}, {lon}\n\nПодготовка..."
    )

    async def progress(msg: str):
        try:
            await status.edit_text(msg)
        except Exception:
            pass

    try:
        result = await analyze_road_section(
            lat=lat, lon=lon,
            vlm_api_key=VLM_API_KEY or None,
            vlm_api_url=VLM_API_URL,
            vlm_model=VLM_MODEL,
            progress_callback=progress,
        )

        try:
            await status.delete()
        except Exception:
            pass

        # Отправляем текстовый отчёт
        await update.message.reply_text(result["formatted_message"])

        # Отправляем Excel
        if result.get("excel_bytes"):
            await context.bot.send_document(
                chat_id=chat_id,
                document=result["excel_bytes"],
                filename=result.get("excel_filename", "road_assessment.xlsx"),
                caption=f"Отчёт: {lat}, {lon}",
            )

        logger.info(f"Анализ дороги завершён: {lat}, {lon}")

    except Exception as e:
        logger.exception(f"Ошибка анализа дороги: {e}")
        try:
            await status.edit_text(f"Ошибка анализа: {e}")
        except Exception:
            pass


# ========================
# Callback handler
# ========================

async def on_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    if not is_user_allowed(query.from_user.id):
        await query.edit_message_text("У вас нет доступа.")
        return

    data = query.data
    try:
        if data.startswith("rp:"):
            parts = data.split(":")
            if parts[1] != "noop":
                page = int(parts[1])
                keyboard = build_region_keyboard(_get_regions(context), page)
                await query.edit_message_text("Выберите регион:", reply_markup=keyboard)
            return

        if data.startswith("r:"):
            reg_code = data[2:]
            regions = _get_regions(context)
            reg_name = "Регион " + reg_code
            for r in regions:
                if r["code"] == reg_code:
                    reg_name = r["name"]
                    break
            context.user_data["reg_code"] = reg_code
            context.user_data["reg_name"] = reg_name
            current_year = datetime.now().year
            context.user_data["sel_year"] = current_year
            keyboard = build_period_keyboard(current_year)
            await query.edit_message_text(f"Регион: {reg_name}\n\nВыберите период:", reply_markup=keyboard)
            return

        if data.startswith("py:"):
            year = int(data[3:])
            period = ParsedPeriod(months=list(range(1, 13)), year=year, label=f"Весь {year} год")
            await _start_fetching(query, context, period)
            return

        if data.startswith("pq:"):
            parts = data.split(":")
            q = int(parts[1])
            year = int(parts[2])
            start = (q - 1) * 3 + 1
            period = ParsedPeriod(months=list(range(start, start + 3)), year=year,
                                 label=f"{['I','II','III','IV'][q-1]} кв {year}")
            await _start_fetching(query, context, period)
            return

        if data.startswith("ph:"):
            parts = data.split(":")
            half = int(parts[1])
            year = int(parts[2])
            if half == 1:
                months, label = list(range(1, 7)), f"Полугодие 1 {year}"
            else:
                months, label = list(range(7, 13)), f"Полугодие 2 {year}"
            await _start_fetching(query, context, ParsedPeriod(months=months, year=year, label=label))
            return

        if data.startswith("p9:"):
            year = int(data[3:])
            period = ParsedPeriod(months=list(range(1, 10)), year=year, label=f"9 мес {year}")
            await _start_fetching(query, context, period)
            return

        if data.startswith("pm:"):
            parts = data.split(":")
            month = int(parts[1])
            year = int(parts[2])
            month_names = {1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель", 5: "Май", 6: "Июнь",
                          7: "Июль", 8: "Август", 9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"}
            period = ParsedPeriod(months=[month], year=year, label=f"{month_names.get(month, '')} {year}")
            await _start_fetching(query, context, period)
            return

        if data.startswith("yy:"):
            parts = data.split(":")
            if parts[1] != "noop":
                year = int(parts[1])
                context.user_data["sel_year"] = year
                keyboard = build_period_keyboard(year)
                reg_name = context.user_data.get("reg_name", "")
                await query.edit_message_text(f"Регион: {reg_name}\n\nВыберите период:", reply_markup=keyboard)
            return

        if data == "back":
            context.user_data.pop("reg_code", None)
            context.user_data.pop("reg_name", None)
            keyboard = build_region_keyboard(_get_regions(context), page=0)
            await query.edit_message_text("Выберите регион:", reply_markup=keyboard)
            return

        if data == "do_analytics":
            await _run_analysis(update, context, use_llm=False)
            return

        if data == "do_analytics_ai":
            await _run_analysis(update, context, use_llm=True)
            return

        if data == "do_concentration":
            await _run_concentration_points(update, context)
            return

        if data == "end_qa":
            _clear_analytics_data(context.user_data)
            await query.edit_message_text("Режим вопросов завершён.\n/dtp — новая выгрузка")
            return

        if data == "cancel":
            context.user_data.clear()
            await query.edit_message_text("Отменено. /dtp — начать заново.")
            return

    except Exception as e:
        logger.exception(f"Ошибка callback: {e}")
        try:
            await query.edit_message_text(f"Ошибка: {e}\n/dtp — начать заново.")
        except Exception:
            pass


# ========================
# Мультизапрос с прогрессом
# ========================

async def _start_fetching(query, context: ContextTypes.DEFAULT_TYPE, period: ParsedPeriod) -> None:
    reg_code = context.user_data.get("reg_code", "")
    reg_name = context.user_data.get("reg_name", "Регион " + reg_code)
    dat_list = period.get_dat_list()
    total = len(dat_list)

    await query.edit_message_text(f"Выгрузка: {reg_name}\nПериод: {period.label}\nЗапросов: {total}\n\nПодготовка...")

    all_cards = []
    errors = []

    for i, dat in enumerate(dat_list, 1):
        month_num = int(dat.split(".")[0])
        month_name = {1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель", 5: "Май", 6: "Июнь",
                     7: "Июль", 8: "Август", 9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"}.get(month_num, dat)
        progress = _make_progress_bar(i, total)
        try:
            await query.edit_message_text(
                f"Выгрузка: {reg_name}\nПериод: {period.label}\n\n{progress} {i}/{total}\n{month_name} {period.year}..."
            )
        except Exception:
            pass
        try:
            api_response = await fetch_dtp_data(dat=dat, reg=reg_code, pok="1")
            cards = extract_accident_cards(api_response)
            all_cards.extend(cards)
        except Exception as e:
            errors.append(f"{month_name} {period.year}: {e}")
            logger.error(f"{dat}: ОШИБКА — {e}")

    if not all_cards and errors:
        await query.edit_message_text(f"Не удалось получить данные.\n\n" + "\n".join(f"- {e}" for e in errors))
        return

    try:
        await query.edit_message_text(f"Выгрузка: {reg_name}\nНайдено ДТП: {len(all_cards)}\nГенерация Excel...")

        file1_data = build_file1_data(all_cards)
        file2_data = build_file2_data(all_cards)
        file1_bytes, file2_bytes = generate_both_files(file1_data, file2_data)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_reg = reg_name.replace(" ", "_")[:30]
        await query.edit_message_text("Готово! Отправляю файлы...")

        chat_id = query.message.chat_id
        await context.bot.send_document(chat_id=chat_id, document=file1_bytes,
                                       filename=f"dtp_cards_{safe_reg}_{period.year}_{timestamp}.xlsx",
                                       caption=f"Карточки ДТП\n{reg_name} | {period.label}\nДТП: {len(all_cards)}")
        await context.bot.send_document(chat_id=chat_id, document=file2_bytes,
                                       filename=f"dtp_uch_{safe_reg}_{period.year}_{timestamp}.xlsx",
                                       caption=f"Участники ДТП\n{reg_name} | {period.label}\nУчастников: {len(file2_data)}")
        try:
            await query.message.delete()
        except Exception:
            pass

        await _offer_analysis(context, chat_id, reg_name, reg_code, period, all_cards)

    except Exception as e:
        logger.exception(f"Ошибка генерации файлов: {e}")
        try:
            await query.edit_message_text(f"Ошибка: {e}")
        except Exception:
            pass
    finally:
        for key in ["reg_code", "reg_name", "sel_year"]:
            context.user_data.pop(key, None)


async def _offer_analysis(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int,
    reg_name: str, reg_code: str, period: ParsedPeriod, current_cards: list[dict],
) -> None:
    prev_year = period.year - 1
    prev_label = period.label.replace(str(period.year), str(prev_year))
    context.user_data["analytics_ready"] = True
    context.user_data["analytics_reg_code"] = reg_code
    context.user_data["analytics_reg_name"] = reg_name
    context.user_data["analytics_period"] = period
    context.user_data["analytics_cards"] = current_cards

    buttons = [[InlineKeyboardButton(f"📊 Анализ ({prev_label})", callback_data="do_analytics")]]
    if LLM_API_KEY:
        buttons.append([InlineKeyboardButton(f"🤖 AI-анализ ({prev_label})", callback_data="do_analytics_ai")])
    buttons.append([InlineKeyboardButton("🔥 Очаги ДТП", callback_data="do_concentration")])

    text = f"✅ Выгрузка завершена: {len(current_cards)} ДТП.\n\n"
    if LLM_API_KEY:
        text += "📊 Без ИИ — математический анализ\n🤖 С ИИ — анализ + резюме нейросети\n"
    text += "🔥 Очаги ДТП — места концентрации аварийности"

    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")


async def _run_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE, use_llm: bool = False) -> None:
    chat_id = update.effective_chat.id
    reg_code = context.user_data.get("analytics_reg_code", "")
    reg_name = context.user_data.get("analytics_reg_name", "")
    period = context.user_data.get("analytics_period")
    current_cards = context.user_data.get("analytics_cards", [])

    if not reg_code or not period or not current_cards:
        await update.callback_query.edit_message_text("Данные не найдены. Выполните выгрузку заново.")
        return

    prev_year = period.year - 1
    dat_list_prev = [f"{m}.{prev_year}" for m in period.months]
    prev_label = period.label.replace(str(period.year), str(prev_year))
    current_label = period.label
    mode = "🤖 AI" if use_llm else "📊"

    status = await context.bot.send_message(
        chat_id=chat_id, text=f"{mode}-анализ: загрузка данных за {prev_year}..."
    )

    prev_cards = []
    for i, dat in enumerate(dat_list_prev, 1):
        month_num = int(dat.split(".")[0])
        mn = {1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель", 5: "Май", 6: "Июнь",
              7: "Июль", 8: "Август", 9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"}.get(month_num, dat)
        await status.edit_text(f"{mode}-анализ: {_make_progress_bar(i, len(dat_list_prev))} {i}/{len(dat_list_prev)}\n{mn} {prev_year}...")
        try:
            api_response = await fetch_dtp_data(dat=dat, reg=reg_code, pok="1")
            cards = extract_accident_cards(api_response)
            prev_cards.extend(cards)
        except Exception as e:
            logger.error(f"Аналитика {dat}: {e}")

    if not prev_cards:
        await status.edit_text(f"⚠️ Не удалось загрузить данные за {prev_label}.")
        return

    await status.edit_text(f"{mode}-анализ: считаю метрики...")
    current_metrics = calculate_metrics(current_cards)
    previous_metrics = calculate_metrics(prev_cards)
    comparison = compare_metrics(current_metrics, previous_metrics)

    context.user_data["analytics_comparison"] = comparison
    context.user_data["analytics_current_label"] = current_label
    context.user_data["analytics_prev_label"] = prev_label
    context.user_data["analytics_prev_cards"] = prev_cards

    llm_summary = None
    if use_llm and LLM_API_KEY:
        try:
            await status.edit_text(f"{mode}-анализ: запрос к нейросети...")
            raw_sup = extract_raw_supplement(current_cards, current_label, max_cards=25)
            raw_sup += extract_raw_supplement(prev_cards, prev_label, max_cards=25)
            from utils.llm_client import get_ai_summary
            llm_summary = await get_ai_summary(
                comparison=comparison, reg_name=reg_name,
                current_label=current_label, prev_label=prev_label,
                raw_supplement=raw_sup, news_context="",
                progress_callback=lambda msg: status.edit_text(msg),
            )
        except Exception as e:
            logger.error(f"Ошибка LLM: {e}")

    analytics_data = build_analytics_excel_data(comparison, reg_name, current_label, prev_label)
    column_names = get_analytics_column_names(current_label, prev_label)
    analytics_bytes = generate_analytics_file(analytics_data, column_names)

    try:
        await status.delete()
    except Exception:
        pass

    if use_llm and llm_summary:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🤖 <b>AI-аналитика: {reg_name}</b>\n{current_label} vs {prev_label}\n\n<i>{llm_summary}</i>",
            parse_mode="HTML",
        )
        analytics_text = build_analytics_message(comparison, reg_name, current_label, prev_label)
        await context.bot.send_message(
            chat_id=chat_id, text=f"📊 <b>Детальные данные:</b>\n\n{analytics_text}", parse_mode="HTML",
        )
    else:
        analytics_text = build_analytics_message(comparison, reg_name, current_label, prev_label)
        await context.bot.send_message(chat_id=chat_id, text=analytics_text, parse_mode="HTML")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_reg = reg_name.replace(" ", "_")[:30]
    suffix = "_ai" if use_llm else ""
    await context.bot.send_document(
        chat_id=chat_id, document=analytics_bytes,
        filename=f"dtp_analytics{suffix}_{safe_reg}_{period.year}_vs_{prev_year}_{timestamp}.xlsx",
        caption=f"📊 Аналитика: {reg_name}\n{current_label} vs {prev_label}\n"
                f"Текущий: {len(current_cards)} | Прошлый: {len(prev_cards)} ДТП",
    )

    if LLM_API_KEY:
        context.user_data["qa_mode"] = True
        await context.bot.send_message(
            chat_id=chat_id,
            text="❓ Задавайте вопросы по данным.\nПросто напишите текстом.\n/dtp — новая выгрузка",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Завершить", callback_data="end_qa")]]),
        )
    else:
        _clear_analytics_data(context.user_data)


def _clear_analytics_data(user_data: dict) -> None:
    for key in [
        "analytics_ready", "analytics_reg_code", "analytics_reg_name",
        "analytics_period", "analytics_cards", "analytics_comparison",
        "analytics_current_label", "analytics_prev_label", "qa_mode",
    ]:
        user_data.pop(key, None)


async def _run_concentration_points(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    reg_name = context.user_data.get("analytics_reg_name", "")
    period = context.user_data.get("analytics_period")
    current_cards = context.user_data.get("analytics_cards", [])

    if not reg_name or not period or not current_cards:
        await update.callback_query.edit_message_text("Данные не найдены.")
        return

    status = await context.bot.send_message(chat_id=chat_id, text="🔥 Расчёт очагов ДТП...\nФильтрация карточек...")

    async def progress(msg: str):
        try:
            await status.edit_text(msg)
        except Exception:
            pass

    try:
        points = await calculate_concentration_points(current_cards, progress_callback=progress)

        if not points:
            await status.edit_text("Очаги ДТП не найдены.\nВозможно, недостаточно данных.")
            return

        # Сводный файл + детализация (2 листа в одном файле)
        conc_data = build_concentration_excel_data(points)
        conc_cols = get_concentration_column_names()
        detail_data = build_concentration_detail_data(points)
        detail_cols = get_detail_column_names()
        conc_bytes = generate_concentration_file(conc_data, conc_cols, detail_data, detail_cols)

        try:
            await status.delete()
        except Exception:
            pass

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_reg = reg_name.replace(" ", "_")[:30]

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🔥 <b>Очаги ДТП: {reg_name}</b>\n{period.label}\n\nНайдено: {len(points)} очагов",
            parse_mode="HTML",
        )
        await context.bot.send_document(
            chat_id=chat_id, document=conc_bytes,
            filename=f"dtp_conc_{safe_reg}_{period.year}_{timestamp}.xlsx",
            caption=f"Очаги ДТП (сводка + детализация, 2 листа)\n{len(points)} очагов",
        )

        logger.info(f"Очаги отправлены: {len(points)}")

    except Exception as e:
        logger.exception(f"Ошибка очагов: {e}")
        try:
            await status.edit_text(f"Ошибка расчёта очагов: {e}")
        except Exception:
            pass


# ========================
# Message handlers
# ========================

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает текстовые сообщения (ГИБДД запросы, координаты, QA)."""
    if not is_user_allowed(update.effective_user.id):
        return

    text = (update.message.text or "").strip()
    if not text:
        return

    # QA mode
    if context.user_data.get("qa_mode"):
        comparison = context.user_data.get("analytics_comparison")
        reg_name = context.user_data.get("analytics_reg_name", "")
        current_label = context.user_data.get("analytics_current_label", "")
        prev_label = context.user_data.get("analytics_prev_label", "")

        if not comparison:
            await update.message.reply_text("Данные для вопросов не найдены.")
            return

        await update.message.chat.send_action("typing")

        # Формируем контекст
        from gibdd.analytics import build_analytics_message
        context_text = build_analytics_message(comparison, reg_name, current_label, prev_label)

        try:
            from utils.llm_client import get_ai_answer
            answer = await get_ai_answer(text, context_text)
            await update.message.reply_text(answer, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ошибка QA: {e}")
            await update.message.reply_text(f"Ошибка при ответе на вопрос: {e}")
        return

    # Автоопределение координат
    coords = parse_coordinates(text)
    if coords:
        lat, lon = coords
        await _do_road_check(update, context, text)
        return

    # Обработка текстового запроса ГИБДД
    period, reg_code = parse_user_message(text)
    if period and reg_code:
        # Устанавливаем данные и запускаем
        context.user_data["reg_code"] = reg_code
        context.user_data["reg_name"] = f"Регион {reg_code}"
        dat_list = period.get_dat_list()

        msg = await update.message.reply_text(
            f"Запрос: {period.label}\nРегион: {reg_code}\nЗапросов: {len(dat_list)}\n\nПодготовка..."
        )

        all_cards = []
        for i, dat in enumerate(dat_list, 1):
            progress = _make_progress_bar(i, len(dat_list))
            try:
                await msg.edit_text(
                    f"Выгрузка: Регион {reg_code}\n{progress} {i}/{len(dat_list)}\n{dat}..."
                )
            except Exception:
                pass
            try:
                api_response = await fetch_dtp_data(dat=dat, reg=reg_code, pok="1")
                cards = extract_accident_cards(api_response)
                all_cards.extend(cards)
            except Exception as e:
                logger.error(f"{dat}: {e}")

        if all_cards:
            try:
                file1_data = build_file1_data(all_cards)
                file2_data = build_file2_data(all_cards)
                file1_bytes, file2_bytes = generate_both_files(file1_data, file2_data)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                await msg.edit_text("Готово!")
                chat_id = update.effective_chat.id
                await context.bot.send_document(chat_id=chat_id, document=file1_bytes,
                                               filename=f"dtp_cards_{reg_code}_{period.year}_{timestamp}.xlsx",
                                               caption=f"Карточки ДТП\n{period.label}\nДТП: {len(all_cards)}")
                await context.bot.send_document(chat_id=chat_id, document=file2_bytes,
                                               filename=f"dtp_uch_{reg_code}_{period.year}_{timestamp}.xlsx",
                                               caption=f"Участники ДТП\n{period.label}")
                try:
                    await msg.delete()
                except Exception:
                    pass
                await _offer_analysis(context, chat_id, f"Регион {reg_code}", reg_code, period, all_cards)
            except Exception as e:
                logger.exception(f"Ошибка: {e}")
                await msg.edit_text(f"Ошибка генерации файлов: {e}")
        else:
            await msg.edit_text("Не удалось получить данные. Проверьте параметры.")
        return

    # Период без кода региона — ищем регион
    if period and not reg_code:
        regions = await _load_regions_if_needed(context)
        found = find_region(text, regions)
        if found:
            context.user_data["reg_code"] = found["code"]
            context.user_data["reg_name"] = found["name"]
            dat_list = period.get_dat_list()
            msg = await update.message.reply_text(
                f"Регион: {found['name']}\nПериод: {period.label}\nЗапросов: {len(dat_list)}\n\nПодготовка..."
            )
            all_cards = []
            for i, dat in enumerate(dat_list, 1):
                try:
                    await msg.edit_text(f"Выгрузка: {found['name']}\n{_make_progress_bar(i, len(dat_list))} {i}/{len(dat_list)}\n{dat}...")
                except Exception:
                    pass
                try:
                    api_response = await fetch_dtp_data(dat=dat, reg=found["code"], pok="1")
                    cards = extract_accident_cards(api_response)
                    all_cards.extend(cards)
                except Exception as e:
                    logger.error(f"{dat}: {e}")
            if all_cards:
                try:
                    file1_data = build_file1_data(all_cards)
                    file2_data = build_file2_data(all_cards)
                    file1_bytes, file2_bytes = generate_both_files(file1_data, file2_data)
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    await msg.edit_text("Готово!")
                    chat_id = update.effective_chat.id
                    await context.bot.send_document(chat_id=chat_id, document=file1_bytes,
                                                   filename=f"dtp_cards_{found['code']}_{period.year}_{timestamp}.xlsx",
                                                   caption=f"Карточки ДТП\n{found['name']} | {period.label}\nДТП: {len(all_cards)}")
                    await context.bot.send_document(chat_id=chat_id, document=file2_bytes,
                                                   filename=f"dtp_uch_{found['code']}_{period.year}_{timestamp}.xlsx",
                                                   caption=f"Участники ДТП\n{found['name']} | {period.label}")
                    try:
                        await msg.delete()
                    except Exception:
                        pass
                    await _offer_analysis(context, chat_id, found["name"], found["code"], period, all_cards)
                except Exception as e:
                    logger.exception(f"Ошибка: {e}")
                    await msg.edit_text(f"Ошибка: {e}")
            else:
                await msg.edit_text("Не удалось получить данные.")
            return

    # Не распознано
    await update.message.reply_text(
        "Не удалось распознать запрос.\n\n"
        "Примеры:\n"
        "  «Вологодская область за 2025 год»\n"
        "  «2.2024 1101»\n"
        "  /check 55.7558 37.6176\n"
        "  /help — справка"
    )


# ========================
# Main
# ========================

def main() -> None:
    validate_config()

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не задан! Бот не может запуститься.")
        sys.exit(1)

    logger.info("Запуск бота...")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("dtp", cmd_dtp))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("road", cmd_road))
    app.add_handler(CommandHandler("roadhelp", cmd_roadhelp))
    app.add_handler(CommandHandler("regions", cmd_regions))
    app.add_handler(CallbackQueryHandler(on_callback_query))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
