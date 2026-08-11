"""bot.point_stats — статистика ДТП по точке (геолокация).

Содержит:
  • _start_point_stats — начало сессии статистики по точке
  • _handle_point_stats_radius — выбор радиуса
  • _send_point_stats_excel / _send_point_stats_html — отправка результатов
  • _process_point_stats — основной расчёт
  • _handle_location_message — обработка Location от Telegram

Выделено из единого bot.py (Phase 3-2). 100% pure.
"""
from bot._state import *
from bot.infra import _tg_retry, _safe_edit, _send_long_message, _get_user_lock, _sanitize_error
from bot.access import _fetch_cards_for_period
from bot.analysis import _get_current_cards, _build_menu_keyboard

async def _start_point_stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Начинает режим «Статистика по точке».
    Загружает данные за прошлый год (если ещё нет) и просит координаты.
    """
    chat_id = update.effective_chat.id

    reg_code = context.user_data.get("analytics_reg_code", "")
    reg_name = context.user_data.get("analytics_reg_name", "")
    period = context.user_data.get("analytics_period")
    current_cards = _get_current_cards(context) or []

    if not period or not current_cards:
        await update.callback_query.edit_message_text(
            "Данные не найдены. Пожалуйста, выполните выгрузку заново."
        )
        return

    current_label = period.label
    prev_year = period.year - 1
    prev_label = period.label.replace(str(period.year), str(prev_year))

    # Проверяем, есть ли данные за прошлый год
    prev_cards = context.user_data.get("analytics_prev_cards", [])

    if not prev_cards and reg_code:
        # Загружаем данные за прошлый год
        status_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "\U0001F4CD Статистика по точке: подготовка...\n\n"
                f"Загрузка данных за {prev_label}..."
            ),
        )

        dat_list_prev = [f"{m}.{prev_year}" for m in period.months]

        async def pt_progress(i, total, month_name, year):
            await status_msg.edit_text(
                f"\U0001F4CD Загрузка данных за прошлый год...\n\n"
                f"{i}/{total} — {month_name} {year}"
            )

        prev_cards, _ = await _fetch_cards_for_period(
            dat_list_prev, reg_code, "Точечная статистика",
            progress_callback=pt_progress,
        )

        # Сохраняем для повторного использования
        if prev_cards:
            context.user_data["analytics_prev_cards"] = prev_cards
            context.user_data["analytics_prev_label"] = prev_label

        try:
            await status_msg.delete()
        except Exception:
            pass

    # Входим в режим ожидания координат
    context.user_data["point_stats_mode"] = True

    await update.callback_query.edit_message_text(
        "\U0001F4CD <b>Статистика по точке</b>\n\n"
        "Отправьте координаты одним из способов:\n\n"
        "\U0001F4CD <b>Прикрепить локацию</b> (скрепка \U0001F4CE → Местоположение)\n\n"
        "Или текстом:\n"
        "<code>55.1234, 38.5678</code>\n\n"
        f"Период: {current_label}"
        + (f" | {prev_label}" if prev_cards else ""),
        parse_mode="HTML",
    )


async def _handle_point_stats_radius(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    radius_m: int,
) -> None:
    """Пересчитывает статистику с новым радиусом (координаты те же)."""
    lat = context.user_data.get("point_stats_lat")
    lon = context.user_data.get("point_stats_lon")

    if lat is None or lon is None:
        await update.callback_query.edit_message_text(
            "Координаты потеряны. Попробуйте снова."
        )
        return

    await _process_point_stats(
        context, update.effective_chat.id, lat, lon, radius_m,
        edit_query=update.callback_query,
    )


async def _send_point_stats_excel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Генерирует и отправляет Excel-файл с ДТП в радиусе точки.
    Использует сохранённые координаты и радиус из user_data.
    """
    chat_id = update.effective_chat.id

    lat = context.user_data.get("point_stats_lat")
    lon = context.user_data.get("point_stats_lon")
    radius_m = context.user_data.get("point_stats_radius", 500)

    if lat is None or lon is None:
        await update.callback_query.answer(
            "Координаты не найдены. Отправьте координаты заново.",
            show_alert=True,
        )
        return

    # Подтверждение
    await update.callback_query.answer("Генерирую Excel-файл...")

    current_cards = _get_current_cards(context) or []
    prev_cards = context.user_data.get("analytics_prev_cards", [])
    period = context.user_data.get("analytics_period")
    current_label = period.label if period else "Текущий период"
    prev_label = context.user_data.get("analytics_prev_label", "")

    # Фильтруем карточки по радиусу
    from point_statistics import filter_cards_by_radius

    current_filtered = filter_cards_by_radius(current_cards, lat, lon, radius_m)
    prev_filtered = filter_cards_by_radius(prev_cards, lat, lon, radius_m) if prev_cards else []

    total = len(current_filtered) + len(prev_filtered)
    if total == 0:
        await context.bot.send_message(
            chat_id=chat_id,
            text="\u26A0\uFE0F В указанном радиусе нет ДТП для выгрузки.",
        )
        return

    # Строим данные для Excel
    current_rows, prev_rows = build_point_stats_excel_data(
        current_filtered,
        prev_filtered if prev_filtered else None,
        current_label,
        prev_label,
    )

    column_names = get_point_stats_column_names()

    # Генерируем файл
    excel_bytes = await asyncio.to_thread(
        generate_point_stats_file,
        current_rows=current_rows,
        prev_rows=prev_rows if prev_rows else None,
        column_names=column_names,
        current_label=current_label,
        prev_label=prev_label if prev_filtered else None,
    )

    # Имя файла
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if radius_m >= 1000:
        radius_str = f"{radius_m / 1000:.0f}km"
    else:
        radius_str = f"{radius_m}m"

    filename = f"dtp_point_{radius_str}_{timestamp}.xlsx"

    # Формируем подпись
    if radius_m >= 1000:
        radius_display = f"{radius_m / 1000:.0f} км"
    else:
        radius_display = f"{radius_m} м"

    caption_parts = [
        f"\U0001F4CD ДТП в радиусе {radius_display}",
        f"Координаты: {lat:.5f}, {lon:.5f}",
        f"Период: {current_label}",
    ]
    if prev_filtered:
        caption_parts.append(f"Сравнение: {prev_label}")
    caption_parts.append(f"ДТП: {total} ({len(current_filtered)} + {len(prev_filtered)})")

    await context.bot.send_document(
        chat_id=chat_id,
        document=excel_bytes,
        filename=filename,
        caption="\n".join(caption_parts),
    )


