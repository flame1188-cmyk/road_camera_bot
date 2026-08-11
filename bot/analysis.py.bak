"""bot.analysis — конвейер аналитики и очагов ДТП.

Содержит:
  • _start_fetching — мультизапрос с прогрессом
  • _get_current_cards / _get_prev_cards / _has_analytics_data / _get_card_count
  • _build_menu_keyboard — меню действий после выгрузки
  • _preload_prev_year — предзагрузка предыдущего периода
  • _offer_analysis — предложение анализа
  • _run_analysis — основной цикл анализа
  • _clear_analytics_data — очистка состояния
  • _run_concentration_points — расчёт очагов

Самый большой модуль (~1300 строк). В будущем можно разбить на
analysis/pipeline.py + analysis/clusters.py + analysis/menu.py.

Выделено из единого bot.py (Phase 3-2). 100% pure.
"""
from bot._state import *
from bot.infra import _tg_retry, _safe_edit, _send_long_message, _get_user_lock, _sanitize_error, _make_progress_bar, _log_memory
from bot.access import _fetch_cards_for_period
from bot.keyboards import build_region_keyboard, build_period_keyboard
from bot.output import _send_analytics_html, _send_clusters_html, _generate_and_send_dtp_map, _html_map_menu

async def _start_fetching(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    period: ParsedPeriod,
) -> None:
    """
    Начинает выгрузку данных для выбранного региона и периода.
    Использует _fetch_cards_for_period, который при 5xx
    автоматически переключается на запасной метод через сайт ГИБДД.
    """
    reg_code = context.user_data.get("reg_code", "")
    reg_name = context.user_data.get("reg_name", "Регион " + reg_code)
    dat_list = period.get_dat_list()
    total_months = len(dat_list)

    # Сбрасываем флаг недоступности API при новом запросе пользователя
    global _api_down
    _api_down = False

    # Логируем память перед выгрузкой (для диагностики OOM)
    _log_memory(f"Выгрузка СТАРТ: {reg_name}, {period.label}")

    try:
        await _tg_retry(lambda: query.edit_message_text(
            f"Выгрузка данных:\n\n"
            f"Регион: {reg_name}\n"
            f"Период: {period.label}\n"
            f"Запросов: {total_months}\n\n"
            f"Подготовка..."
        ), "edit_message_text (старт выгрузки)")
    except (TimedOut, NetworkError):
        logger.warning("Не удалось отправить стартовое сообщение выгрузки")

    # Прогресс-колбэк для обновления сообщения в Telegram
    async def _progress(i: int, total: int, month_name: str, year: str):
        progress_bar = _make_progress_bar(i, total)
        status_text = (
            f"Выгрузка данных:\n\n"
            f"Регион: {reg_name}\n"
            f"Период: {period.label}\n\n"
            f"{progress_bar} {i}/{total}\n"
            f"Запрос: {month_name} {year}..."
        )
        try:
            await query.edit_message_text(status_text)
        except Exception:
            pass  # Не критично

    # Уведомление о переключении на запасной метод
    async def _notify(text: str):
        try:
            await query.edit_message_text(
                f"Выгрузка данных:\n\n"
                f"Регион: {reg_name}\n"
                f"Период: {period.label}\n\n"
                f"{text}"
            )
        except Exception:
            pass

    # Загружаем данные (с автоматическим web-fallback при 5xx)
    all_cards, errors = await _fetch_cards_for_period(
        dat_list, reg_code,
        log_prefix="Выгрузка",
        progress_callback=_progress,
        notify_callback=_notify,
    )

    # Проверяем результат
    if not all_cards and errors:
        error_text = "\n".join(f"- {e}" for e in errors)
        try:
            await _tg_retry(lambda: query.edit_message_text(
                f"Не удалось получить данные.\n\nОшибки:\n{error_text}\n\n"
                f"Попробуйте позже или измените параметры."
            ), "edit_message_text (ошибки выгрузки)")
        except (TimedOut, NetworkError):
            logger.warning("Не удалось отправить сообщение об ошибках выгрузки")
        return

    # Логируем память после загрузки всех карточек
    _log_memory(
        f"Выгрузка ЗАГРУЖЕНО: {reg_name}, {len(all_cards)} ДТП, "
        f"кэш: {data_cache.stats()}"
    )

    # Обработка и генерация Excel
    try:
        await _tg_retry(lambda: query.edit_message_text(
            f"Выгрузка данных:\n\n"
            f"Регион: {reg_name}\n"
            f"Период: {period.label}\n\n"
            f"Найдено ДТП: {len(all_cards)}\n"
            f"Генерация Excel-файлов..."
        ), "edit_message_text (статус генерации)")

        # Синхронные CPU-bound операции — выполняем в пуле потоков,
        # чтобы не блокировать event loop бота (при 3000+ ДТП
        # parse + generate может занимать 60-120 секунд)
        file1_data, file2_data = await asyncio.to_thread(
            lambda: (build_file1_data(all_cards), build_file2_data(all_cards))
        )
        participants_count = len(file2_data)
        file1_bytes, file2_bytes = await asyncio.to_thread(
            generate_both_files, file1_data, file2_data
        )

        # Отправляем файлы
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_reg = reg_name.replace(" ", "_")[:30]
        filename1 = f"dtp_cards_{safe_reg}_{period.year}_{timestamp}.xlsx"
        filename2 = f"dtp_uch_{safe_reg}_{period.year}_{timestamp}.xlsx"

        # Статусное сообщение — не критично, не должно прерывать отправку файлов
        try:
            await _tg_retry(lambda: query.edit_message_text("Готово! Отправляю файлы..."),
                                     "edit_message_text (готово)")
        except (TimedOut, NetworkError):
            logger.warning("Не удалось обновить статус 'Готово' — отправляю файлы как есть")

        chat_id = query.message.chat_id

        from telegram import Bot
        bot: Bot = context.bot

        await _tg_retry(lambda: bot.send_document(
            chat_id=chat_id,
            document=file1_bytes,
            filename=filename1,
            caption=(
                f"Карточки ДТП\n"
                f"{reg_name} | {period.label}\n"
                f"ДТП: {len(all_cards)}"
            ),
        ), "send_document (карточки ДТП)")

        await _tg_retry(lambda: bot.send_document(
            chat_id=chat_id,
            document=file2_bytes,
            filename=filename2,
            caption=(
                f"Участники ДТП\n"
                f"{reg_name} | {period.label}\n"
                f"Участников: {participants_count}"
            ),
        ), "send_document (участники ДТП)")

        # Удаляем сообщение о статусе
        try:
            await _tg_retry(lambda: query.message.delete(), "delete message")
        except Exception:
            pass

        logger.info(f"Файлы отправлены: {len(all_cards)} ДТП, {participants_count} участников")

        # Освобождаем байты файлов (уже отправлены)
        del file1_bytes, file2_bytes

        # Предлагаем провести анализ
        await _offer_analysis(context, chat_id, reg_name, reg_code, period, all_cards)

        # all_cards уже сохранены в data_cache и user_data["analytics_cards"],
        # локальная ссылка выйдет из области видимости сама

    except Exception as e:
        logger.exception(f"Ошибка генерации/отправки файлов: {e}")
        user_msg = _sanitize_error(e)
        try:
            await _tg_retry(lambda: query.edit_message_text(
                f"\u26A0\uFE0F Ошибка при генерации файлов: {user_msg}"
            ), "edit_message_text (ошибка)")
        except (TimedOut, NetworkError):
            logger.warning("Не удалось отправить сообщение об ошибке пользователю")
        except Exception:
            pass

    finally:
        # НЕ очищаем user_data полностью, потому что _offer_analysis
        # сохранил данные аналитики (analytics_reg_code, analytics_cards и т.д.)
        # Удаляем только данные выгрузки, оставляем данные аналитики
        for key in ["reg_code", "reg_name", "sel_year"]:
            context.user_data.pop(key, None)


