"""bot.handlers.messages — обработчики текстовых сообщений и документов.

Содержит:
  • _handle_document — приём Excel-файла с камерами фотовидеофиксации
  • handle_message — основной обработчик текста (NLP-парсинг запроса)

Выделено из единого bot.py (Phase 3-2). 100% pure.
"""
from bot._state import *
from bot.access import is_user_allowed, _get_regions, _load_regions_if_needed, _fetch_cards_for_period
from bot.keyboards import build_region_keyboard, build_period_keyboard
from bot.infra import _tg_retry, _safe_edit, _send_long_message, _get_user_lock, _sanitize_error, _IsDocument
from bot.analysis import (
    _start_fetching, _build_menu_keyboard, _offer_analysis,
    _clear_analytics_data, _run_analysis,
)

async def _handle_document(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Обрабатывает загрузку файла (камеры фотовидеофиксации)."""

    # --- Загрузка камер для HTML-карты ДТП ---
    if context.user_data.get("waiting_camera_for_map"):
        context.user_data.pop("waiting_camera_for_map", None)
        document = update.message.document
        if not document:
            return
        filename = document.file_name or ""
        if not filename.startswith("gibddrf_cameras_change"):
            await update.message.reply_text(
                "\u26A0\uFE0F Неверный файл.\n\n"
                "Ожидается: gibddrf_cameras_change_*.xls"
            )
            return
        wait_msg = await update.message.reply_text(
            "\U0001F4F7 Обработка файла камер..."
        )
        try:
            file = await document.get_file()
            import tempfile
            import os as _os
            from camera_loader import parse_camera_file

            tmp_path = _os.path.join(
                tempfile.gettempdir(), f"cam_{document.file_id}.xls"
            )
            try:
                await file.download_to_drive(custom_path=tmp_path)
                with open(tmp_path, "rb") as f:
                    file_bytes = f.read()
            finally:
                if _os.path.exists(tmp_path):
                    _os.remove(tmp_path)

            cameras = parse_camera_file(file_bytes)
            if not cameras:
                await wait_msg.edit_text(
                    "\u26A0\uFE0F В файле не найдено камер."
                )
                return

            await wait_msg.edit_text(
                f"\u2705 Загружено {len(cameras)} камер. "
                f"Генерирую карту..."
            )
            await _generate_and_send_dtp_map(
                update, context, cameras=cameras,
            )
        except Exception as e:
            logger.error(f"Ошибка загрузки камер для карты: {e}")
            await wait_msg.edit_text(f"\u26A0\uFE0F Ошибка: {_sanitize_error(e)}")
        return

    if not context.user_data.get("waiting_camera_file"):
        # Не ожидаем файл — уведомляем пользователя
        logger.info("Получен документ, но бот не ожидает файл камер")
        try:
            await update.message.reply_text(
                "\u26A0\uFE0F Бот не ожидает файл.\n\n"
                "Чтобы загрузить камеры фотовидеофиксации:\n"
                "1. Выберите регион (/dtp)\n"
                "2. Выгрузите данные за период\n"
                "3. Нажмите кнопку \u2B50 Очаги ДТП\n"
                "4. Затем отправьте файл камер"
            )
        except Exception:
            pass
        return

    context.user_data.pop("waiting_camera_file", None)

    document = update.message.document
    if not document:
        return

    # Проверяем имя файла (.xls или .xlsx)
    filename = document.file_name or ""
    if not filename.startswith("gibddrf_cameras_change"):
        await update.message.reply_text(
            "\u26A0\uFE0F Неверный файл.\n\n"
            "Ожидается файл: gibddrf_cameras_change_*.xls"
        )
        return

    wait_msg = await update.message.reply_text(
        "\U0001F4F7 Обработка файла камер..."
    )

    try:
        file = await document.get_file()

        # Скачиваем файл
        import io
        import tempfile
        import os
        from camera_loader import parse_camera_file

        # Используем временный файл — самый надёжный способ
        tmp_path = os.path.join(tempfile.gettempdir(), f"cam_{document.file_id}.xls")
        try:
            await file.download_to_drive(custom_path=tmp_path)
            with open(tmp_path, "rb") as f:
                file_bytes = f.read()
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

        # Диагностика: логируем сигнатуру до вызова парсера
        sig_hex = file_bytes[:8].hex() if len(file_bytes) >= 8 else "<too short>"
        logger.info(
            f"Загружен файл: {document.file_name}, "
            f"{len(file_bytes)} байт, сигнатура: {sig_hex}"
        )

        cameras = parse_camera_file(file_bytes)

        if not cameras:
            await wait_msg.edit_text(
                "\u26A0\uFE0F В файле не найдено камер.\n"
                "Проверьте формат файла."
            )
            return

        # Сохраняем в сессию
        context.user_data["cameras_data"] = cameras

        # Сохраняем файл на диск (кэш по региону)
        # reg_code мог быть удалён после выгрузки ДТП, проверяем все источники
        reg_code = (
            context.user_data.get("concentration_reg_code", "")
            or context.user_data.get("reg_code", "")
            or context.user_data.get("analytics_reg_code", "")
        )
        if reg_code and file_bytes:
            try:
                from camera_cache import save_camera_file
                path = save_camera_file(reg_code, file_bytes)
                logger.info(f"Камеры сохранены в кэш: {path}")
                save_ok = True
            except Exception as save_err:
                logger.error(f"Ошибка сохранения камер в кэш: {save_err}", exc_info=True)
                save_ok = False
        else:
            logger.warning(
                f"Кэширование камер пропущено: "
                f"reg_code={reg_code!r}, file_bytes={len(file_bytes) if file_bytes else 0}, "
                f"user_data keys={list(context.user_data.keys())}"
            )
            save_ok = False

        with_pk = sum(1 for c in cameras if c["has_piket"])
        without_pk = len(cameras) - with_pk

        save_line = ""
        if reg_code and save_ok:
            save_line = f"  \u2022 Файл сохранён для региона {reg_code}\n"
        elif reg_code and not save_ok:
            save_line = f"  \u26A0\uFE0F Не удалось сохранить файл на сервере\n"

        await wait_msg.edit_text(
            f"\u2705 Загружено <b>{len(cameras)}</b> камер:\n"
            f"  \u2022 С пикетажем: {with_pk}\n"
            f"  \u2022 Городских: {without_pk}\n"
            f"{save_line}\n"
            f"Запускаю расчёт очагов...",
            parse_mode="HTML",
        )

        # Запускаем расчёт очагов
        await _run_concentration_points(update, context)

    except Exception as e:
        logger.error(f"Ошибка обработки файла камер: {e}")
        await wait_msg.edit_text(
            f"\u26A0\uFE0F Ошибка обработки файла:\n\n{_sanitize_error(e)}\n\n"
            f"Попробуйте ещё раз или нажмите 'Без камер'.",
            parse_mode="HTML",
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обрабатывает текстовые сообщения:
      - Пытается распознать запрос на естественном языке
      - Если распознал — начинает выгрузку
      - Если нет — предлагает помощь
    """
    user = update.effective_user
    user_text = update.message.text.strip()

    if not user_text:
        return

    # --- Режим статистики по точке: проверяем координаты ---
    if context.user_data.get("point_stats_mode"):
        coords = parse_coordinates(user_text)
        if coords is not None:
            lat, lon = coords
            radius_m = context.user_data.get("point_stats_radius", 500)
            try:
                await update.message.delete()
            except Exception:
                pass
            await _process_point_stats(
                context, update.effective_chat.id, lat, lon, radius_m,
            )
            return
        # Если координаты не распознаны — подсказываем, НЕ выходим из режима
        await update.message.reply_text(
            "\u26A0\uFE0F Не удалось распознать координаты.\n\n"
            "Отправьте координаты в формате:\n"
            "  59.1234, 39.5678\n\n"
            "Или нажмите \u21A9\uFE0F \u00abВ меню\u00bb."
        )
        return

    if not is_user_allowed(user.id):
        await update.message.reply_text("У вас нет доступа к этому боту.")
        return

    logger.info(f"Сообщение от user_id={user.id}: {user_text}")

    # Пытаемся распознать запрос
    parsed = await parse_user_message(user_text)

    if parsed is not None:
        # Полностью распознано — начинаем выгрузку
        reg_code = parsed.region_code
        reg_name = parsed.region_name
        period = parsed.period

        # Очищаем контекст аналитики от предыдущего запроса
        _clear_analytics_data(context.user_data)

        logger.info(
            f"Распознан запрос: регион={reg_name} ({reg_code}), "
            f"период={period.label}"
        )

        # Сохраняем в user_data для _start_fetching
        context.user_data["reg_code"] = reg_code
        context.user_data["reg_name"] = reg_name

        # Создаём сообщение и вызываем выгрузку
        processing_msg = await update.message.reply_text(
            f"Распознан запрос:\n\n"
            f"Регион: {reg_name}\n"
            f"Период: {period.label}\n\n"
            f"Начинаю выгрузку..."
        )

        # Создаём фейковый callback-объект для _start_fetching
        class FakeQuery:
            def __init__(self, message, bot):
                self.message = message
                self._bot = bot

            async def edit_message_text(self, text, reply_markup=None):
                try:
                    await self._bot.edit_message_text(
                        chat_id=self.message.chat_id,
                        message_id=self.message.message_id,
                        text=text,
                        reply_markup=reply_markup,
                    )
                except Exception:
                    pass

        fake_query = FakeQuery(processing_msg, context.bot)
        await _start_fetching(fake_query, context, period)
        return

    # --- Режим вопрос-ответ по данным аналитики ---
    # Проверяем ДО частичного парсинга региона, иначе find_region() может
    # ложно сработать на словах вопроса (порог совпадения всего 30 баллов).
    if context.user_data.get("qa_mode") and is_any_llm_available():
        comparison = context.user_data.get("analytics_comparison")
        reg_name = context.user_data.get("analytics_reg_name", "")
        current_label = context.user_data.get("analytics_current_label", "")
        prev_label = context.user_data.get("analytics_prev_label", "")
        qa_provider = context.user_data.get("qa_llm_provider", "free")

        if comparison:
            await _handle_analytics_question(
                update, context, user_text,
                comparison, reg_name, current_label, prev_label,
                llm_provider=qa_provider,
            )
            return

    # Не удалось распознать полностью — пробуем частичный парсинг
    regions = await _load_regions_if_needed(context)

    # Попробуем найти хотя бы регион
    region = find_region(user_text, regions) if regions else None
    period = parse_period(user_text)

    if region is not None and period is None:
        # Регион найден, но период — нет → показываем выбор периода
        reg_code, reg_name = region
        context.user_data["reg_code"] = reg_code
        context.user_data["reg_name"] = reg_name
        context.user_data["sel_year"] = datetime.now().year

        keyboard = build_period_keyboard(datetime.now().year)
        await update.message.reply_text(
            f"Регион распознан: {reg_name}\n\n"
            f"Теперь выберите период:",
            reply_markup=keyboard,
        )
        return

    if region is None and period is not None:
        # Период найден, но регион — нет
        # Очищаем контекст аналитики при новой выгрузке
        _clear_analytics_data(context.user_data)

        await update.message.reply_text(
            f"Период распознан: {period.label}\n\n"
            f"Но не удалось определить регион.\n"
            f"Укажите название региона или его код.\n\n"
            f"Или используйте /dtp для выбора через кнопки."
        )
        return

    # Ничего не распознано — подсказка
    await update.message.reply_text(
        "Не удалось распознать запрос.\n\n"
        "Попробуйте один из вариантов:\n\n"
        "1. Текстом:\n"
        "   Вологодская область за 2025 год\n"
        "   Алтайский край за март 2025\n"
        "   за I квартал 2025 Москва\n\n"
        "2. Строгий формат:\n"
        "   2.2024 1101\n\n"
        "3. Через кнопки:\n"
        "   /dtp\n\n"
        "Справка: /help\n"
        "Список регионов: /regions"
    )




