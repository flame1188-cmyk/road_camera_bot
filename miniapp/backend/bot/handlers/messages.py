"""
messages.py — текстовые сообщения, локации, документы (Фаза 2.6 facade).

Handlers:
  handle_message           (~166 строк, 3788-3953) — текстовые сообщения
                                                       (парсинг языка + Q&A)
  _handle_document         (~184 строк, 3604-3787) — загрузка камер
  _handle_location_message (~21 строк, 3432-3452)  — геолокация для point stats
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[5])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


_ALLOWED = {
    "handle_message",
    "_handle_document",
    "_handle_location_message",
}


def __getattr__(name: str):
    if name in _ALLOWED:
        import bot as _b
        return getattr(_b, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
