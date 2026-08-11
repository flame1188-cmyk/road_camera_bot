"""bot.analysis.clusters — расчёт очагов ДТП с динамикой.

Содержит:
  • _run_concentration_points — расчёт очагов с историей (АППГ),
    обогащение камерами, генерация Excel (4 листа) + HTML-карты

Вторая по размеру функция (~414 строк) в пакете bot.analysis.

Зависимости:
  • bot.analysis.state._get_current_cards — для получения текущих карточек
  • bot.analysis.menu._build_menu_keyboard — для возврата в меню
  • bot.output._send_clusters_html — для HTML-карты очагов

Выделено из единого bot/analysis.py (Phase 3-4). 100% pure.
"""
from __future__ import annotations

from bot._state import *
from bot.infra import (
    _send_long_message,
    _sanitize_error,
)
from bot.access import _fetch_cards_for_period
from bot.output import _send_clusters_html
from bot.analysis.state import _get_current_cards
from bot.analysis.menu import _build_menu_keyboard


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
