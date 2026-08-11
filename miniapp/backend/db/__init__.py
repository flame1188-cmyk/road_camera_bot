"""
Слой работы с PostgreSQL.

Модули:
- connection: async-пул соединений (psycopg 3) с retry и health-check.
- schema.sql: CREATE TABLE IF NOT EXISTS для tasks, access_log, dtp_cards_cache.
- repository: TaskRepository — CRUD задач + аудит-лог,
  с transparent fallback на in-memory если БД недоступна.
- cards_cache: кэш карточек ДТП в БД (Этап 3) — замена in-memory LRU
  из data_cache.py на персистентное SQL-хранилище.

См. miniapp/README.md → «Переход на production-архитектуру».
"""
