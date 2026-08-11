"""bot.handlers.commands — обработчики команд Telegram.

Содержит:
  • cmd_start, cmd_help, cmd_dtp, cmd_regions, cmd_miniapp, cmd_precache
  • _show_region_keyboard, _run_precache

Выделено из единого bot.py (Phase 3-2). 100% pure.
"""
from bot._state import *
from bot.access import is_user_allowed, _load_regions_if_needed
from bot.keyboards import build_region_keyboard

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_user_allowed(update.effective_user.id):
        await update.message.reply_text("У вас нет доступа к этому боту.")
        return

    await update.message.reply_text(
        "Привет! Я бот для выгрузки данных ДТП с stat.gibdd.ru.\n\n"
        "Способы запроса:\n\n"
        "1. Кнопки: /dtp — выберите регион и период\n\n"
        "2. Текстом (примеры):\n"
        "   Вологодская область за 2025 год\n"
        "   Вологодская за 3 месяца 2026\n"
        "   март 2025 Алтайский край\n"
        "   за I квартал 2025 Москва\n"
        "   2.2024 1101\n\n"
        "Результат: 2 Excel-файла\n"
        "  1. Карточки ДТП (1 строка = 1 ДТП)\n"
        "  2. Участники ДТП (1 строка = 1 участник)\n\n"
        "После выгрузки бот предложит:\n"
        "\U0001F4CA Анализ — сравнение с прошлым годом\n"
        "\U0001F916 Анализ с ИИ — анализ + резюме нейросети\n"
        "\U0001F525 Очаги ДТП — места концентрации аварийности\n\n"
        "Команды:\n"
        "/dtp — начать выгрузку через кнопки\n"
        "/miniapp — открыть веб-приложение с картой и отчётами\n"
        "/help — справка\n"
        "/regions — список регионов"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_user_allowed(update.effective_user.id):
        return

    await update.message.reply_text(
        "Справка по использованию бота\n\n"
        "--- Способ 1: Кнопки ---\n"
        "/dtp → выберите регион → выберите период\n\n"
        "--- Способ 2: Текстом ---\n"
        "Напишите запрос на русском, например:\n"
        "  Вологодская область за 2025 год\n"
        "  Алтайский край за 3 месяца 2026\n"
        "  март 2025 Вологодская\n"
        "  за I квартал 2025 Татарстан\n"
        "  за полугодие 2025 Москва\n\n"
        "--- Способ 3: Строгий формат ---\n"
        "  2.2024 1101  (месяц.год код_региона)\n\n"
        "--- Аналитика ---\n"
        "После выгрузки данных бот предложит:\n\n"
        "\U0001F4CA <b>Анализ</b> — математическое сравнение\n"
        "текущего периода с аналогичным периодом\n"
        "прошлого года. Результат: текстовое резюме + Excel-файл.\n\n"
        "\U0001F916 <b>Анализ с ИИ</b> — то же самое +\n"
        "текстовое резюме от нейросети GLM\n"
        "и возможность задавать вопросы по данным.\n\n"
        "\U0001F525 <b>Очаги ДТП</b> — выявление мест\n"
        "концентрации аварийности (перекрёстки, участки дорог).\n"
        "Результат: Excel-файл с описанием очагов и подробностями.\n\n"
        "--- Команды ---\n"
        "/dtp — выгрузка через кнопки\n"
        "/miniapp — открыть веб-приложение с картой и отчётами\n"
        "/regions — список регионов\n"
        "/precache — управление кэшем OSM-границ\n"
        "/help — эта справка\n\n"
        "--- Результат выгрузки ---\n"
        "Бот вернёт 2 Excel-файла:\n"
        "  1. dtp_cards.xlsx — карточки ДТП\n"
        "  2. dtp_uch.xlsx — участники ДТП\n\n"
        "--- Контакты ---\n"
        "Вопросы и предложения по работе бота:\n"
        "@flame1290 и @Julich_Vorobevich",
        parse_mode="HTML",
    )


async def cmd_dtp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /dtp — начало интерактивной выгрузки через кнопки."""
    if not is_user_allowed(update.effective_user.id):
        return

    await _show_region_keyboard(update, context, page=0)


async def _show_region_keyboard(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    page: int = 0,
    edit_message: bool = False,
) -> None:
    """Показывает клавиатуру выбора региона."""
    msg = await update.message.reply_text("Загружаю список регионов...") if not edit_message else None

    regions = await _load_regions_if_needed(context)
    if not regions:
        text = (
            "Не удалось загрузить список регионов.\n\n"
            "Сервер ГИБДД недоступен, а локальный кэш пуст.\n\n"
            "Возможные действия:\n"
            "• Подождите и попробуйте позже\n"
            "• Используйте текстовый формат:\n"
            "  <code>месяц.год код_региона</code>\n"
            "  Например: <code>6.2026 1119</code>"
        )
        if msg:
            await msg.edit_text(text, parse_mode="HTML")
        else:
            await update.callback_query.edit_message_text(text, parse_mode="HTML")
        return

    keyboard = build_region_keyboard(regions, page)
    text = "Выберите регион:"

    if edit_message and update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text, reply_markup=keyboard,
            )
        except Exception:
            await update.callback_query.message.reply_text(text, reply_markup=keyboard)
    else:
        await msg.edit_text(text, reply_markup=keyboard)


async def cmd_regions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /regions — выводит список регионов текстом."""
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

    # Отправляем частями
    chunk_size = 40
    for i in range(0, len(lines), chunk_size):
        chunk = lines[i:i + chunk_size]
        text = "\n".join(chunk)
        await update.message.reply_text(text, parse_mode="HTML")

    await msg.delete()


async def cmd_miniapp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /miniapp — присылает кнопку для открытия Mini App в WebView.

    Использует BOTHOST_DOMAIN из окружения для построения URL.
    Если домен не задан — сообщает об ошибке конфигурации.
    """
    if not is_user_allowed(update.effective_user.id):
        return

    import os
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    # Нормализуем домен (убираем возможный протокол/слэш/порт)
    raw_domain = os.getenv("BOTHOST_DOMAIN", "").strip()
    for proto in ("https://", "http://", "www."):
        if raw_domain.startswith(proto):
            raw_domain = raw_domain[len(proto):]
    domain = raw_domain.rstrip("/").split(":")[0]

    if not domain:
        await update.message.reply_text(
            "Mini App недоступен: администратор не задал BOTHOST_DOMAIN.\n"
            "Используйте /dtp для выгрузки через кнопки."
        )
        return

    miniapp_url = f"https://{domain}/app/"
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(text="Открыть Mini App", web_app={"url": miniapp_url})]]
    )

    await update.message.reply_text(
        "Веб-приложение для просмотра статистики ДТП:\n"
        "• Интерактивная карта с очагами аварийности\n"
        "• Excel-отчёты для скачивания\n"
        "• История запросов\n\n"
        "Нажмите кнопку ниже, чтобы открыть.",
        reply_markup=keyboard,
    )


async def cmd_precache(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /precache — управление кэшем OSM-границ.

    Использование:
        /precache                      — статус кэша + список готовых регионов
        /precache 1145                 — закэшировать Москву
        /precache 1145,1146,1147       — несколько регионов
        /precache all                  — все 23 региона из DEFAULT_REGIONS
        /precache list                 — список всех 82 регионов с кодами
        /precache 1145 force           — принудительно обновить (даже если есть)
    """
    if not is_user_allowed(update.effective_user.id):
        return

    import json as _json
    import time as _time
    from pathlib import Path as _Path

    args = context.args or []
    cache_dir = os.path.join(
        os.environ.get("CAMERA_DATA_DIR", str(_Path(__file__).parent / "data")),
        "osm_cache",
    )

    # /precache без аргументов — статус кэша
    if not args:
        try:
            files = []
            if os.path.exists(cache_dir):
                files = sorted(
                    f for f in os.listdir(cache_dir)
                    if f.startswith("region_") and f.endswith(".json")
                )
            lines = ["<b>🗂 Кэш OSM-границ</b>\n"]
            lines.append(f"Папка: <code>{cache_dir}</code>")
            lines.append(f"Регионов в кэше: <b>{len(files)}</b>\n")
            total_mb = 0.0
            for f in files:
                path = os.path.join(cache_dir, f)
                size_mb = os.path.getsize(path) / (1024 * 1024)
                total_mb += size_mb
                try:
                    with open(path, "r", encoding="utf-8") as fp:
                        data = _json.load(fp)
                    code = data.get("region_code", "?")
                    name = data.get("region_name", "?")
                    count = data.get("count", 0)
                    age_days = (_time.time() - data.get("timestamp", 0)) / 86400
                    ttl_days = data.get("ttl_seconds", 7776000) / 86400
                    remaining = max(0, ttl_days - age_days)
                    lines.append(
                        f"<code>{code}</code> — {name} "
                        f"({count} НП, {size_mb:.1f} МБ, осталось {remaining:.0f}д)"
                    )
                except Exception:
                    lines.append(f"<code>{f}</code> ({size_mb:.1f} МБ)")
            lines.append(f"\n<b>Итого: {total_mb:.1f} МБ</b>")
            lines.append("\nКоманды:")
            lines.append("• <code>/precache 1145</code> — закэшировать регион")
            lines.append("• <code>/precache all</code> — все 23 топ-региона")
            lines.append("• <code>/precache list</code> — все 82 региона с кодами")
            lines.append("• <code>/precache 1145 force</code> — обновить принудительно")
            text = "\n".join(lines)
            for i in range(0, len(text), 4000):
                await update.message.reply_text(text[i:i + 4000], parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка чтения кэша: {e}")
        return

    # /precache list — все 82 региона
    if args[0].lower() == "list":
        regions = await _load_regions_if_needed(context)
        if not regions:
            await update.message.reply_text("Не удалось загрузить список регионов.")
            return
        lines = [f"<b>Все регионы РФ ({len(regions)})</b>\n"]
        for r in regions:
            lines.append(f"<code>{r['code']}</code> — {r['name']}")
        text = "\n".join(lines)
        for i in range(0, len(text), 4000):
            await update.message.reply_text(text[i:i + 4000], parse_mode="HTML")
        return

    # Защита от параллельных запусков
    if _precache_lock.locked():
        await update.message.reply_text(
            "⚠️ precache уже запущен. Дождитесь завершения.\n"
            "Overpass API не терпит параллельных запросов (429/504)."
        )
        return

    async with _precache_lock:
        # /precache all — все 23 топ-региона
        if args[0].lower() == "all":
            await _run_precache(update, ["python", "precache_osm.py"], "23 топ-региона")
            return

        # /precache 1145[,1146,...] [force]
        # Поддерживаем 2 формата:
        #   /precache 1145              — только коды (имя подгрузится из regions_builtin.json)
        #   /precache 1145,Имя региона  — код + имя (через запятую, если регион нестандартный)
        codes_arg = args[0]
        force = len(args) > 1 and args[1].lower() == "force"

        # Проверяем формат: либо все части — коды (4 цифры), либо код,Имя
        parts = codes_arg.split(",")
        all_codes = all(p.strip().isdigit() for p in parts)
        has_named = any(not p.strip().isdigit() and p.strip() for p in parts[1:]) if len(parts) > 1 else False
        # Формат "code,Name" — одна пара
        is_code_name_pair = (
            len(parts) == 2
            and parts[0].strip().isdigit()
            and parts[1].strip()
            and not parts[1].strip().isdigit()
        )

        if is_code_name_pair:
            # /precache 1182,Республика Дагестан
            code = parts[0].strip()
            name = parts[1].strip()
            # Используем --regions-file через временный файл (так умеет precache_osm.py)
            import tempfile
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, encoding="utf-8"
            )
            tmp.write(f"{code},{name}\n")
            tmp.close()
            cmd = ["python", "precache_osm.py", "--regions-file", tmp.name]
            if force:
                cmd.append("--force")
            await _run_precache(update, cmd, f"{name} ({code})")
            try:
                os.unlink(tmp.name)
            except Exception:
                pass
            return

        if not all_codes:
            await update.message.reply_text(
                "❌ Неверный формат. Примеры:\n"
                "<code>/precache 1145</code> — по коду (имя подгрузится автоматически)\n"
                "<code>/precache 1145,1146,1147</code> — несколько регионов\n"
                "<code>/precache 1182,Республика Дагестан</code> — код + имя\n"
                "<code>/precache 1145 force</code> — обновить принудительно",
                parse_mode="HTML",
            )
            return
        cmd = ["python", "precache_osm.py", "--codes", codes_arg]
        if force:
            cmd.append("--force")
        await _run_precache(update, cmd, f"регион(ы) {codes_arg}")


async def _run_precache(update: Update, cmd: list, label: str) -> None:
    """Запускает precache_osm.py как subprocess.

    Все логи subprocess идут в stdout/stderr → автоматически попадают в
    `docker logs` (видны через «Показать логи» на bothost).
    В Telegram отправляем только 2 уведомления: старт и финиш.
    """
    await update.message.reply_text(
        f"🔄 Запуск precache: {label}\n"
        f"⏳ Процесс идёт в фоне. Полные логи — «Показать логи» на bothost.\n"
        f"Сообщу о завершении.",
    )

    try:
        # stdout/stderr не перехватываем — вывод идёт напрямую в docker logs.
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        await proc.wait()

        if proc.returncode == 0:
            await update.message.reply_text(
                "✅ precache завершён успешно.\n"
                "Используйте /precache для проверки статуса кэша."
            )
        else:
            await update.message.reply_text(
                f"❌ precache завершился с ошибкой (код {proc.returncode}).\n"
                f"Подробности — в «Показать логи» на bothost."
            )
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка запуска: {e}")



