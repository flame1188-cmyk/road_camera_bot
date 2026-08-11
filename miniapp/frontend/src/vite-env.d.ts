/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Базовый URL API. Пусто = тот же origin (для production). */
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