def _get_current_cards(
    context: ContextTypes.DEFAULT_TYPE,
) -> list[dict] | None:
    """
    Получает карточки ДТП текущего периода.
    Сначала проверяет user_data, потом data_cache.
    Возвращает None если данные не найдены.
    """
    cards = context.user_data.get("analytics_cards", [])
    if cards:
        return cards

    reg_code = context.user_data.get("analytics_reg_code", "")
    period = context.user_data.get("analytics_period")
    if not reg_code or not period:
        return None

    dat_list = [f"{m}.{period.year}" for m in period.months]
    cached = data_cache.get(reg_code, dat_list)
    if cached:
        return cached[0]
    return None


def _has_analytics_data(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Проверяет, есть ли данные для аналитики.
    Используется для построения меню.
    """
    period = context.user_data.get("analytics_period")
    reg_name = context.user_data.get("analytics_reg_name", "")
    return bool(period and reg_name)


def _get_card_count(context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Возвращает количество ДТП текущего периода.
    """
    cards = _get_current_cards(context)
    if cards:
        return len(cards)
    return 0


def _get_prev_cards(
    context: ContextTypes.DEFAULT_TYPE,
) -> list[dict] | None:
    """
    Получает карточки ДТП за прошлый период.
    Сначала проверяет user_data, потом data_cache.
    Возвращает None если данные не найдены.
    """
    prev_cards = context.user_data.get("analytics_prev_cards", [])
    if prev_cards:
        return prev_cards

    reg_code = context.user_data.get("analytics_reg_code", "")
    period = context.user_data.get("analytics_period")
    if not reg_code or not period:
        return None

    prev_year = period.year - 1
    dat_list_prev = [f"{m}.{prev_year}" for m in period.months]
    cached = data_cache.get(reg_code, dat_list_prev)
    if cached:
        return cached[0]
    return None


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


async def _preload_prev_year(
    reg_code: str,
    period: ParsedPeriod,
) -> None:
    """
    Фоновая задача: загружает данные за аналогичный период прошлого года
    в глобальный кэш. Вызывается через asyncio.create_task() после
    успешной выгрузки текущего периода — пользователь уже видит меню
    и может работать, а данные за прошлый год подгружаются незаметно.

    Ошибки логируются, но не влияют на работу бота.
    """
    prev_year = period.year - 1
    dat_list_prev = [f"{m}.{prev_year}" for m in period.months]

    # Не скачиваем, если уже в кэше (БД или in-memory)
    if await data_cache_has_async(reg_code, dat_list_prev):
        logger.info(
            f"Preload: данные за прошлый год уже в кэше "
            f"(reg={reg_code}, {len(dat_list_prev)} мес)"
        )
        return

    logger.info(
        f"Preload: старт фоновой загрузки за прошлый год "
        f"(reg={reg_code}, {len(dat_list_prev)} мес)"
    )
    try:
        cards, errors = await _fetch_cards_for_period(
            dat_list_prev, reg_code, "Preload",
        )
        if cards:
            logger.info(
                f"Preload: загружено {len(cards)} ДТП за прошлый год "
                f"[{data_cache.stats()}]"
            )
        elif errors:
            logger.warning(f"Preload: ошибки при загрузке: {errors}")
        else:
            logger.info("Preload: нет данных за прошлый год (пустой ответ)")
    except Exception as e:
        logger.error(f"Preload: ошибка фоновой загрузки: {e}", exc_info=True)


async def _offer_analysis(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    reg_name: str,
    reg_code: str,
    period: ParsedPeriod,
    current_cards: list[dict],
) -> None:
    """
    После выгрузки предлагает кнопки для проведения анализа
    (сравнение с аналогичным периодом прошлого года).
    Два режима: без ИИ и с ИИ (нейросеть GLM).

    Также запускает фоновую preload-задачу для данных за прошлый год.
    """
    # Сохраняем данные для аналитики в user_data
    context.user_data["analytics_ready"] = True
    context.user_data["analytics_reg_code"] = reg_code
    context.user_data["analytics_reg_name"] = reg_name
    context.user_data["analytics_period"] = period
    context.user_data["analytics_cards"] = current_cards

    # --- Фоновый preload данных за прошлый год ---
    preload_task = asyncio.create_task(_preload_prev_year(reg_code, period))
    context.user_data["_preload_task"] = preload_task

    text, keyboard = _build_menu_keyboard(context)
    if text and keyboard:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )


