-- ============================================================
-- Схема БД для GIBDD Mini App (Этап 2: tasks + access_log)
-- ============================================================
-- Запуск: python -m miniapp.backend.db.init_schema
-- Все запросы идемпотентны (IF NOT EXISTS), можно пере-запускать.
-- ============================================================

-- ============================================================
-- tasks: метаданные задач выгрузки (персистентное хранилище
-- вместо in-memory _tasks: dict в gibdd_service.py).
--
-- Тяжёлые поля (cards, prev_cards, raw_clusters) НЕ хранятся в БД —
-- они остаются in-memory или пере-вычисляются при необходимости.
-- В БД хранится только то, что нужно для:
--   1. Отображения задачи в UI (статус, прогресс, totals, files)
--   2. Истории задач пользователя (list_user_tasks)
--   3. Аудита обращений к ПДн (152-ФЗ)
-- ============================================================
CREATE TABLE IF NOT EXISTS tasks (
    id              VARCHAR(32)   PRIMARY KEY,
    user_id         BIGINT        NOT NULL,
    region_code     VARCHAR(16)   NOT NULL,
    region_name     TEXT          NOT NULL,
    period_label    TEXT          NOT NULL,
    dat_list        JSONB         NOT NULL,    -- ["1.2026", "2.2026", ...]
    raw_query       TEXT,
    status          VARCHAR(32)   NOT NULL DEFAULT 'pending',
    progress        INT           NOT NULL DEFAULT 0,
    error           TEXT,
    total_dtp       INT           NOT NULL DEFAULT 0,
    total_dead      INT           NOT NULL DEFAULT 0,
    total_injured   INT           NOT NULL DEFAULT 0,
    files           JSONB         NOT NULL DEFAULT '[]'::jsonb,
    analytics       JSONB,                     -- результат analytics (опционально)
    clusters_result JSONB,                     -- результат clusters_state.result (опционально)
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- История задач пользователя (самые свежие наверху)
CREATE INDEX IF NOT EXISTS idx_tasks_user_created
    ON tasks(user_id, created_at DESC);

-- Для очистки старых задач по возрасту
CREATE INDEX IF NOT EXISTS idx_tasks_created_at
    ON tasks(created_at);

-- ============================================================
-- access_log: аудит обращений к ПДн (требование 152-ФЗ).
-- Каждая запись = одно действие пользователя:
--   - create_task: создал задачу выгрузки
--   - download_file: скачал Excel/HTML
--   - view_clusters: открыл вкладку «Очаги»
--   - view_point_stats: запросил статистику по точке
--   - llm_query: задал вопрос LLM по данным
-- ============================================================
CREATE TABLE IF NOT EXISTS access_log (
    id              BIGSERIAL     PRIMARY KEY,
    user_id         BIGINT        NOT NULL,
    region_code     VARCHAR(16),
    period_label    TEXT,
    action          VARCHAR(64)   NOT NULL,
    task_id         VARCHAR(32),
    details         JSONB,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_access_log_user_id
    ON access_log(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_access_log_created_at
    ON access_log(created_at DESC);

-- ============================================================
-- updated_at триггер для tasks (авто-обновление при UPDATE)
-- ============================================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_tasks_updated_at ON tasks;
CREATE TRIGGER trg_tasks_updated_at
    BEFORE UPDATE ON tasks
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();


-- ============================================================
-- dtp_cards_cache: кэш карточек ДТП в PostgreSQL (Этап 3).
-- Заменяет in-memory LRU из data_cache.py на персистентное хранилище,
-- разделяемое между всеми воркерами и переживающее рестарт.
--
-- Ключ кэша: (reg_code, dat_hash) где
--   dat_hash = MD5 от отсортированного списка "m.YYYY" дат,
--   склеенных через ','. Пример:
--     dat_list = ["1.2026", "2.2026"] → dat_hash = MD5("1.2026,2.2026")
--
-- Это даёт стабильный ключ, не зависящий от порядка месяцев в массиве
-- (сортируем перед хэшированием), и позволяет использовать в кэше
-- составные запросы за несколько периодов сразу.
--
-- TTL: expires_at = created_at + TTL_SECONDS (по умолчанию 1 час).
-- Записи с expires_at < NOW() считаются протухшими и игнорируются
-- при SELECT. Физическая очистка — через cleanup_old_cards() или
-- background job (см. db/cards_cache.py).
-- ============================================================
CREATE TABLE IF NOT EXISTS dtp_cards_cache (
    id              BIGSERIAL    PRIMARY KEY,
    reg_code        VARCHAR(16)  NOT NULL,
    dat_hash        CHAR(32)     NOT NULL,            -- MD5 hash
    dat_list        JSONB        NOT NULL,            -- ["1.2026","2.2026",...] для диагностики
    payload         JSONB        NOT NULL,            -- список карточек ДТП
    errors          JSONB        NOT NULL DEFAULT '[]'::jsonb,  -- ошибки выгрузки
    total_cards     INT          NOT NULL DEFAULT 0,
    source          VARCHAR(16)  NOT NULL DEFAULT 'api',  -- 'api' | 'web_fallback'
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ  NOT NULL
);

-- Уникальный индекс: одна запись на (reg_code, dat_hash).
-- На INSERT конфликтов (DO UPDATE) обновляем payload/expires_at.
CREATE UNIQUE INDEX IF NOT EXISTS uq_dtp_cards_cache_reg_dat
    ON dtp_cards_cache(reg_code, dat_hash);

-- Для самого частого запроса:
--   SELECT ... WHERE reg_code=%s AND dat_hash=%s AND expires_at > NOW()
-- ВАЖНО: НЕ используем partial index (WHERE expires_at > NOW()),
-- потому что NOW() — функция STABLE, а не IMMUTABLE. PostgreSQL
-- запрещает STABLE-функции в предикате partial index:
--   ERROR: functions in index predicate must be marked IMMUTABLE
-- Это рушит весь init_pool() и переводит приложение в in-memory fallback.
-- Обычный композитный индекс тоже эффективен: фильтр по expires_at > NOW()
-- применяется после индексного поиска по (reg_code, dat_hash) — для одной
-- записи это O(1).
CREATE INDEX IF NOT EXISTS idx_dtp_cards_cache_reg_dat_expires
    ON dtp_cards_cache(reg_code, dat_hash, expires_at);

-- Для cleanup_old_cards() — быстрый поиск протухших записей.
CREATE INDEX IF NOT EXISTS idx_dtp_cards_cache_expires
    ON dtp_cards_cache(expires_at);

-- Для invalidate_by_region — быстрое удаление всех записей региона.
CREATE INDEX IF NOT EXISTS idx_dtp_cards_cache_reg
    ON dtp_cards_cache(reg_code);


-- ============================================================
-- clusters_cache: кэш очагов концентрации ДТП (Этап 4).
-- Хранит финальный сериализованный result расчёта очагов
-- (clusters_state.result), чтобы повторные запросы по тому же
-- региону+периоду не пересчитывали 15-30 секунд.
--
-- Ключ кэша: (reg_code, current_dat_hash, prev_dat_hash) где
--   current_dat_hash = MD5(sorted(current_dat_list).join(','))
--   prev_dat_hash    = MD5(sorted(prev_dat_list).join(',')) или NULL
--
-- Размер записи: 1-3 MB (result ~50-200 KB + raw_clusters с cards
-- внутри ~1-2 MB + raw_preclusters ~0.5-1 MB). Кэшируем raw_clusters
-- и raw_preclusters, иначе при cache hit не работают:
--   - generate_clusters_map_html (продвинутая карта со слоями/попапами)
--   - generate_clusters_excel (4 листа с детализацией ДТП)
-- Оба метода итерируют cluster["cards"] — без кэша raw_clusters они
-- падают в fallback (простая карта без слоёв / None вместо Excel).
--
-- TTL: expires_at = created_at + TTL_SECONDS (по умолчанию 6 часов,
--      настраивается через env CLUSTERS_CACHE_TTL_SECONDS).
--
-- Что НЕ кэшируется:
--   - HTML-карта — генерируется из raw_clusters при запросе.
--   - OSM-полигоны — кэшируются отдельно в data/osm_cache/.
-- ============================================================
CREATE TABLE IF NOT EXISTS clusters_cache (
    id                  BIGSERIAL    PRIMARY KEY,
    reg_code            VARCHAR(16)  NOT NULL,
    current_dat_hash    CHAR(32)     NOT NULL,
    prev_dat_hash       CHAR(32),                          -- NULL если без АППГ
    current_dat_list    JSONB        NOT NULL,             -- ["1.2026","2.2026",...]
    prev_dat_list       JSONB,                             -- ["1.2025","2.2025",...] или NULL
    payload             JSONB        NOT NULL,             -- финальный result (clusters_state.result)
    raw_clusters        JSONB,                             -- сырые очаги с cards внутри (для карты/Excel)
    raw_preclusters     JSONB,                             -- сырые предочаги с cards внутри
    total_clusters      INT          NOT NULL DEFAULT 0,
    total_preclusters   INT          NOT NULL DEFAULT 0,
    has_prev_data       BOOLEAN      NOT NULL DEFAULT FALSE,
    current_label       TEXT,
    prev_label          TEXT,
    region_name         TEXT,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at          TIMESTAMPTZ  NOT NULL
);

-- ============================================================
-- Миграция: для уже существующих таблиц добавляем новые колонки.
-- ADD COLUMN IF NOT EXISTS поддерживается PostgreSQL 9.6+.
-- Идемпотентно — безопасно при каждом запуске приложения.
-- ============================================================
ALTER TABLE clusters_cache ADD COLUMN IF NOT EXISTS raw_clusters    JSONB;
ALTER TABLE clusters_cache ADD COLUMN IF NOT EXISTS raw_preclusters JSONB;

-- Уникальный индекс на составной ключ с COALESCE для NULL-безопасности.
-- Важно: prev_dat_hash может быть NULL (АППГ не используется),
-- и без COALESCE уникальность NULL-NULL не сработала бы (NULL != NULL
-- в SQL). Используем COALESCE(prev_dat_hash, '') для индекса.
CREATE UNIQUE INDEX IF NOT EXISTS uq_clusters_cache_keys
    ON clusters_cache(reg_code, current_dat_hash,
                      COALESCE(prev_dat_hash, ''::text));

-- Для частого запроса GET:
--   WHERE reg_code=? AND current_dat_hash=? AND prev_dat_hash=?
--   AND expires_at > NOW()
CREATE INDEX IF NOT EXISTS idx_clusters_cache_keys_expires
    ON clusters_cache(reg_code, current_dat_hash, prev_dat_hash, expires_at);

-- Для cleanup_old_clusters — быстрый поиск протухших.
CREATE INDEX IF NOT EXISTS idx_clusters_cache_expires
    ON clusters_cache(expires_at);

-- Для invalidate_by_region — быстрое удаление всех записей региона.
CREATE INDEX IF NOT EXISTS idx_clusters_cache_reg
    ON clusters_cache(reg_code);


-- ============================================================
-- excel_cache: кэш готовых Excel-файлов в PostgreSQL (Этап 5).
-- Хранит байты двух файлов (Файл 1 «ДТП» + Файл 2 «Участники»),
-- чтобы повторные запросы по тому же региону+периоду не пересчитывали
-- 5-8 секунд excel_generator.generate_both_files().
--
-- Ключ кэша: (reg_code, dat_hash) — СОВПАДАЕТ с ключом dtp_cards_cache.
-- Это не случайно: Excel — производное от cards (через gibdd_parser),
-- поэтому одинаковый ключ гарантирует консистентность. Если cards
-- инвалидированы — Excel тоже нужно инвалидировать (см. хук в cards_cache).
--
-- Размер записи: 1-2 MB (Файл 1 ~500 KB + Файл 2 ~1 MB).
-- TTL: expires_at = created_at + TTL_SECONDS (по умолчанию 24 часа,
--      настраивается через env EXCEL_CACHE_TTL_SECONDS).
--
-- Что НЕ кэшируется:
--   - HTML-карта ДТП (generate_dtp_map) — генерируется по запросу.
--   - Excel «Очаги» (generate_clusters_excel) — ключ другой:
--     (reg_code, current_dat_hash, prev_dat_hash), кэш в clusters_cache.
--   - analytics JSON — малый объём, генерируется быстро (~50 мс),
--     кэшируется в tasks.analytics (persist в БД).
-- ============================================================
CREATE TABLE IF NOT EXISTS excel_cache (
    id              BIGSERIAL    PRIMARY KEY,
    reg_code        VARCHAR(16)  NOT NULL,
    dat_hash        CHAR(32)     NOT NULL,            -- MD5 hash (как в dtp_cards_cache)
    dat_list        JSONB        NOT NULL,            -- ["1.2026","2.2026",...] для диагностики
    file1_bytes     BYTEA        NOT NULL,            -- Файл 1 (ДТП) — XLSX
    file2_bytes     BYTEA        NOT NULL,            -- Файл 2 (участники) — XLSX
    file1_size      INT          NOT NULL DEFAULT 0,  -- размер Файла 1 в байтах
    file2_size      INT          NOT NULL DEFAULT 0,  -- размер Файла 2 в байтах
    total_dtp       INT          NOT NULL DEFAULT 0,
    total_dead      INT          NOT NULL DEFAULT 0,
    total_injured   INT          NOT NULL DEFAULT 0,
    region_name     TEXT,
    period_label    TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ  NOT NULL
);

-- Уникальный индекс: одна запись на (reg_code, dat_hash).
-- На INSERT конфликтов (DO UPDATE) обновляем file*_bytes/expires_at.
CREATE UNIQUE INDEX IF NOT EXISTS uq_excel_cache_reg_dat
    ON excel_cache(reg_code, dat_hash);

-- Для частого запроса GET:
--   WHERE reg_code=? AND dat_hash=? AND expires_at > NOW()
CREATE INDEX IF NOT EXISTS idx_excel_cache_reg_dat_expires
    ON excel_cache(reg_code, dat_hash, expires_at);

-- Для cleanup_old_excel — быстрый поиск протухших.
CREATE INDEX IF NOT EXISTS idx_excel_cache_expires
    ON excel_cache(expires_at);

-- Для invalidate_by_region — быстрое удаление всех записей региона.
CREATE INDEX IF NOT EXISTS idx_excel_cache_reg
    ON excel_cache(reg_code);


-- ============================================================
-- llm_cache — кэш LLM-резюме (Sprint 2)
-- ============================================================
-- Зачем: LLM-summary — самое дорогое место пайплайна (~53 сек,
-- 429 Too Many Requests при 3+ одновременных на free-тарифе).
-- Повторный запрос того же региона+периода+провайдера+промпта
-- даёт байтово идентичный summary — кэшируем, чтобы:
--   - 2-й пользователь получал ответ мгновенно (<100 мс)
--   - не тратилась quota LLM
--   - снижался риск 429
--
-- Ключ cache_key = SHA-256 от:
--   reg_code | dat_hash | provider | prompt_hash | llm_version
--
-- prompt_hash = MD5 от (system_prompt + clusters_ctx + cross_tables_ctx)
-- Если меняется SYSTEM_PROMPT или формат таблиц — кэш инвалидируется
-- автоматически.
--
-- llm_version (env LLM_CACHE_VERSION) — позволяет принудительно
-- инвалидировать весь кэш при релизе новой версии промпта.
--
-- TTL: 24 часа (env LLM_CACHE_TTL_SECONDS).
-- ============================================================
CREATE TABLE IF NOT EXISTS llm_cache (
    id              BIGSERIAL    PRIMARY KEY,
    cache_key       CHAR(64)     NOT NULL,            -- SHA-256
    reg_code        VARCHAR(16)  NOT NULL,
    dat_hash        CHAR(32)     NOT NULL,            -- MD5 hash (как в dtp_cards_cache)
    provider        VARCHAR(16)  NOT NULL,            -- 'free' / 'paid'
    summary_text    TEXT         NOT NULL,
    prompt_hash     CHAR(32)     NOT NULL,            -- MD5 от промпта
    clusters_count  INT,                              -- для диагностики
    total_dtp       INT,                              -- для диагностики
    region_name     TEXT,
    period_label    TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ  NOT NULL
);

-- Уникальный индекс: одна запись на cache_key.
CREATE UNIQUE INDEX IF NOT EXISTS uq_llm_cache_key
    ON llm_cache(cache_key);

-- Для частого запроса GET по cache_key с проверкой expires_at.
CREATE INDEX IF NOT EXISTS idx_llm_cache_key_expires
    ON llm_cache(cache_key, expires_at);

-- Для cleanup_expired_llm_cache — быстрый поиск протухших.
CREATE INDEX IF NOT EXISTS idx_llm_cache_expires
    ON llm_cache(expires_at);

-- Для invalidate_by_region — быстрое удаление всех записей региона.
CREATE INDEX IF NOT EXISTS idx_llm_cache_reg
    ON llm_cache(reg_code);


-- ============================================================
-- llm_sessions: сохранение LLM-сессий пользователей (Sprint 6).
-- ============================================================
-- Зачем: после рестарта приложения task.llm_summary_state и
-- task.llm_qa_history терялись (in-memory только) — пользователь
-- открывал задачу и видел пустую историю, а резюме нужно было
-- перегенерировать. Sprint 6: персистим в БД, восстанавливаем
-- при первом обращении через get_task_async().
--
-- Ключ: task_id (1 сессия = 1 задача). user_id дублируется для
-- быстрой фильтрации списка сессий пользователя и авторизации.
--
-- Что хранится:
--   - summary_text + summary_provider + summary_generated_at:
--     финальный текст резюме, чтобы показать мгновенно без
--     перегенерации (даже если llm_cache протух, по task_id
--     резюме всё ещё доступно).
--   - qa_history JSONB: массив {question, answer, provider,
--     timestamp}, последние 10 (как в task.llm_qa_history).
--
-- Запросы:
--   - load_llm_session(task_id) — при открытии задачи (fast path).
--   - save_llm_session(task_id, summary, ...) — после генерации
--     резюме (upsert, перезаписывает summary).
--   - append_qa_entry(task_id, question, answer, provider) —
--     после каждого Q&A (atomic jsonb_insert, не перезаписывает
--     summary). Trim до 10 последних.
-- ============================================================
CREATE TABLE IF NOT EXISTS llm_sessions (
    task_id              VARCHAR(32)  PRIMARY KEY,
    user_id              BIGINT       NOT NULL,
    summary_text         TEXT,
    summary_provider     VARCHAR(16),
    summary_generated_at TIMESTAMPTZ,
    qa_history           JSONB        NOT NULL DEFAULT '[]'::jsonb,
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Для списка сессий пользователя (история).
CREATE INDEX IF NOT EXISTS idx_llm_sessions_user
    ON llm_sessions(user_id, updated_at DESC);

-- Триггер для авто-обновления updated_at (как в tasks).
DROP TRIGGER IF EXISTS trg_llm_sessions_updated_at ON llm_sessions;
CREATE TRIGGER trg_llm_sessions_updated_at
    BEFORE UPDATE ON llm_sessions
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