async def _send_point_stats_html(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Генерирует и отправляет HTML-карту по точке."""
    import tempfile
    import os as _os

    chat_id = update.effective_chat.id
    await update.callback_query.answer("Генерирую HTML-карту...")

    lat = context.user_data.get("point_stats_lat")
    lon = context.user_data.get("point_stats_lon")
    radius_m = context.user_data.get("point_stats_radius", 500)

    if lat is None or lon is None:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Координаты потеряны. Отправьте заново.",
        )
        return

    reg_name = context.user_data.get("analytics_reg_name", "")
    current_cards = _get_current_cards(context) or []
    prev_cards = context.user_data.get("analytics_prev_cards", [])
    period = context.user_data.get("analytics_period")
    current_label = period.label if period else "Текущий период"
    prev_label = context.user_data.get("analytics_prev_label", "")
    cameras = context.user_data.get("cameras_data")

    try:
        from report_generator import ReportGenerator

        gen = ReportGenerator(
            region_name=reg_name,
            period_label=current_label,
        )
        html_content = gen.generate_point_stats_map(
            lat=lat,
            lon=lon,
            radius_m=radius_m,
            current_cards=current_cards,
            prev_cards=prev_cards if prev_cards else None,
            cameras=cameras,
            current_label=current_label,
            prev_label=prev_label,
        )

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"point_stats_{radius_m}m_{timestamp}.html"

        tmp_path = _os.path.join(tempfile.gettempdir(), filename)
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        size_kb = len(html_content.encode("utf-8")) / 1024

        radius_str = f"{radius_m} м" if radius_m < 1000 else f"{radius_m/1000:.0f} км"

        with open(tmp_path, "rb") as f:
            await context.bot.send_document(
                chat_id=chat_id,
                document=f,
                filename=filename,
                caption=(
                    f"\U0001F4CD Карта по точке\n"
                    f"Радиус: {radius_str}\n"
                    f"Координаты: {lat:.5f}, {lon:.5f}\n"
                    f"Размер: {size_kb:.0f} КБ"
                ),
            )

        try:
            _os.remove(tmp_path)
        except Exception:
            pass

        # Показываем кнопку возврата в меню
        menu_text, menu_kb = _build_menu_keyboard(context)
        if menu_text and menu_kb:
            await context.bot.send_message(
                chat_id=chat_id,
                text=menu_text,
                reply_markup=menu_kb,
                parse_mode="HTML",
            )

    except Exception as e:
        logger.error(f"Ошибка генерации HTML-карты по точке: {e}", exc_info=True)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"\u26A0\uFE0F Ошибка генерации карты: {_sanitize_error(e)}",
        )


async def _process_point_stats(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    lat: float,
    lon: float,
    radius_m: int,
    edit_query=None,
) -> None:
    """
    Вычисляет и отправляет статистику по точке.

    Args:
        edit_query: Если передан — редактирует сообщение с кнопками.
            Иначе отправляет новое сообщение.
    """
    current_cards = _get_current_cards(context) or []
    prev_cards = context.user_data.get("analytics_prev_cards", [])
    period = context.user_data.get("analytics_period")
    current_label = period.label if period else ""
    prev_label = context.user_data.get("analytics_prev_label", "")

    # Сохраняем координаты и радиус для переключения
    context.user_data["point_stats_lat"] = lat
    context.user_data["point_stats_lon"] = lon
    context.user_data["point_stats_radius"] = radius_m

    # Вычисляем статистику
    stats = calculate_point_statistics(
        lat, lon, radius_m, current_cards,
        prev_cards if prev_cards else None,
    )

    # Форматируем сообщение
    message_text = format_point_stats_message(
        stats, current_label,
        prev_label if prev_cards else None,
    )

    # Кнопки радиуса
    radius_buttons = []
    for r_m, r_label in RADIUS_OPTIONS:
        active = "\u2022 " if r_m == radius_m else ""
        radius_buttons.append(InlineKeyboardButton(
            f"{active}{r_label}",
            callback_data=f"ps_radius:{r_m}",
        ))

    # Кнопка выгрузки в Excel (если есть ДТП)
    total_dtp = stats["current"]["total"]
    prev_total = stats["prev"]["total"] if stats.get("prev") else 0
    buttons = [radius_buttons]

    if total_dtp > 0 or prev_total > 0:
        excel_label = f"\U0001F4E5 Выгрузить в Excel ({total_dtp + prev_total} ДТП)"
        buttons.append([InlineKeyboardButton(
            excel_label,
            callback_data="ps_excel",
        )])
        buttons.append([InlineKeyboardButton(
            "\U0001F5FA HTML-карта",
            callback_data="ps_html_map",
        )])

    # Кнопка возврата в меню
    buttons.append([InlineKeyboardButton(
        "\u21A9\uFE0F В меню",
        callback_data="back_to_menu",
    )])

    keyboard = InlineKeyboardMarkup(buttons)

    # Отправляем или редактируем
    if edit_query:
        try:
            await edit_query.edit_message_text(
                message_text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception:
            # Если сообщение слишком длинное для редактирования — отправляем новое
            await _send_long_message(
                context.bot, chat_id,
                text=message_text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
    else:
        await _send_long_message(
            context.bot, chat_id,
            text=message_text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )


async def _handle_location_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Обрабатывает сообщение с локацией (пinned location)."""
    if not context.user_data.get("point_stats_mode"):
        return

    location = update.message.location
    if not location:
        return

    lat = location.latitude
    lon = location.longitude
    radius_m = context.user_data.get("point_stats_radius", 500)

    await _process_point_stats(
        context, update.effective_chat.id, lat, lon, radius_m,
    )