async def _run_analysis(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    use_llm: bool = False,
    llm_provider: str = "free",
) -> None:
    """
    Выполняет сравнительный анализ текущего периода с прошлым годом.

    Args:
        use_llm: Если True — после расчёта метрик запрашивает резюме у LLM
        llm_provider: "free" (ZhipuAI/GLM) или "paid" (OpenAI-совместимый)
    """
    chat_id = update.effective_chat.id

    reg_code = context.user_data.get("analytics_reg_code", "")
    reg_name = context.user_data.get("analytics_reg_name", "")
    period = context.user_data.get("analytics_period")
    current_cards = _get_current_cards(context)

    if not reg_code or not period or not current_cards:
        await update.callback_query.edit_message_text(
            "Данные для анализа не найдены. Пожалуйста, выполните выгрузку заново."
        )
        return

    # Период прошлого года
    prev_year = period.year - 1
    dat_list_prev = [f"{m}.{prev_year}" for m in period.months]
    prev_label = period.label.replace(str(period.year), str(prev_year))
    current_label = period.label

    # Сохраняем количество ДТП
    current_cards_count = len(current_cards)

    mode_label = "\U0001F916 AI-анализ" if use_llm else "\U0001F4CA Анализ"

    # Немедленно отвечаем пользователю, чтобы он видел реакцию на кнопку
    try:
        await update.callback_query.answer()
    except Exception:
        pass

    # Проверяем, есть ли уже данные за прошлый год (per-user или глобальный кэш)
    preload_task = context.user_data.get("_preload_task")
    if preload_task and not preload_task.done():
        # Фоновая загрузка ещё идёт — показываем пользователю статус
        status_msg = await _tg_retry(lambda: context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"{mode_label}: подготовка...\n\n"
                f"Регион: {reg_name}\n"
                f"Текущий период: {current_label}\n"
                f"Сравнение: {prev_label}\n\n"
                f"\u23F3 Ждём завершения фоновой загрузки данных за прошлый год..."
            ),
        ), "send_message (статус аналитики)")
        logger.info(f"{mode_label}: ждём завершения фоновой загрузки за прошлый год...")
        try:
            await asyncio.wait_for(preload_task, timeout=300)
        except asyncio.TimeoutError:
            logger.warning(f"{mode_label}: preload не завершился за 5 мин, скачиваем самостоятельно")
        except Exception:
            pass  # preload упал — продолжим самостоятельно
    else:
        status_msg = None

    cached_prev = context.user_data.get("analytics_prev_cards", [])
    cached_prev_label = context.user_data.get("analytics_prev_label", "")

    # Также проверяем глобальный кэш (БД + in-memory; может быть заполнен preload-задачей)
    if (not cached_prev or cached_prev_label != prev_label):
        global_cached = await data_cache_get_async(reg_code, dat_list_prev)
        if global_cached is not None:
            cached_prev, _ = global_cached
            cached_prev_label = prev_label

    if cached_prev and cached_prev_label == prev_label:
        # Данные за прошлый год уже есть — не скачиваем повторно
        prev_cards = cached_prev
        errors = []
        context.user_data["analytics_prev_cards"] = prev_cards
        if status_msg is None:
            status_msg = await _tg_retry(lambda: context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"{mode_label}: подготовка...\n\n"
                    f"Регион: {reg_name}\n"
                    f"Текущий период: {current_label}\n"
                    f"Сравнение: {prev_label}\n\n"
                    f"Данные за прошлый год из кэша ({len(prev_cards)} ДТП)..."
                ),
            ), "send_message (статус аналитики)")
        else:
            await status_msg.edit_text(
                f"{mode_label}: подготовка...\n\n"
                f"Регион: {reg_name}\n"
                f"Текущий период: {current_label}\n"
                f"Сравнение: {prev_label}\n\n"
                f"Данные за прошлый год из кэша ({len(prev_cards)} ДТП)..."
            )
    else:
        # Скачиваем данные за прошлый год
        if status_msg is None:
            status_msg = await _tg_retry(lambda: context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"{mode_label}: подготовка...\n\n"
                    f"Регион: {reg_name}\n"
                    f"Текущий период: {current_label}\n"
                    f"Сравнение: {prev_label}\n\n"
                    f"Загрузка данных за {prev_year} год..."
                ),
            ), "send_message (статус аналитики)")
        else:
            await status_msg.edit_text(
                f"{mode_label}: загрузка...\n\n"
                f"Регион: {reg_name}\n"
                f"Текущий период: {current_label}\n"
                f"Сравнение: {prev_label}\n\n"
                f"\u23F3 Загрузка данных за {prev_year} год..."
            )

        async def progress(i, total, month_name, year):
            bar = _make_progress_bar(i, total)
            try:
                await status_msg.edit_text(
                    f"{mode_label}: загрузка...\n\n"
                    f"{bar} {i}/{total}\n"
                    f"Запрос: {month_name} {year}..."
                )
            except Exception:
                pass

        prev_cards, errors = await _fetch_cards_for_period(
            dat_list_prev, reg_code, "Аналитика", progress_callback=progress,
        )

        # Сохраняем количество ДТП за прошлый год
        prev_cards_count = len(prev_cards)

        # Кэшируем для повторного использования
        if prev_cards:
            context.user_data["analytics_prev_cards"] = prev_cards
            context.user_data["analytics_prev_label"] = prev_label

    # Сохраняем количество ДТП за прошлый год (для обоих веток)
    prev_cards_count = len(prev_cards)

    if not prev_cards:
        error_text = "\n".join(f"- {e}" for e in errors) if errors else "Нет данных"
        await status_msg.edit_text(
            f"\u26A0\uFE0F Не удалось загрузить данные за {prev_label}.\n\n"
            f"Ошибки:\n{error_text}\n\n"
            f"Возможно, данные за этот период ещё не опубликованы."
        )
        return

    # Считаем метрики
    await status_msg.edit_text(f"{mode_label}: считаю метрики...")

    current_metrics = calculate_metrics(current_cards)
    previous_metrics = calculate_metrics(prev_cards)
    comparison = compare_metrics(current_metrics, previous_metrics)

    # Сохраняем comparison для возможных вопросов
    context.user_data["analytics_comparison"] = comparison
    context.user_data["analytics_current_label"] = current_label
    context.user_data["analytics_prev_label"] = prev_label
    context.user_data["analytics_prev_cards"] = prev_cards

    # --- Генерируем контент ---
    llm_summary_text = None

    # Проверяем, что запрошенный провайдер доступен
    llm_available = (
        (llm_provider == "free" and LLM_API_KEY)
        or (llm_provider == "paid" and is_paid_llm_available())
    )

    if use_llm and llm_available:
        try:
            provider_label = "DeepSeek (полный)" if llm_provider == "paid" else "GLM (бесплатный)"
            await status_msg.edit_text(
                f"{mode_label}: собираю данные для {provider_label}..."
            )

            # Формируем дополнение из сырых карточек
            raw_sup = extract_raw_supplement(current_cards, current_label, max_cards=50)
            raw_sup += extract_raw_supplement(prev_cards, prev_label, max_cards=50)

            # Ищем новости из открытых источников (если включено)
            news_ctx = ""
            if ENABLE_NEWS_SEARCH:
                news_ctx = await fetch_news_context(reg_name, current_label, prev_label)
            # Сохраняем для вопросов
            context.user_data["analytics_news_context"] = news_ctx

            # Рассчитываем очаги ДТП для передачи в LLM
            clusters_ctx = ""
            existing_clusters = context.user_data.get("analytics_clusters")
            if existing_clusters:
                # Очаги уже рассчитаны (кнопка «Очаги ДТП»),
                # используем готовый результат без пересчёта —
                # пересчёт требует OSM-полигонов и всех карточек в памяти,
                # что может вызвать OOM (5992+6155 ДТП + 1443 полигона).
                clusters_ctx = format_clusters_for_prompt(
                    existing_clusters, max_clusters=10
                )
                logger.info(
                    f"LLM-анализ: очаги взяты из кэша "
                    f"({len(existing_clusters)} очагов, {len(clusters_ctx)} симв.)"
                )
            else:
                try:
                    await status_msg.edit_text(
                        f"{mode_label}: рассчитываю очаги ДТП..."
                    )
                    # Используем полигоны из кэша (если есть от «Очаги ДТП»)
                    existing_polygons = context.user_data.get("_settlement_polygons")
                    # Код региона — для проверки регион-уровневого OSM-кэша
                    _llm_reg_code = (
                        context.user_data.get("reg_code", "")
                        or context.user_data.get("analytics_reg_code", "")
                        or context.user_data.get("concentration_reg_code", "")
                    )
                    clusters, _preclusters, calc_polygons = await calculate_concentration_points(
                        current_cards,
                        settlement_polygons=existing_polygons,
                        reg_code=_llm_reg_code or None,
                    )
                    if clusters:
                        clusters_ctx = format_clusters_for_prompt(clusters, max_clusters=10)
                        context.user_data["analytics_clusters"] = clusters
                        # Сохраняем полигоны для переиспользования
                        # (например, если пользователь потом нажмёт «Очаги ДТП")
                        if calc_polygons:
                            context.user_data["_settlement_polygons"] = calc_polygons
                            logger.info(
                                f"LLM-анализ: полигоны сохранены в кэш "
                                f"({len(calc_polygons)} полигонов)"
                            )
                        logger.info(
                            f"LLM-анализ: рассчитано {len(clusters)} очагов "
                            f"для контекста ({len(clusters_ctx)} симв.)"
                        )
                    else:
                        context.user_data["analytics_clusters"] = []
                except Exception as e:
                    logger.warning(f"Не удалось рассчитать очаги для LLM-контекста: {e}")
                    context.user_data["analytics_clusters"] = []

            await status_msg.edit_text(
                f"{mode_label}: нейросеть анализирует данные...\n"
                f"⏳ Обычно занимает 15-30 секунд."
            )

            # Кросс-таблицы для бесплатного метода (GLM)
            cross_tables_ctx = ""
            if llm_provider == "free" and current_cards:
                try:
                    from analytics import (
                        calculate_cross_tables, calculate_statistical_metrics,
                    )
                    current_cross = calculate_cross_tables(current_cards)
                    prev_cross = None
                    if prev_cards:
                        prev_cross = calculate_cross_tables(prev_cards)
                    from llm_analyzer import (
                        format_cross_tables_for_prompt,
                        format_statistical_metrics_for_prompt,
                    )
                    cross_tables_ctx = format_cross_tables_for_prompt(
                        current_cross, prev_cross, current_label, prev_label,
                    )
                    # Этап 2: статистические метрики (severity rates, Z-score, χ²)
                    stats = calculate_statistical_metrics(current_cross)
                    stats_text = format_statistical_metrics_for_prompt(stats)
                    if stats_text and not stats_text.endswith("(недостаточно данных для статистического анализа)"):
                        cross_tables_ctx += "\n\n" + stats_text
                    logger.info(
                        f"Кросс-таблицы для GLM: {len(cross_tables_ctx)} символов"
                    )
                except Exception as e:
                    logger.warning(f"Не удалось построить кросс-таблицы: {e}")

            llm_summary_text = await get_ai_summary(
                comparison=comparison,
                reg_name=reg_name,
                current_label=current_label,
                prev_label=prev_label,
                raw_supplement=raw_sup,
                news_context=news_ctx,
                clusters_context=clusters_ctx,
                cross_tables_context=cross_tables_ctx,
                provider=llm_provider,
                current_cards=current_cards if llm_provider == "paid" else None,
                prev_cards=prev_cards if llm_provider == "paid" else None,
            )
        except Exception as e:
            logger.error(f"Ошибка LLM: {e}")
            llm_summary_text = None
            await status_msg.edit_text(
                f"\u26A0\uFE0F Не удалось получить ответ от нейросети.\n\n"
                f"Ошибка: {_sanitize_error(e)}\n\n"
                f"Отправляю математический анализ без ИИ.\n"
                f"Попробуйте нажать кнопку ещё раз — обычно работает со 2-й попытки."
            )
            # Не удаляем status_msg — пользователь должен увидеть ошибку

    # Генерируем Excel
    analytics_data = build_analytics_excel_data(
        comparison=comparison,
        reg_name=reg_name,
        current_label=current_label,
        previous_label=prev_label,
    )
    column_names = get_analytics_column_names(current_label, prev_label)
    analytics_bytes = await asyncio.to_thread(
        generate_analytics_file, analytics_data, column_names
    )

    # Удаляем сообщение о статусе (если не было ошибки LLM)
    if use_llm and not llm_summary_text:
        # status_msg уже содержит сообщение об ошибке LLM — не удаляем
        pass
    else:
        try:
            await status_msg.delete()
        except Exception:
            pass

    # Отправляем результаты
    if use_llm and llm_summary_text:
        # Экранируем спецсимволы HTML в LLM-ответе, чтобы теги от модели
        # (например <i>, <b>) не ломали Telegram HTML-парсер
        safe_llm = html_mod.escape(llm_summary_text)
        # Режим с ИИ: сначала LLM-резюме, потом таблица + Excel
        # НЕ оборачиваем в <i> — _send_long_message разбивает текст по \n\n,
        # что разорвёт тег <i>...</i> между частями и вызовет BadRequest.
        await _send_long_message(
            context.bot, chat_id,
            text=(
                f"\U0001F916 <b>Аналитика ИИ: {reg_name}</b>\n"
                f"{current_label} vs {prev_label}\n\n"
                f"{safe_llm}"
            ),
            parse_mode="HTML",
        )
        # Также отправляем математический анализ
        analytics_text = build_analytics_message(
            comparison=comparison,
            reg_name=reg_name,
            current_label=current_label,
            previous_label=prev_label,
        )
        # Отправляем как отдельное сообщение (математика)
        await _send_long_message(
            context.bot, chat_id,
            text=f"\U0001F4CA <b>Детальные данные:</b>\n\n{analytics_text}",
            parse_mode="HTML",
        )
    else:
        # Режим без ИИ: только математический анализ
        analytics_text = build_analytics_message(
            comparison=comparison,
            reg_name=reg_name,
            current_label=current_label,
            previous_label=prev_label,
        )
        await _send_long_message(
            context.bot, chat_id,
            text=analytics_text,
            parse_mode="HTML",
        )

    # Отправляем Excel-файл аналитики
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_reg = reg_name.replace(" ", "_")[:30]
    ai_suffix = "_ai" if use_llm else ""
    filename = f"dtp_analytics{ai_suffix}_{safe_reg}_{period.year}_vs_{prev_year}_{timestamp}.xlsx"

    await _tg_retry(lambda: context.bot.send_document(
        chat_id=chat_id,
        document=analytics_bytes,
        filename=filename,
        caption=(
            f"\U0001F4CA Аналитика: {reg_name}\n"
            f"{current_label} vs {prev_label}\n"
            f"Текущий: {current_cards_count} ДТП | Прошлый: {prev_cards_count} ДТП"
        ),
    ), "send_document (аналитика)")

    # Генерируем и отправляем HTML-отчёт с визуализациями
    try:
        await _send_analytics_html(
            context, chat_id,
            reg_name=reg_name,
            current_label=current_label,
            prev_label=prev_label,
            current_cards=current_cards,
            prev_cards=prev_cards,
        )
    except Exception as e:
        logger.warning(f"Не удалось сгенерировать HTML-отчёт аналитики: {e}")

    # Предлагаем задать вопросы (только если аналитика с ИИ)
    if use_llm and llm_available:
        context.user_data["qa_mode"] = True
        context.user_data["qa_llm_provider"] = llm_provider
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "\u21A9\uFE0F В меню",
                callback_data="back_to_menu",
            )],
            [InlineKeyboardButton(
                "\u274C Завершить",
                callback_data="end_qa",
            )],
        ])
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "\u2753 Вы можете задавать вопросы по этим данным.\n"
                "Просто напишите вопрос текстом, например:\n"
                "\n"
                "\u2022 Почему выросла тяжкость аварий?\n"
                "\u2022 Какие рекомендации можно дать?\n"
                "\u2022 Что происходит с нетрезвыми водителями?\n\n"
                "Или нажмите /dtp для новой выгрузки."
            ),
            reply_markup=keyboard,
        )
    else:
        # Без ИИ — показываем кнопку возврата в меню
        menu_text, menu_kb = _build_menu_keyboard(context)
        if menu_text and menu_kb:
            await context.bot.send_message(
                chat_id=chat_id,
                text=menu_text,
                reply_markup=menu_kb,
                parse_mode="HTML",
            )

    logger.info(
        f"Аналитика отправлена: {reg_name}, "
        f"{current_label} vs {prev_label}, "
        f"{current_cards_count} vs {prev_cards_count} ДТП, "
        f"LLM={'да' if (use_llm and llm_summary_text) else 'нет'}"
    )

    # НЕ удаляем analytics_cards/comparison здесь — они нужны для:
    # - кнопки «В меню» (_build_menu_keyboard проверяет analytics_cards)
    # - QA-вопросов (_handle_analytics_question использует cards и comparison)
    # - повторного запуска очагов/HTML-карты из меню
    # Полная очистка происходит в _clear_analytics_data() при «Завершить»/«Сменить данные»/cancel.


