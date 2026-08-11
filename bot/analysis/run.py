"""bot.analysis.run — основной цикл аналитики (сравнение с АППГ).

Содержит:
  • _run_analysis — сравнительный анализ текущего периода с прошлым годом
    (метрики → опц. LLM-резюме → Excel → HTML-отчёт → опц. QA-режим)

Самая большая функция (~450 строк) в пакете bot.analysis.

Зависимости:
  • bot.analysis.state._get_current_cards — для получения текущих карточек
  • bot.analysis.menu._build_menu_keyboard — для возврата в меню

Late imports (внутри _run_analysis, для разрыва циклов):
  • telegram.Bot (для send_document)
  • analytics.calculate_cross_tables / calculate_statistical_metrics
  • llm_analyzer.format_cross_tables_for_prompt / format_statistical_metrics_for_prompt

Выделено из единого bot/analysis.py (Phase 3-4). 100% pure.
"""
from __future__ import annotations

from bot._state import *
from bot.infra import (
    _tg_retry,
    _send_long_message,
    _sanitize_error,
    _make_progress_bar,
)
from bot.access import _fetch_cards_for_period
from bot.output import _send_analytics_html
from bot.analysis.state import _get_current_cards
from bot.analysis.menu import _build_menu_keyboard


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
