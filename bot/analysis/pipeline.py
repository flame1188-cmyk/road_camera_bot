"""bot.analysis.pipeline — конвейер выгрузки и предложение анализа.

Содержит:
  • _start_fetching — мультизапрос с прогрессом + генерация Excel + отправка
  • _offer_analysis — предложение анализа после выгрузки (запуск preload)
  • _preload_prev_year — фоновая загрузка данных за прошлый год

Зависимости:
  • bot.analysis.menu._build_menu_keyboard — для меню после выгрузки

Выделено из единого bot/analysis.py (Phase 3-4). 100% pure.
"""
from __future__ import annotations

from bot._state import *
from bot.infra import (
    _tg_retry,
    _safe_edit,
    _send_long_message,
    _sanitize_error,
    _make_progress_bar,
    _log_memory,
)
from bot.access import _fetch_cards_for_period
from bot.keyboards import build_region_keyboard, build_period_keyboard
from bot.analysis.menu import _build_menu_keyboard


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
