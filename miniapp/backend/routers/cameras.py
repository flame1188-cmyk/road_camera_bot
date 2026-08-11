"""
Роутер для управления камерами фотовидеофиксации.

Файлы камер хранятся на диске в data/cameras_{reg_code}.xls
(см. camera_cache.py). Загружаются через Telegram-бота (legacy) или
через Mini App (POST /api/cameras/{reg_code}).

Endpoints:
- GET  /api/cameras                  — список регионов с загруженными камерами
- GET  /api/cameras/{reg_code}       — статус одного региона
- POST /api/cameras/{reg_code}       — загрузить .xls файл
- DELETE /api/cameras/{reg_code}     — удалить файл камер региона
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel

from ..telegram_auth import TelegramUser, get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cameras", tags=["cameras"])


# ============================================================
# Schemas
# ============================================================
class CameraRegionInfo(BaseModel):
    """Информация о загруженных камерах одного региона."""
    reg_code: str
    reg_name: Optional[str] = None
    has_file: bool
    file_size_bytes: int = 0
    file_modified: Optional[str] = None  # ISO datetime
    cameras_count: int = 0
    cameras_with_piket: int = 0


class CameraListResponse(BaseModel):
    """Ответ GET /api/cameras — список всех загруженных регионов."""
    regions: List[CameraRegionInfo]
    total_regions: int
    total_cameras: int


class CameraUploadResponse(BaseModel):
    """Ответ POST /api/cameras/{reg_code} — результат загрузки."""
    ok: bool
    reg_code: str
    file_size_bytes: int
    cameras_count: int
    cameras_with_piket: int
    message: str


# ============================================================
# Helpers
# ============================================================
def _import_camera_cache():
    """Ленивый импорт camera_cache из корня gibdd-bot."""
    import sys
    from pathlib import Path
    # Корень gibdd-bot — на 4 уровня выше этого файла
    # miniapp/backend/routers/cameras.py → gibdd-bot/
    root = Path(__file__).resolve().parents[4]
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    try:
        import camera_cache  # type: ignore
        return camera_cache
    except ImportError as exc:
        raise RuntimeError(
            f"Не удалось импортировать camera_cache. "
            f"PROJECT_ROOT={root}, sys.path[:3]={sys.path[:3]}. "
            f"Ошибка: {exc}"
        ) from exc


def _import_regions():
    """Ленивый импорт справочника регионов для имён."""
    try:
        from regions_builtin import BUILTIN_REGIONS  # type: ignore
        return {r["code"]: r["name"] for r in BUILTIN_REGIONS}
    except Exception:
        return {}


def _region_info(reg_code: str, with_parsing: bool = False) -> CameraRegionInfo:
    """Собирает информацию о камерах одного региона."""
    cc = _import_camera_cache()
    regions_names = _import_regions()

    has_file = cc.has_cached_cameras(reg_code)
    if not has_file:
        return CameraRegionInfo(
            reg_code=reg_code,
            reg_name=regions_names.get(reg_code),
            has_file=False,
        )

    # Размер и дата изменения файла
    path = cc._camera_filepath(reg_code)
    try:
        stat = os.stat(path)
        file_size = stat.st_size
        from datetime import datetime
        file_modified = datetime.fromtimestamp(stat.st_mtime).isoformat()
    except Exception:
        file_size = 0
        file_modified = None

    # Парсим камеру только если нужно (для отдельного GET региона)
    cameras_count = 0
    cameras_with_piket = 0
    if with_parsing:
        cameras = cc.load_cameras_from_cache(reg_code)
        if cameras:
            cameras_count = len(cameras)
            cameras_with_piket = sum(
                1 for c in cameras if c.get("has_piket")
            )

    return CameraRegionInfo(
        reg_code=reg_code,
        reg_name=regions_names.get(reg_code),
        has_file=True,
        file_size_bytes=file_size,
        file_modified=file_modified,
        cameras_count=cameras_count,
        cameras_with_piket=cameras_with_piket,
    )


# ============================================================
# Endpoints
# ============================================================
@router.get("", response_model=CameraListResponse)
async def list_cameras(
    user: TelegramUser = Depends(get_current_user),
):
    """
    Возвращает список регионов, для которых загружены файлы камер.

    Метрики (count, with_piket) НЕ считаются — это долго.
    Для подробной информации по региону используйте GET /api/cameras/{reg_code}.
    """
    cc = _import_camera_cache()
    codes = cc.list_cached_regions()
    regions = [_region_info(code, with_parsing=False) for code in codes]
    return CameraListResponse(
        regions=regions,
        total_regions=len(regions),
        total_cameras=0,  # не считаем в этом endpoint — слишком долго
    )


@router.get("/{reg_code}", response_model=CameraRegionInfo)
async def get_cameras_status(
    reg_code: str,
    user: TelegramUser = Depends(get_current_user),
):
    """
    Возвращает подробный статус камер региона (с парсингом и подсчётом).
    Если файл не загружен — has_file=False.
    """
    return _region_info(reg_code, with_parsing=True)


@router.post("/{reg_code}", response_model=CameraUploadResponse)
async def upload_cameras(
    reg_code: str,
    file: UploadFile = File(...),
    user: TelegramUser = Depends(get_current_user),
):
    """
    Загружает .xls файл с камерами для региона.

    Файл сохраняется в data/cameras_{reg_code}.xls и сразу парсится
    для проверки корректности.

    Ожидаемый формат файла: gibddrf_cameras_change_*.xls(x)
    (с сайта https://gibddrf.com/camera/).
    """
    # Читаем содержимое
    try:
        file_bytes = await file.read()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Не удалось прочитать файл: {exc}",
        )

    if not file_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Файл пустой",
        )

    # Лимит 50 MB (камеры по региону — обычно 1-10 MB)
    if len(file_bytes) > 50 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Файл слишком большой (лимит 50 MB)",
        )

    # Сохраняем через существующий camera_cache
    try:
        cc = _import_camera_cache()
        path = cc.save_camera_file(reg_code, file_bytes)
    except Exception as exc:
        logger.exception(f"Camera upload failed: reg_code={reg_code}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Не удалось сохранить файл: {exc}",
        )

    # Парсим для подсчёта и верификации
    try:
        cameras = cc.load_cameras_from_cache(reg_code)
        if cameras is None:
            # Файл сохранён, но не парсится — удаляем
            cc.delete_cached_cameras(reg_code)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Файл сохранён, но не удалось распарсить. "
                    "Проверьте, что это корректный .xls с камерами "
                    "(gibddrf_cameras_change_*.xls). Файл удалён."
                ),
            )
        cameras_count = len(cameras)
        cameras_with_piket = sum(1 for c in cameras if c.get("has_piket"))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Camera parse failed: reg_code={reg_code}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Файл сохранён, но ошибка при парсинге: {exc}",
        )

    logger.info(
        f"Camera upload OK: user={user.id}, reg_code={reg_code}, "
        f"size={len(file_bytes)}, cameras={cameras_count}, "
        f"with_piket={cameras_with_piket}"
    )

    return CameraUploadResponse(
        ok=True,
        reg_code=reg_code,
        file_size_bytes=len(file_bytes),
        cameras_count=cameras_count,
        cameras_with_piket=cameras_with_piket,
        message=(
            f"Загружено {cameras_count} камер "
            f"({cameras_with_piket} с пикетажем)"
        ),
    )


@router.delete("/{reg_code}")
async def delete_cameras(
    reg_code: str,
    user: TelegramUser = Depends(get_current_user),
):
    """Удаляет файл камер региона."""
    cc = _import_camera_cache()
    deleted = cc.delete_cached_cameras(reg_code)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Файл камер для региона {reg_code} не найден",
        )
    logger.info(f"Camera delete: user={user.id}, reg_code={reg_code}")
    return {"ok": True, "reg_code": reg_code, "deleted": True}
