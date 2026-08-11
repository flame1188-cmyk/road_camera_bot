"""bot.access — контроль доступа и загрузка регионов.

Содержит:
  • is_user_allowed
  • _get_regions / _load_regions_if_needed
  • _fetch_cards_for_period — основная функция загрузки карточек ДТП
    (API ГИБДД + web_fallback + кэш)

Выделено из единого bot.py (Phase 3-2). 100% pure.
"""
from bot._state import *
from bot.infra import _is_api_down, _mark_api_down

def is_user_allowed(user_id: int) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS


def _get_regions(context: ContextTypes.DEFAULT_TYPE) -> list[dict[str, str]]:
    """Возвращает список регионов из кэша в user_data."""
    return context.bot_data.get("regions", [])


async def _load_regions_if_needed(context: ContextTypes.DEFAULT_TYPE) -> list[dict[str, str]]:
    """Загружает справочник регионов, если ещё не загружен."""
    regions = _get_regions(context)
    if not regions:
        regions = await ensure_regions_loaded()
        context.bot_data["regions"] = regions
    return regions


async def _fetch_cards_for_period(
    dat_list: list[str],
    reg_code: str,
    log_prefix: str,
    progress_callback=None,
    notify_callback=None,
    cache_result: bool = True,
) -> tuple[list[dict], list[str]]:
    """Загружает карточки ДТП за список месяцев с GIBDD API.

    При получении 5xx от API автоматически переключается на запасной
    метод через сайт stat.gibdd.ru (web_fallback).

    Общая функция для аналитики, очагов и точечной статистики —
    устраняет дублирование одного и того же цикла в 3 местах.

    Args:
        dat_list: Список строк в формате "m.YYYY"
        reg_code: Код региона
        log_prefix: Префикс для логов (например "Аналитика", "Очаги")
        progress_callback: Опциональная async-функция(i, total, month_name, year)
                           для обновления статуса
        notify_callback: Опциональная async-функция(str) для одноразовых
                         уведомлений пользователю (например, о переключении
                         на запасной метод)

    Returns:
        (cards, errors) — список карточек ДТП и список строк-ошибок
    """
    import httpx as _httpx

    # --- Глобальный кэш: проверяем перед скачиванием (БД + in-memory) ---
    cached = await data_cache_get_async(reg_code, dat_list)
    if cached is not None:
        cards, errors = cached
        logger.info(
            f"  {log_prefix}: из глобального кэша "
            f"({len(cards)} ДТП) [{data_cache.stats()}]"
        )
        return cards, errors

    cards: list[dict] = []
    errors: list[str] = []

    # Если API уже помечен как недоступный — сразу на web_fallback
    if _is_api_down():
        logger.info(
            f"  {log_prefix}: API ГИБДД помечен как недоступный, "
            f"сразу на сайт ({len(dat_list)} мес)"
        )
        from web_fallback import fetch_dtp_via_web_period
        fb_cards, fb_errors = await fetch_dtp_via_web_period(
            dat_list, reg_code,
            log_prefix=f"{log_prefix} [сайт]",
            progress_callback=progress_callback,
        )
        cards.extend(fb_cards)
        errors.extend(fb_errors)
    else:
        import httpx as _httpx
        use_web_fallback = False

        for i, dat in enumerate(dat_list, start=1):
            month_num = int(dat.split(".")[0])
            month_name = MONTH_FULL.get(month_num, dat)
            year = dat.split(".")[1]

            if progress_callback:
                await progress_callback(i, len(dat_list), month_name, year)

            if not use_web_fallback:
                # --- Основной метод: API ГИБДД ---
                try:
                    api_response = await fetch_dtp_data(dat=dat, reg=reg_code, pok="1")
                    extracted = extract_accident_cards(api_response)
                    cards.extend(extracted)
                    logger.info(f"  {log_prefix}: {dat} -> {len(extracted)} ДТП")
                except _httpx.HTTPStatusError as e:
                    status = e.response.status_code
                    if status >= 500:
                        _mark_api_down()  # запоминаем на всю сессию
                        use_web_fallback = True
                        logger.warning(
                            f"  {log_prefix}: {dat} -> HTTP {status}, "
                            f"переключаюсь на запасной метод (сайт ГИБДД)"
                        )
                        if notify_callback:
                            try:
                                await notify_callback(
                                    "\u26A0\uFE0F API ГИБДД недоступен (HTTP "
                                    f"{status}).\n"
                                    "Переключаюсь на запасной метод (сайт)..."
                                )
                            except Exception:
                                pass
                        remaining_dats = [dat] + dat_list[i:]
                        from web_fallback import fetch_dtp_via_web_period
                        fb_cards, fb_errors = await fetch_dtp_via_web_period(
                            remaining_dats, reg_code,
                            log_prefix=f"{log_prefix} [сайт]",
                            progress_callback=progress_callback,
                        )
                        cards.extend(fb_cards)
                        errors.extend(fb_errors)
                        break  # fallback обработал все оставшиеся месяцы
                    else:
                        # Клиентская ошибка — не ретраим
                        err_msg = f"{month_name} {year}: {error_brief(e)}"
                        errors.append(err_msg)
                        logger.error(
                            f"  {log_prefix}: {dat} -> ОШИБКА "
                            f"[{type(e).__name__}] {error_brief(e)}"
                        )
                except ConnectionError as e:
                    # Сетевая ошибка / таймаут — переключаемся на fallback
                    _mark_api_down()
                    use_web_fallback = True
                    logger.warning(
                        f"  {log_prefix}: {dat} -> {error_brief(e)}, "
                        f"переключаюсь на запасной метод (сайт ГИБДД)"
                    )
                    if notify_callback:
                        try:
                            await notify_callback(
                                "\u26A0\uFE0F API ГИБДД недоступен "
                                f"({error_brief(e)}).\n"
                                "Переключаюсь на запасной метод (сайт)..."
                            )
                        except Exception:
                            pass
                    remaining_dats = [dat] + dat_list[i:]
                    from web_fallback import fetch_dtp_via_web_period
                    fb_cards, fb_errors = await fetch_dtp_via_web_period(
                        remaining_dats, reg_code,
                        log_prefix=f"{log_prefix} [сайт]",
                        progress_callback=progress_callback,
                    )
                    cards.extend(fb_cards)
                    errors.extend(fb_errors)
                    break  # fallback обработал все оставшиеся месяцы
                except Exception as e:
                    err_msg = f"{month_name} {year}: {error_brief(e)}"
                    errors.append(err_msg)
                    logger.error(
                        f"  {log_prefix}: {dat} -> ОШИБКА "
                        f"[{type(e).__name__}] {error_brief(e)}"
                    )

    # --- Глобальный кэш: сохраняем результат (БД + in-memory) ---
    if cache_result and cards:
        await data_cache_put_async(reg_code, dat_list, cards, errors)

    return cards, errors


