"""bot.output — HTML-вывод и карты.

Содержит:
  • _html_map_menu — меню карты
  • _generate_and_send_dtp_map — генерация и отправка HTML-карты
  • _send_analytics_html — отправка аналитики HTML
  • _send_clusters_html — отправка очагов HTML

Выделено из единого bot.py (Phase 3-2). 100% pure.
"""
from bot._state import *
from bot.infra import (
    _tg_retry,
    _safe_edit,
    _send_long_message,
    _make_progress_bar,
    _sanitize_error,
)
# Примечание: _get_current_cards и _build_menu_keyboard импортируются
# ЛОКАЛЬНО внутри функций (поздний импорт) — чтобы разорвать циклическую
# зависимость bot.output ↔ bot.analysis (analysis импортирует output, и
# наоборот). При module-level импорте Python вернул бы partial-модуль и
# упал с ImportError.

async def _html_map_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Показывает подменю для HTML-карты ДТП."""
    query = update.callback_query

    from bot.analysis import _get_current_cards
    cards = _get_current_cards(context) or []
    if not cards:
        await query.edit_message_text(
            "Данные не найдены. Выполните выгрузку заново."
        )
        return

    await query.edit_message_text(
        "\U0001F5FA <b>HTML-карта ДТП</b>\n\n"
        "Выберите вариант карты:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "\U0001F4CD Только ДТП",
                callback_data="html_map_dtp_only",
            )],
            [InlineKeyboardButton(
                "\U0001F4F7 Загрузить камеры + карта",
                callback_data="html_map_ask_cameras",
            )],
        ]),
    )


async def _generate_and_send_dtp_map(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    cameras: list[dict] | None = None,
) -> None:
    """Генерирует и отправляет HTML-карту ДТП."""
    chat_id = update.effective_chat.id
    reg_name = context.user_data.get("analytics_reg_name", "")
    period = context.user_data.get("analytics_period")
    from bot.analysis import _get_current_cards
    cards = _get_current_cards(context) or []

    if not cards or not period:
        msg = await (update.callback_query.edit_message_text
                     if update.callback_query
                     else update.message.reply_text)(
            "Данные не найдены. Выполните выгрузку заново."
        )
        return

    # Прогресс
    try:
        if update.callback_query:
            msg = await update.callback_query.edit_message_text(
                "\U0001F5FA Генерация HTML-карты..."
            )
        else:
            msg = await update.message.reply_text(
                "\U0001F5FA Генерация HTML-карты..."
            )
    except Exception:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="\U0001F5FA Генерация HTML-карты...",
        )

    try:
        from report_generator import ReportGenerator

        gen = ReportGenerator(
            region_name=reg_name,
            period_label=period.label,
        )
        html_content = gen.generate_dtp_map(cards, cameras=cameras)

        # Сохраняем во временный файл
        import tempfile
        import os as _os

        safe_reg = reg_name.replace(" ", "_")[:30]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"dtp_map_{safe_reg}_{period.label.replace(' ', '_')}_{timestamp}.html"

        tmp_path = _os.path.join(
            tempfile.gettempdir(), filename,
        )
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        size_kb = len(html_content.encode("utf-8")) / 1024

        with open(tmp_path, "rb") as f:
            await context.bot.send_document(
                chat_id=chat_id,
                document=f,
                filename=filename,
                caption=(
                    f"\U0001F5FA Карта ДТП\n"
                    f"{reg_name} | {period.label}\n"
                    f"Точек: {len(cards)}"
                    + (f" | Камер: {len(cameras)}" if cameras else "")
                    + f"\nРазмер: {size_kb:.0f} КБ"
                ),
            )

        # Удаляем сообщение о прогрессе
        try:
            await msg.delete()
        except Exception:
            pass

        # Удаляем временный файл
        try:
            _os.remove(tmp_path)
        except Exception:
            pass

        # Показываем кнопку возврата в меню
        from bot.analysis import _build_menu_keyboard
        menu_text, menu_kb = _build_menu_keyboard(context)
        if menu_text and menu_kb:
            await context.bot.send_message(
                chat_id=chat_id,
                text=menu_text,
                reply_markup=menu_kb,
                parse_mode="HTML",
            )

    except Exception as e:
        logger.error(f"Ошибка генерации HTML-карты: {e}", exc_info=True)
        try:
            await msg.edit_text(
                f"\u26A0\uFE0F Ошибка генерации карты:\n\n{_sanitize_error(e)}",
                parse_mode="HTML",
            )
        except Exception:
            pass


async def _send_analytics_html(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    reg_name: str,
    current_label: str,
    prev_label: str,
    current_cards: list[dict],
    prev_cards: list[dict],
) -> None:
    """Генерирует и отправляет HTML-отчёт с графиками аналитики."""
    import tempfile
    import os as _os
    from report_generator import ReportGenerator

    gen = ReportGenerator(
        region_name=reg_name,
        period_label=f"{current_label} vs {prev_label}",
    )
    html_content = gen.generate_analytics_report(
        current_cards=current_cards,
        prev_cards=prev_cards,
    )

    safe_reg = reg_name.replace(" ", "_")[:30]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"analytics_{safe_reg}_{timestamp}.html"

    tmp_path = _os.path.join(tempfile.gettempdir(), filename)
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    size_kb = len(html_content.encode("utf-8")) / 1024

    with open(tmp_path, "rb") as f:
        await context.bot.send_document(
            chat_id=chat_id,
            document=f,
            filename=filename,
            caption=(
                f"\U0001F4CA Визуализация аналитики\n"
                f"{reg_name} | {current_label} vs {prev_label}\n"
                f"Размер: {size_kb:.0f} КБ"
            ),
        )

    try:
        _os.remove(tmp_path)
    except Exception:
        pass


async def _send_clusters_html(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    reg_name: str,
    current_label: str,
    clusters: list[dict],
    preclusters: list[dict] | None = None,
    cameras: list[dict] | None = None,
) -> None:
    """Генерирует и отправляет HTML-карту очагов."""
    import tempfile
    import os as _os
    from report_generator import ReportGenerator

    gen = ReportGenerator(
        region_name=reg_name,
        period_label=current_label,
    )
    html_content = gen.generate_cluster_map(
        clusters=clusters,
        preclusters=preclusters,
        cameras=cameras,
    )

    safe_reg = reg_name.replace(" ", "_")[:30]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"ochagi_{safe_reg}_{timestamp}.html"

    tmp_path = _os.path.join(tempfile.gettempdir(), filename)
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    size_kb = len(html_content.encode("utf-8")) / 1024

    with open(tmp_path, "rb") as f:
        await context.bot.send_document(
            chat_id=chat_id,
            document=f,
            filename=filename,
            caption=(
                f"\U0001F525 Карта очагов ДТП\n"
                f"{reg_name} | {current_label}\n"
                f"Очагов: {len(clusters)}"
                + (f" | Предочагов: {len(preclusters)}" if preclusters else "")
                + (f" | Камер: {len(cameras)}" if cameras else "")
                + f"\nРазмер: {size_kb:.0f} КБ"
            ),
        )

    try:
        _os.remove(tmp_path)
    except Exception:
        pass