def _clear_analytics_data(user_data: dict) -> None:
    """Очищает все данные аналитики из user_data (включая тяжёлые списки ДТП)."""
    for key in [
        "analytics_ready", "analytics_reg_code", "analytics_reg_name",
        "analytics_period", "analytics_cards", "analytics_comparison",
        "analytics_current_label", "analytics_prev_label",
        "analytics_prev_cards", "analytics_clusters",
        "analytics_news_context", "qa_mode", "qa_llm_provider", "qa_history",
        "point_stats_mode", "point_stats_lat", "point_stats_lon", "point_stats_radius",
        "cameras_data", "waiting_camera_file", "waiting_camera_for_map",
        "_settlement_polygons", "_preload_task",
    ]:
        user_data.pop(key, None)


async def _run_concentration_points(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Рассчитывает очаги концентрации ДТП с исторической динамикой
    (сравнение с прошлым годом) и отправляет Excel-файл.
    """
    chat_id = update.effective_chat.id

    reg_name = context.user_data.get("analytics_reg_name", "")
    reg_code = context.user_data.get("analytics_reg_code", "")
    period = context.user_data.get("analytics_period")
    current_cards = _get_current_cards(context) or []

    if not period or not current_cards:
        await update.callback_query.edit_message_text(
            "Данные для расчёта очагов не найдены. "
            "Пожалуйста, выполните выгрузку заново."
        )
        return

    current_label = period.label
    prev_year = period.year - 1
    dat_list_prev = [f"{m}.{prev_year}" for m in period.months]
    prev_label = period.label.replace(str(period.year), str(prev_year))

    status_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "\U0001F525 Очаги ДТП: подготовка...\n\n"
            f"Регион: {reg_name}\n"
            f"Период: {current_label}\n"
            f"ДТП: {len(current_cards)}\n\n"
            f"Этапы:\n"
            f"1. Загрузка данных за прошлый год\n"
            f"2. Загрузка границ НП из OSM\n"
            f"3. Расчёт очагов текущего периода\n"
            f"4. Расчёт очагов + динамика\n"
            f"5. Генерация результата\n\n"
            f"\u23F3 Начинаю..."
        ),
    )

    async def progress_callback(text: str) -> None:
        """Обновляет статусное сообщение."""
        try:
            await status_msg.edit_text(
                f"\U0001F525 Очаги ДТП (динамика)\n\n"
                f"Регион: {reg_name}\n"
                f"{current_label} vs {prev_label}\n\n"
                f"{text}"
            )
        except Exception:
            pass

    try:
        # --- Загрузка данных за прошлый год (из кэша или с сервера) ---
        prev_cards = []
        errors = []

        # Проверяем per-user кэш и глобальный кэш (может заполнен preload-задачей)
        # Если Preload ещё выполняется — ждём его завершения
        preload_task = context.user_data.get("_preload_task")
        if preload_task and not preload_task.done():
            logger.info("Очаги-динамика: ждём завершения фоновой загрузки за прошлый год...")
            try:
                await status_msg.edit_text(
                    f"\U0001F525 Очаги ДТП (динамика)\n\n"
                    f"Регион: {reg_name}\n"
                    f"{current_label} vs {prev_label}\n\n"
                    f"\u23F3 [1/5] Ждём завершения фоновой загрузки данных за прошлый год..."
                )
            except Exception:
                pass
            try:
                await asyncio.wait_for(preload_task, timeout=300)
            except asyncio.TimeoutError:
                logger.warning("Очаги-динамика: preload не завершился за 5 мин, скачиваем самостоятельно")
            except Exception:
                pass  # preload упал — продолжим самостоятельно

        cached_prev = context.user_data.get("analytics_prev_cards", [])
        cached_prev_label = context.user_data.get("analytics_prev_label", "")

        if (not cached_prev or cached_prev_label != prev_label):
            global_cached = await data_cache_get_async(reg_code, dat_list_prev)
            if global_cached is not None:
                cached_prev, _ = global_cached
                cached_prev_label = prev_label

        if cached_prev and cached_prev_label == prev_label:
            prev_cards = cached_prev
            # Обновляем per-user кэш
            context.user_data["analytics_prev_cards"] = prev_cards
            context.user_data["analytics_prev_label"] = prev_label
            await progress_callback(
                f"\u2705 [1/5] Данные за прошлый год: из кэша ({len(prev_cards)} ДТП)"
            )
        elif reg_code:
            async def fetch_progress(i, total, month_name, year):
                await progress_callback(
                    f"\u23F3 [1/5] Загрузка данных за прошлый год...\n"
                    f"{i}/{total} — {month_name} {year}"
                )

            prev_cards, errors = await _fetch_cards_for_period(
                dat_list_prev, reg_code, "Очаги-динамика",
                progress_callback=fetch_progress,
            )

            # Кэшируем для повторного использования
            if prev_cards:
                context.user_data["analytics_prev_cards"] = prev_cards
                context.user_data["analytics_prev_label"] = prev_label

            if errors:
                logger.warning(
                    f"Ошибки загрузки прошлого года: {errors}"
                )
                # Предупреждаем пользователя о неполных данных
                err_text = "\n".join(f"- {e}" for e in errors)
                try:
                    await progress_callback(
                        f"Загрузка данных за прошлый год...\n"
                        f"⚠ Не удалось скачать:\n{err_text}\n\n"
                        f"Данные за эти месяцы отсутствуют, "
                        f"сравнение будет неполным."
                    )
                except Exception:
                    pass

        # --- Расчёт очагов с динамикой ---
        # Обёртка для progress_callback: добавляет нумерацию этапов [2/5]-[4/5]
        _step_map = {
            "границ": "[2/5] Загрузка границ НП из OpenStreetMap...",
            "текущего": "[3/5] Расчёт очагов текущего периода...",
            "прошлого": "[4/5] Расчёт очагов за прошлый год...",
            "Сопоставление": "[4/5] Сопоставление очагов между периодами...",
        }

        async def staged_progress(text: str) -> None:
            # Подставляем шаг на основе ключевых слов в text
            for key, step_text in _step_map.items():
                if key in text:
                    await progress_callback(f"\u23F3 {step_text}\n{text}")
                    return
            # Fallback — передаём как есть
            await progress_callback(f"\u23F3 {text}")

        # Код региона — для проверки регион-уровневого OSM-кэша
        _dyn_reg_code = (
            context.user_data.get("reg_code", "")
            or context.user_data.get("analytics_reg_code", "")
            or context.user_data.get("concentration_reg_code", "")
        )
        clusters, saved_polygons, _preclusters_dyn = await calculate_concentration_dynamics(
            current_cards,
            prev_cards,
            progress_callback=staged_progress,
            reg_code=_dyn_reg_code or None,
        )

        # Сохраняем полигоны для переиспользования в аналитике с ИИ
        if saved_polygons:
            context.user_data["_settlement_polygons"] = saved_polygons

        if not clusters:
            await status_msg.edit_text(
                "\U0001F525 Очаги ДТП\n\n"
                "Очаги концентрации ДТП не найдены.\n\n"
                "Возможные причины:\n"
                "\u2022 Мало ДТП за выбранный период\n"
                "\u2022 ДТП распределены равномерно (нет концентрации)\n"
                "\u2022 У большинства ДТП нет координат"
            )
            return

        # --- Сводная статистика ---
        dyn_stats = build_dynamics_summary(clusters)

        # --- Разделяем очаги: текущие vs исчезнувшие ---
        current_only_clusters = [
            c for c in clusters if not c.get("_is_lost", False)
        ]

        # --- Обогащение камерами (если загружен файл) ---
        cameras = context.user_data.get("cameras_data")
        if cameras:
            await progress_callback(
                f"\u23F3 [5/5] Сопоставление с камерами фотовидеофиксации...\n"
                f"Камер: {len(cameras)}"
            )
            enrich_clusters_with_cameras(current_only_clusters, cameras)
            lost_clusters = [
                c for c in clusters if c.get("_is_lost", False)
            ]
            if lost_clusters:
                enrich_clusters_with_cameras(lost_clusters, cameras)
        else:
            await progress_callback("\u23F3 [5/5] Генерация Excel-файла...")

        # --- Предочаги: извлекаем и обогащаем камерами ---
        # Используем предочаги, возвращённые отдельно из calculate_concentration_dynamics,
        # — раньше они терялись, когда clusters был пуст.
        preclusters = _preclusters_dyn or []
        if preclusters and cameras:
            enrich_clusters_with_cameras(preclusters, cameras)

        # --- Генерируем Excel с 4 листами ---
        def _build_concentration_excel():
            # Лист 1: очаги запрашиваемого года (стандартный формат)
            _current_data = build_concentration_excel_data(current_only_clusters)
            _current_columns = get_concentration_column_names()

            # Лист 2: динамика очагов (текущие + исчезнувшие)
            _dyn_data = build_dynamics_excel_data(clusters)
            _dyn_columns = get_dynamics_column_names()

            # Лист 3: детализация ДТП
            _detail_data = build_dynamics_detail_data(
                clusters, current_label, prev_label,
            )
            _detail_columns = get_dynamics_detail_column_names()

            # Лист 4: предочаги (используем уже обогащённые preclusters)
            _precluster_data = None
            _precluster_columns = None
            if preclusters:
                _precluster_data = build_precluster_excel_data(preclusters)
                _precluster_columns = get_precluster_column_names()

            _conc_bytes = generate_concentration_dynamics_file(
                _current_data, _current_columns,
                _dyn_data, _dyn_columns,
                _detail_data, _detail_columns,
                _precluster_data, _precluster_columns,
            )
            return _conc_bytes

        conc_bytes = await asyncio.to_thread(_build_concentration_excel)

        # Удаляем статус
        try:
            await status_msg.delete()
        except Exception:
            pass

        # --- Статистика по очагам текущего года ---
        current_np_count = sum(
            1 for c in current_only_clusters
            if c["zone_type"].startswith("settlement")
        )
        current_nonp_count = sum(
            1 for c in current_only_clusters
            if c["zone_type"] == "nonsettlement"
        )
        current_total_clusters = len(current_only_clusters)
        current_total_dtp = sum(
            c["total_accidents"] for c in current_only_clusters
        )
        current_deaths = sum(
            c["deaths"] for c in current_only_clusters
        )
        current_injured = sum(
            c["injured"] for c in current_only_clusters
        )

        # --- Текстовое резюме ---
        # Блок 1: очаги запрашиваемого года
        summary_lines = [
            f"\U0001F525 <b>Очаги ДТП: {reg_name}</b>",
            f"Период: {current_label}",
            f"Всего ДТП: {len(current_cards)}",
            "",
            f"\U0001F4CA <b>Очагов за {current_label}:</b> <b>{current_total_clusters}</b>",
            f"  \u2022 В НП: {current_np_count}",
            f"  \u2022 Вне НП: {current_nonp_count}",
            "",
            f"ДТП в очагах: {current_total_dtp}",
            f"  \u2022 Погибло: {current_deaths}",
            f"  \u2022 Ранено: {current_injured}",
        ]

        # Блок камер (если загружены)
        if cameras:
            cam_closed = sum(
                1 for c in current_only_clusters
                if (c.get("camera_match") or {}).get("status") == "закрыт"
            )
            cam_open = current_total_clusters - cam_closed
            summary_lines.extend([
                "",
                f"\U0001F4F7 <b>Камеры фотовидеофиксации:</b>",
                f"  \u2022 Закрыто камерой: {cam_closed}/{current_total_clusters}",
                f"  \u2022 Открыто: {cam_open}",
            ])

        # Блок 2: динамика (только если есть данные за прошлый год)
        if prev_cards:
            summary_lines.extend([
                "",
                f"<b>\U0001F4C8 Динамика ({prev_label}):</b>",
                f"  \U0001F7E2 Новый: {dyn_stats['new']}",
                f"  \u2B06 Рост: {dyn_stats['growing']}",
                f"  \u2B07 Снижение: {dyn_stats['shrinking']}",
                f"  \u27A1 Стабильный: {dyn_stats['stable']}",
                f"  \u274C Исчезнувший: {dyn_stats['lost']}",
            ])

            if dyn_stats["prev_total_dtp"] > 0:
                delta_dtp = (
                    dyn_stats["current_total_dtp"]
                    - dyn_stats["prev_total_dtp"]
                )
                summary_lines.append("")
                summary_lines.append(
                    f"ДТП в очагах ({prev_label}): {dyn_stats['prev_total_dtp']} "
                    f"({delta_dtp:+d})"
                )

        # Блок 3: предочаги
        if preclusters:
            pre_np = sum(
                1 for p in preclusters
                if p["zone_type"].startswith("settlement")
            )
            pre_nonp = len(preclusters) - pre_np
            pre_dtp = sum(p["total_accidents"] for p in preclusters)
            summary_lines.extend([
                "",
                f"\u26A0\uFE0F <b>Предочаги:</b> <b>{len(preclusters)}</b>",
                f"  \u2022 В НП: {pre_np}",
                f"  \u2022 Вне НП: {pre_nonp}",
                f"  \u2022 ДТП в предочагах: {pre_dtp}",
            ])

        await _send_long_message(
            context.bot, chat_id,
            text="\n".join(summary_lines),
            parse_mode="HTML",
        )

        # Отправляем Excel
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_reg = reg_name.replace(" ", "_")[:30]
        filename = (
            f"dtp_ochagi_dynamics_{safe_reg}_"
            f"{period.year}_{timestamp}.xlsx"
        )

        await context.bot.send_document(
            chat_id=chat_id,
            document=conc_bytes,
            filename=filename,
            caption=(
                f"\U0001F525 Очаги ДТП: {reg_name}\n"
                f"{current_label}"
                + (f" | Динамика: {prev_label}" if prev_cards else "")
                + f"\n"
                f"Очагов: {current_total_clusters} | "
                f"ДТП в очагах: {current_total_dtp}"
                + (f" | Предочагов: {len(preclusters)}" if preclusters else "")
            ),
        )

        # Генерируем и отправляем HTML-карту очагов
        try:
            await _send_clusters_html(
                context, chat_id,
                reg_name=reg_name,
                current_label=current_label,
                clusters=current_only_clusters,
                preclusters=preclusters,
                cameras=cameras,
            )
        except Exception as e:
            logger.warning(f"Не удалось сгенерировать HTML-карту очагов: {e}")

        # Сохраняем очаги в сессию (для LLM и дальнейших вопросов)
        context.user_data["analytics_clusters"] = clusters

        # Показываем кнопку возврата в меню
        menu_text, menu_kb = _build_menu_keyboard(context)
        if menu_text and menu_kb:
            await context.bot.send_message(
                chat_id=chat_id,
                text=menu_text,
                reply_markup=menu_kb,
                parse_mode="HTML",
            )

        logger.info(
            f"Очаги отправлены: {reg_name}, "
            f"{current_label}, "
            f"{current_total_clusters} очагов из {len(current_cards)} ДТП"
            + (f", динамика: {prev_label}" if prev_cards else "")
        )

        # Освобождаем память: удаляем камеры из сессии
        context.user_data.pop("cameras_data", None)

        # Полигоны OSM оставляем в user_data["_settlement_polygons"]
        # для переиспользования в AI-анализе (без повторного запроса к OSM).

    except Exception as e:
        logger.exception(f"Ошибка расчёта очагов (динамика): {e}")
        try:
            await status_msg.edit_text(
                f"\u26A0\uFE0F Ошибка при расчёте очагов ДТП:\n\n{_sanitize_error(e)}\n\n"
                f"Попробуйте позже или выберите другой период."
            )
        except Exception:
            pass




