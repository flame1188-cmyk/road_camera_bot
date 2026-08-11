"""bot.app — точка входа и сборка Application.

Содержит:
  • error_handler — глобальный обработчик ошибок PTB
  • _post_init / _post_shutdown — lifecycle hooks
  • _build_app — сборка Application с хендлерами
  • main — точка входа (стартап-ретрай)

Выделено из единого bot.py (Phase 3-2). 100% pure.
"""
from bot._state import *
from bot.infra import _IsDocument, _tg_retry, _sanitize_error
from bot.handlers.commands import (
    cmd_start, cmd_help, cmd_dtp, cmd_regions, cmd_miniapp, cmd_precache,
)
from bot.handlers.callbacks import on_callback_query
from bot.handlers.messages import handle_message, _handle_document
from bot.point_stats import _handle_location_message

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error

    if isinstance(error, Conflict):
        import time as _time
        global _conflict_last_log
        now = _time.monotonic()
        if now - _conflict_last_log >= _CONFLICT_LOG_INTERVAL:
            _conflict_last_log = now
            logger.warning(
                "Conflict: другой экземпляр бота (deploy). "
                "Автоматически разрешится. Следующее сообщение через 60с."
            )
        return

    if isinstance(error, NetworkError):
        label = _sanitize_error(error)
        logger.warning(f"Сетевая ошибка (временная): {label}")
        # Уведомляем пользователя только если есть конкретное сообщение
        # (при таймауте get_updates update=None — уведомлять некого)
        try:
            if isinstance(update, Update) and update.callback_query and update.callback_query.message:
                await _tg_retry(
                    update.callback_query.message.reply_text,
                    "⚠️ Временная проблема с подключением к Telegram.\n"
                    "Попробуйте нажать кнопку ещё раз.",
                )
            elif isinstance(update, Update) and update.message:
                await _tg_retry(
                    update.message.reply_text,
                    "⚠️ Временная проблема с подключением к Telegram.\n"
                    "Попробуйте отправить запрос ещё раз.",
                )
        except Exception:
            pass  # Само уведомление тоже может упасть — игнорируем
        return

    logger.error(f"Ошибка: {_sanitize_error(error)}", exc_info=error)




async def _post_init(app) -> None:
    """Проверка доступности Telegram API при запуске с ретраем.

    Запускается ВНУТРИ event loop, который создаёт run_polling(),
    поэтому не вызывает «Event loop is closed» при ретраях.
    """
    _RETRIES = 5
    _DELAYS = [5, 10, 15, 30, 60]
    for attempt in range(1, _RETRIES + 1):
        try:
            await app.bot.get_me()
            logger.info(f"Telegram API доступен (попытка {attempt})")
            return
        except (TimedOut, NetworkError) as e:
            if attempt < _RETRIES:
                delay = _DELAYS[attempt - 1]
                logger.warning(
                    f"Telegram API недоступен при запуске ({type(e).__name__}). "
                    f"Попытка {attempt}/{_RETRIES}, повтор через {delay}с..."
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    f"Telegram API недоступен после {_RETRIES} попыток. "
                    f"Бот остановится."
                )
                raise


# Флаг: run_polling() завершился штатно (Ctrl+C), а не по таймауту при старте.
# Если False — _post_shutdown НЕ закрывает HTTP-клиенты, чтобы не сломать
# следующую попытку в retry-цикле main().
# Примечание: _clean_shutdown объявлен в bot._state (shared state).
# Здесь только используем его через `global`.


async def _post_shutdown(app) -> None:
    """Корректно закрывает все HTTP-клиенты при остановке бота."""
    global _clean_shutdown
    if not _clean_shutdown:
        # retry при старте — не закрываем клиенты, они привязаны
        # к уже закрытому event loop следующей попытки создаст свои.
        logger.info("post_shutdown: нештатный выход (startup-retry), клиенты не закрываем")
        return
    await close_client()
    await close_llm_client()
    await _close_overpass_client()
    logger.info("Все HTTP-клиенты закрыты (post_shutdown)")


def _build_app(token: str) -> "Application":
    """Создаёт и настраиваем Application. Вызывается заново для каждого retry."""
    app = (
        Application.builder()
        .token(token)
        .concurrent_updates(True)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .get_updates_connect_timeout(30.0)
        .get_updates_read_timeout(60.0)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    # Команды
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("dtp", cmd_dtp))
    app.add_handler(CommandHandler("regions", cmd_regions))
    app.add_handler(CommandHandler("miniapp", cmd_miniapp))
    app.add_handler(CommandHandler("precache", cmd_precache))
    # Callback-кнопки
    app.add_handler(CallbackQueryHandler(on_callback_query))
    # Текстовые сообщения
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    # Сообщения с локацией (для статистики по точке)
    app.add_handler(MessageHandler(filters.LOCATION, _handle_location_message))
    # Документы (загрузка камер фотовидеофиксации)
    app.add_handler(MessageHandler(_IsDocument(), _handle_document))
    # Глобальный обработчик ошибок
    app.add_error_handler(error_handler)
    return app


def main() -> None:
    # tracemalloc.start(25) удалён — замедлял openpyxl в 177-400x
    # (трассировка 25 фреймов стека на каждую аллокацию Python)
    logger.info("=== GIBDD Telegram Bot запускается ===")

    errors = validate_config()
    if errors:
        print("\nОШИБКИ КОНФИГУРАЦИИ:")
        for err in errors:
            print(f"  x {err}")
        print("\nСоздайте файл .env на основе .env.example и заполните его.")
        sys.exit(1)

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")

    print("\nGIBDD-бот запускается...")
    print("  /dtp — выгрузка через кнопки")
    print("  /help — справка")
    print("  Текст — 'Вологодская область за 2025 год'")
    print("  Нажмите Ctrl+C для остановки.\n")

    # Стартап-ретрай: Application создаётся заново для каждой попытки,
    # т.к. после TimedOut внутренний event loop закрывается и объект
    # становится непригодным для повторного run_polling().
    _STARTUP_RETRIES = 5
    _STARTUP_DELAYS = [5, 10, 15, 30, 60]
    for attempt in range(1, _STARTUP_RETRIES + 1):
        try:
            global _clean_shutdown
            _clean_shutdown = False
            # PTB __run() использует asyncio.get_event_loop() для setup
            # signal handlers. Если loop от предыдущего деплоя закрыт —
            # падает RuntimeError. Принудительно создаём свежий loop.
            asyncio.set_event_loop(asyncio.new_event_loop())
            app = _build_app(token)
            app.run_polling(allowed_updates=Update.ALL_TYPES)
            # Если мы здесь — bot остановлен штатно (Ctrl+C / SIGTERM)
            _clean_shutdown = True
            break
        except (TimedOut, NetworkError, RuntimeError) as e:
            if attempt < _STARTUP_RETRIES:
                delay = _STARTUP_DELAYS[attempt - 1]
                logger.warning(
                    f"Ошибка запуска ({type(e).__name__}: {e}). "
                    f"Попытка {attempt}/{_STARTUP_RETRIES}, повтор через {delay}с..."
                )
                time.sleep(delay)
            else:
                logger.error(
                    f"Не удалось запустить бота после {_STARTUP_RETRIES} попыток. "
                    f"Последняя ошибка: {type(e).__name__}: {e}"
                )
                raise


if __name__ == "__main__":
    main()


