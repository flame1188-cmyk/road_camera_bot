"""
Пайплайн выполнения задачи выгрузки: create → execute → DONE.

Содержит:
- create_task() — создание и регистрация новой задачи
- execute_task() / _execute_task_impl() — основной пайплайн:
  FETCHING → PARSING → ANALYTICS → GENERATING → DONE
- ensure_prev_cards() — lazy загрузка карточек прошлого года (АППГ)
- _parse_files_sync() / _task_dir() — хелперы

Использует Semaphore(3) для ограничения одновременных выгрузок к API ГИБДД.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import _imports
from .models import Task, TaskStatus
from .task_registry import (
    _gen_task_id,
    _now_utc,
    _register_task,
    _task_factory,  # noqa: F401 — re-exported для тестов
    get_task_async,
)

logger = logging.getLogger(__name__)


# === Фаза 1.1: Semaphore на одновременные выгрузки ===
# Ограничивает количество параллельно выполняемых execute_task().
# Почему 3: API ГИБДД при 5+ одновременных запросах с одного IP
# начинает возвращать 429/502; web-fallback (сайт stat.gibdd.ru) ещё
# хуже — там POST-генерация отчётов. 3 параллельные выгрузки —
# безопасный максимум. Остальные задачи ждут в очереди Semaphore.
# При росте до 30 пользователей можно увеличить до 5.
MAX_CONCURRENT_TASKS = 3
_EXECUTE_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_TASKS)


def create_task(
    user_id: int,
    region_code: str,
    region_name: str,
    period_label: str,
    dat_list: List[str],
    raw_query: str,
) -> Task:
    """Создаёт новую задачу и возвращает её объект.

    Задача сохраняется:
    - В in-memory _tasks (с LRU-eviction, см. _register_task)
    - В БД через repository.save_task (если DATABASE_URL задан)
    """
    task_id = _gen_task_id()
    task = Task(
        id=task_id,
        user_id=user_id,
        region_code=region_code,
        region_name=region_name,
        period_label=period_label,
        dat_list=dat_list,
        raw_query=raw_query,
    )
    _register_task(task)

    # Асинхронно сохраняем в БД (если доступна).
    # Fire-and-forget — задача уже в in-memory и доступна сразу.
    try:
        from ..db.repository import save_task
        asyncio.create_task(save_task(task))
    except Exception as exc:
        logger.debug(f"create_task: DB save skipped: {exc}")

    return task


def _task_dir(task_id: str) -> Path:
    """Директория для файлов задачи (в data/tasks/)."""
    d = _imports._PROJECT_ROOT / "data" / "tasks" / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _parse_files_sync(gibdd_parser, cards):
    """Синхронный хелпер для запуска в thread pool (Фаза 1.2).

    gibdd_parser.build_file1_data + build_file2_data — CPU-bound парсинг
    карточек. Объединены в одну функцию, чтобы вызвать через
    asyncio.to_thread() один раз (а не два).
    """
    file1_data = gibdd_parser.build_file1_data(cards)
    file2_data = gibdd_parser.build_file2_data(cards)
    return file1_data, file2_data


async def execute_task(task_id: str) -> None:
    """
    Асинхронное выполнение задачи выгрузки.

    Шаги:
    1. FETCHING — выгрузка карточек ДТП через bot._fetch_cards_for_period
       (внутри: API → web-fallback → кэш)
    2. PARSING — генерация Excel-данных через gibdd_parser
    3. ANALYTICS — расчёт метрик через analytics.calculate_metrics
    4. GENERATING — запись Excel-файлов и HTML-карты

    На каждом переходе статуса — сохранение в БД через repository.save_task
    (если DATABASE_URL задан; иначе работает только in-memory).

    === Фаза 1.1: Semaphore на одновременные выгрузки ===
    Без ограничения 10 одновременных пользователей запустят 10 параллельных
    пайплайнов, каждый делает 12 HTTP-запросов к API ГИБДД → 120 запросов
    с одного IP → 429/502 блокировки. С Semaphore(3) — максимум 3
    одновременных выгрузки, остальные ждут в очереди (пользователь видит
    прогресс через polling статуса = FETCHING).
    """
    # Таймаут 600 сек (10 мин) — если задача зависла, отпускаем semaphore.
    # Обычно выгрузка занимает 30-60 сек, 10 мин — щедрый запас.
    try:
        async with _EXECUTE_SEMAPHORE:
            # === Фаза 1.6: Prometheus metrics ===
            from ..middleware.metrics import task_started, task_finished
            task_started()
            try:
                await _execute_task_impl(task_id)
            finally:
                task_finished()
    except Exception as exc:
        logger.exception(f"Task {task_id} failed (semaphore-wrapped)")
        from .task_registry import _tasks
        task = _tasks.get(task_id)
        if task:
            task.status = TaskStatus.FAILED
            task.error = str(exc)
            task.updated_at = _now_utc()
            # Метрика: задача упала
            from ..middleware.metrics import record_task_status
            record_task_status("failed")


async def _execute_task_impl(task_id: str) -> None:
    """Реализация execute_task (вызывается под Semaphore)."""
    from .task_registry import _tasks

    task = _tasks.get(task_id)
    if not task:
        # Возможно, задача создана в другом воркере и есть в БД.
        task = await get_task_async(task_id)
        if not task:
            return
        _tasks[task_id] = task

    # Локальный helper дляpersist-апдейтов
    async def _persist() -> None:
        try:
            from ..db.repository import save_task
            await save_task(task)
        except Exception as exc:
            logger.debug(f"execute_task: persist failed: {exc}")

    try:
        # === 1. FETCHING ===
        task.status = TaskStatus.FETCHING
        task.progress = 10
        task.updated_at = _now_utc()
        await _persist()

        bot_module = _imports._import_module("bot")

        # Используем существующую функцию выгрузки из bot.py.
        # _fetch_cards_for_period уже умеет: API → web_fallback → кэш.
        cards, errors = await bot_module._fetch_cards_for_period(
            dat_list=task.dat_list,
            reg_code=task.region_code,
            log_prefix=f"MiniApp[{task_id}]",
            cache_result=True,
        )

        if errors:
            logger.warning(
                f"Task {task_id}: выгрузка завершена с ошибками: {errors}"
            )

        if not cards:
            task.status = TaskStatus.FAILED
            task.error = (
                "Не удалось получить данные ДТП. "
                f"Ошибки: {'; '.join(errors[:3]) if errors else 'нет данных'}"
            )
            task.updated_at = _now_utc()
            await _persist()
            return

        # Сводная статистика для отображения
        task.total_dtp = len(cards)
        task.total_dead = sum(int(c.get("pog", 0) or 0) for c in cards)
        task.total_injured = sum(int(c.get("ran", 0) or 0) for c in cards)

        # Сохраняем сырые карточки для последующего анализа
        # (очаги, статистика по точке, LLM-анализ)
        task.cards = cards

        # === 2. PARSING ===
        task.status = TaskStatus.PARSING
        task.progress = 45
        task.updated_at = _now_utc()
        await _persist()

        gibdd_parser = _imports._import_module("gibdd_parser")
        # gibdd_parser.build_file1/2_data — синхронный CPU-bound парсинг
        # 1500-3000 карточек. Выносим в thread pool, чтобы не блокировать
        # event loop (Фаза 1.2).
        file1_data, file2_data = await asyncio.to_thread(
            _parse_files_sync, gibdd_parser, cards
        )

        # === 3. ANALYTICS ===
        task.status = TaskStatus.ANALYTICS
        task.progress = 65
        task.updated_at = _now_utc()
        await _persist()

        try:
            analytics_module = _imports._import_module("analytics")

            # Лучше-эффort загрузка карточек прошлого года для сравнения АППГ.
            # Если prev_cards не загрузились — analytics всё равно валиден,
            # но без блока comparison/previous.
            try:
                if not task.prev_cards_loaded:
                    await ensure_prev_cards(task)
                prev_cards = task.prev_cards or []
                prev_label = task.prev_label
            except Exception as exc:
                logger.warning(
                    f"Task {task_id}: prev_cards load for analytics failed: "
                    f"{exc} — analytics without comparison"
                )
                prev_cards = []
                prev_label = None

            task.analytics = analytics_module.build_full_analytics(
                cards,
                prev_cards if prev_cards else None,
                prev_label,
            )
            # Добавляем current_label для UI
            if isinstance(task.analytics, dict):
                task.analytics["current_label"] = task.period_label
            logger.info(
                f"Task {task_id}: analytics built — "
                f"current={len(cards)} ДТП, "
                f"prev={'нет' if not prev_cards else f'{len(prev_cards)} ДТП'}"
            )
        except Exception as exc:
            logger.warning(f"Task {task_id}: analytics failed: {exc}")
            task.analytics = {
                "total_dtp": task.total_dtp,
                "total_dead": task.total_dead,
                "total_injured": task.total_injured,
                "has_prev_data": False,
            }
        await _persist()  # сохраняем analytics в БД

        # === 4. GENERATING ===
        task.status = TaskStatus.GENERATING
        task.progress = 80
        task.updated_at = _now_utc()
        await _persist()

        out_dir = _task_dir(task_id)
        region_safe = "".join(
            c if c.isalnum() else "_" for c in task.region_name
        )[:30] or task.region_code
        period_safe = "".join(
            c if c.isalnum() else "_" for c in task.period_label
        )[:20]

        # Excel: карточки ДТП + участники (генерируем оба файла одной функцией)
        #
        # === Этап 5: проверяем кэш готовых Excel-байтов в PostgreSQL ===
        # Ключ (reg_code, dat_hash) совпадает с ключом dtp_cards_cache —
        # это безопасно: Excel — производное от cards, если cards
        # идентичны, то и Excel побайтово идентичен.
        #
        # Cache hit → пропускаем 5-8 сек excel_generator.generate_both_files()
        # и сразу пишем байты на диск. Cache miss → генерируем как раньше,
        # сохраняем в кэш для следующих пользователей.
        file1_bytes: Optional[bytes] = None
        file2_bytes: Optional[bytes] = None
        try:
            from ..db.excel_cache import get_cached_excel
            cached_excel = await get_cached_excel(
                reg_code=task.region_code,
                dat_list=task.dat_list,
            )
            if cached_excel is not None:
                file1_bytes, file2_bytes, _meta = cached_excel
                logger.info(
                    f"Task {task_id}: Excel loaded from cache — "
                    f"~{(len(file1_bytes) + len(file2_bytes)) // 1024} KB"
                )
        except Exception as exc:
            logger.debug(f"Task {task_id}: excel cache lookup failed: {exc}")

        # Если кэш промахнулся — генерируем Excel штатно (5-8 сек).
        #
        # === Фаза 1.2: генерация Excel в ThreadPool ===
        # openpyxl — синхронная библиотека, при генерации Файла 2
        # (3999 строк участников) занимает 5-6 сек и БЛОКИРУЕТ event loop.
        # При 5 одновременных пользователях каждый следующий ждёт суммы
        # времён всех предыдущих: 5 × 6 сек = 30 сек задержки.
        # asyncio.to_thread() выносит генерацию в thread pool — event loop
        # остаётся свободным для других запросов.
        if file1_bytes is None or file2_bytes is None:
            excel_gen = _imports._import_module("excel_generator")
            file1_bytes, file2_bytes = await asyncio.to_thread(
                excel_gen.generate_both_files,
                file1_data,
                file2_data,
            )

            # === Этап 5: сохраняем в кэш для следующих пользователей ===
            try:
                from ..db.excel_cache import put_cached_excel
                await put_cached_excel(
                    reg_code=task.region_code,
                    dat_list=task.dat_list,
                    file1_bytes=file1_bytes,
                    file2_bytes=file2_bytes,
                    total_dtp=task.total_dtp,
                    total_dead=task.total_dead,
                    total_injured=task.total_injured,
                    region_name=task.region_name,
                    period_label=task.period_label,
                )
            except Exception as exc:
                logger.debug(f"Task {task_id}: excel cache put failed: {exc}")

        cards_path = out_dir / f"dtp_cards_{region_safe}_{period_safe}.xlsx"
        cards_path.write_bytes(file1_bytes)
        task.files.append({
            "type": "dtp_cards",
            "filename": cards_path.name,
            "path": str(cards_path),
            "size_bytes": len(file1_bytes),
            "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        })

        uch_path = out_dir / f"dtp_uch_{region_safe}_{period_safe}.xlsx"
        uch_path.write_bytes(file2_bytes)
        task.files.append({
            "type": "dtp_participants",
            "filename": uch_path.name,
            "path": str(uch_path),
            "size_bytes": len(file2_bytes),
            "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        })

        # HTML-карта через ReportGenerator
        try:
            report_gen_module = _imports._import_module("report_generator")

            # Подгружаем камеры из кэша, если они есть для этого региона.
            # Файл должен лежать в data/cameras_{reg_code}.xls
            # (загружается через Telegram-бота или через Mini App UI).
            cameras = None
            try:
                camera_cache_module = _imports._import_module("camera_cache")
                if camera_cache_module.has_cached_cameras(task.region_code):
                    cameras = camera_cache_module.load_cameras_from_cache(
                        task.region_code
                    )
                    if cameras:
                        with_pk = sum(
                            1 for c in cameras if c.get("has_piket")
                        )
                        logger.info(
                            f"Task {task_id}: loaded {len(cameras)} cameras "
                            f"({with_pk} with piket) for region "
                            f"{task.region_code}"
                        )
                    else:
                        logger.warning(
                            f"Task {task_id}: camera file exists for "
                            f"{task.region_code} but parser returned empty"
                        )
            except Exception as exc:
                logger.warning(
                    f"Task {task_id}: camera cache load failed: {exc} "
                    f"— building map without cameras"
                )
                cameras = None

            generator = report_gen_module.ReportGenerator(
                region_name=task.region_name,
                period_label=task.period_label,
            )
            # Передаём карточки прошлого года, чтобы на карте появилась
            # динамика АППГ в верхней сводке (ДТП / Погибшие / Раненые).
            prev_cards_for_map = task.prev_cards or None
            html_content = generator.generate_dtp_map(
                cards,
                cameras=cameras,
                prev_cards=prev_cards_for_map,
                prev_label=task.prev_label,
            )

            map_path = out_dir / f"dtp_map_{region_safe}_{period_safe}.html"
            map_path.write_text(html_content, encoding="utf-8")
            task.files.append({
                "type": "map_html",
                "filename": map_path.name,
                "path": str(map_path),
                "size_bytes": len(html_content.encode("utf-8")),
                "mime": "text/html",
            })
        except Exception as exc:
            logger.warning(f"Task {task_id}: map generation failed: {exc}")
            # Карта опциональна — задача считается успешной без неё

        # === DONE ===
        task.status = TaskStatus.DONE
        task.progress = 100
        task.updated_at = _now_utc()
        await _persist()

        # === Фаза 1.6: Prometheus metric — задача завершена успешно ===
        try:
            from ..middleware.metrics import record_task_status
            record_task_status("done")
        except Exception:
            pass

        logger.info(
            f"Task {task_id} done: {task.total_dtp} ДТП, "
            f"{task.total_dead} погибших, {task.total_injured} раненых, "
            f"{len(task.files)} файлов"
        )

    except Exception as exc:
        logger.exception(f"Task {task_id} failed")
        task.status = TaskStatus.FAILED
        task.error = str(exc)
        task.updated_at = _now_utc()
        await _persist()

        # === Фаза 1.6: Prometheus metric — задача упала ===
        try:
            from ..middleware.metrics import record_task_status
            record_task_status("failed")
        except Exception:
            pass


# ============================================================
# Загрузка данных за прошлый год (lazy)
# ============================================================
async def ensure_prev_cards(task: Task) -> Dict[str, Any]:
    """
    Гарантирует, что task.prev_cards загружены.

    Возвращает:
        {
            "ok": True,
            "prev_cards": [...],
            "prev_label": "...",
        }
        или {"ok": False, "error": "..."}
    """
    if task.prev_cards_loaded:
        return {
            "ok": bool(task.prev_cards),
            "prev_cards": task.prev_cards,
            "prev_label": task.prev_label or "",
            "error": None if task.prev_cards else "Нет данных за прошлый год",
        }

    # Вычисляем прошлый период: те же месяцы, год-1
    # dat_list = ['1.2026', '2.2026', ...] -> ['1.2025', '2.2025', ...]
    prev_dat_list = []
    for dat in task.dat_list:
        try:
            m, y = dat.split(".")
            prev_dat_list.append(f"{m}.{int(y) - 1}")
        except Exception:
            continue

    if not prev_dat_list:
        task.prev_cards_loaded = True
        return {"ok": False, "error": "Не удалось вычислить прошлый период"}

    # Формируем label прошлого периода
    try:
        year = int(task.dat_list[0].split(".")[1])
        prev_year = year - 1
        prev_label = task.period_label.replace(str(year), str(prev_year))
    except Exception:
        prev_label = f"Прошлый период ({prev_dat_list[0]})"

    try:
        bot_module = _imports._import_module("bot")
        prev_cards, errors = await bot_module._fetch_cards_for_period(
            dat_list=prev_dat_list,
            reg_code=task.region_code,
            log_prefix=f"MiniApp[{task.id}]/prev",
            cache_result=True,
        )
        task.prev_cards = prev_cards or []
        task.prev_label = prev_label
        task.prev_cards_loaded = True

        if errors:
            logger.warning(
                f"Task {task.id}: prev cards loaded with errors: {errors}"
            )

        return {
            "ok": bool(task.prev_cards),
            "prev_cards": task.prev_cards,
            "prev_label": prev_label,
            "error": None if task.prev_cards else (
                f"Нет данных за прошлый год ({prev_label}). "
                f"Возможно, данные ещё не опубликованы."
            ),
        }
    except Exception as exc:
        logger.exception(f"Task {task.id}: ensure_prev_cards failed")
        task.prev_cards_loaded = True  # не пытаемся снова
        return {"ok": False, "error": str(exc)}


# ============================================================
# Sprint 3.1: Восстановление task.cards из cards_cache
# ============================================================
async def ensure_cards(task: Task) -> Dict[str, Any]:
    """
    Гарантирует, что task.cards загружены.

    Проблема, которую решает эта функция:
      После рестарта контейнера или LRU eviction задачи из _tasks,
      тяжёлые поля (cards, prev_cards) теряются. attach_heavy_state()
      читает из _TASKS_HEAVY_STATE, но это in-memory кэш — после
      рестарта он пуст. cards_cache (PostgreSQL) при этом может быть
      жив (TTL=7 дней), но никто его не читает для восстановления
      task.cards при get_task_async().

      Результат: пользователь открывает старую задачу → task.cards=[]
      → ensure_comparison падает с "Карточки текущего периода не загружены"
      → LLM summary / clusters / point stats не работают.

    Решение:
      Вызывать _fetch_cards_for_period (он сам идёт в cards_cache → HIT,
      или скачивает заново → PUT в кэш). Это дешёвая операция при cache hit
      (<50 мс), и она восстанавливает task.cards до рабочего состояния.

    Возвращает:
        {"ok": True, "cards": [...]}
        или {"ok": False, "error": "..."}
    """
    # Быстрый путь: cards уже есть
    if task.cards:
        return {"ok": True, "cards": task.cards}

    # Если задача ещё в статусе FETCHING/PARSING/ANALYTICS — не вмешиваемся,
    # pipeline.execute_task сам заполнит task.cards. Иначе можем перезаписать
    # данные в процессе их загрузки.
    if task.status in (TaskStatus.FETCHING, TaskStatus.PARSING, TaskStatus.ANALYTICS):
        return {
            "ok": False,
            "error": (
                f"Задача ещё выполняется (статус={task.status.value}), "
                f"карточки загружаются. Попробуйте через несколько секунд."
            ),
        }

    # Если задача упала на этапе выгрузки — нет смысла пытаться снова
    # (cards всё равно не скачаются). Возвращаем понятную ошибку.
    if task.status == TaskStatus.FAILED:
        return {
            "ok": False,
            "error": (
                f"Задача завершилась с ошибкой: {task.error or 'неизвестная'}. "
                f"Создайте новую задачу для этого региона."
            ),
        }

    # Восстанавливаем cards из cards_cache (или скачиваем заново)
    try:
        bot_module = _imports._import_module("bot")
        cards, errors = await bot_module._fetch_cards_for_period(
            dat_list=task.dat_list,
            reg_code=task.region_code,
            log_prefix=f"MiniApp[{task.id}]/restore",
            cache_result=True,
        )

        if not cards:
            return {
                "ok": False,
                "error": (
                    "Не удалось восстановить карточки ДТП. "
                    f"Ошибки: {'; '.join(errors[:3]) if errors else 'нет данных'}. "
                    "Создайте новую задачу для этого региона."
                ),
            }

        # Восстанавливаем сводные поля (могут быть пустыми после load_task)
        if not task.total_dtp:
            task.total_dtp = len(cards)
            task.total_dead = sum(int(c.get("pog", 0) or 0) for c in cards)
            task.total_injured = sum(int(c.get("ran", 0) or 0) for c in cards)

        task.cards = cards

        logger.info(
            f"Task {task.id}: cards restored from cache/API — "
            f"{len(cards)} ДТП, region={task.region_code}"
        )

        # Сбрасываем in-memory кэш analytics-расчётов, т.к. id(cards)
        # изменился. Иначе ensure_comparison может думать, что кэш валиден
        # (сравнивая id(task.cards) с cross_tables_cards_id), но cards
        # теперь другой объект.
        task.cross_tables = None
        task.cross_tables_cards_id = None
        task.current_metrics = None
        task.current_metrics_cards_id = None
        task.comparison = None  # пересчитать comparison с новыми cards

        return {"ok": True, "cards": task.cards}

    except Exception as exc:
        logger.exception(f"Task {task.id}: ensure_cards failed")
        return {"ok": False, "error": str(exc)}
