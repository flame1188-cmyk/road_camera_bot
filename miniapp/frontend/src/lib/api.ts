/**
 * API-клиент для общения с FastAPI backend.
 *
 * Все запросы автоматически добавляют Telegram initData в заголовок
 * X-Tg-Init-Data — это нужно для проверки подписи на сервере.
 */
import { getInitData } from './telegram'

export const API_BASE = import.meta.env.VITE_API_BASE ?? ''

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
    public body?: unknown
  ) {
    super(detail)
    this.name = 'ApiError'
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const initData = getInitData()
  const headers = new Headers(options.headers)
  headers.set('X-Tg-Init-Data', initData)

  // Устанавливаем Content-Type: application/json ТОЛЬКО если body — строка
  // (JSON). Для FormData браузер сам поставит multipart/form-data с
  // правильным boundary — нельзя это перетирать.
  if (options.body && typeof options.body === 'string') {
    headers.set('Content-Type', 'application/json')
  }

  const url = `${API_BASE}${path}`
  const response = await fetch(url, {
    ...options,
    headers,
  })

  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    let body: unknown
    try {
      body = await response.json()
      detail = (body as { detail?: string })?.detail ?? detail
    } catch {
      // Не JSON — игнорируем
    }
    throw new ApiError(response.status, detail, body)
  }

  if (response.status === 204) {
    return undefined as T
  }

  const contentType = response.headers.get('content-type') ?? ''
  if (contentType.includes('application/json')) {
    return (await response.json()) as T
  }
  return (await response.text()) as unknown as T
}

/**
 * Скачивает бинарный файл (Excel, PDF, ...) через fetch с X-Tg-Init-Data,
 * преобразует в Blob и запускает браузерный download с заданным именем.
 *
 * Обычный <a download> не подходит, т.к. не передаёт custom headers.
 */
async function downloadBlobUrl(url: string, fallbackFilename: string): Promise<void> {
  const initData = getInitData()
  const headers = new Headers()
  headers.set('X-Tg-Init-Data', initData)

  const response = await fetch(`${API_BASE}${url}`, { headers })
  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try {
      const body = await response.json()
      detail = (body as { detail?: string })?.detail ?? detail
    } catch {
      // не JSON
    }
    throw new ApiError(response.status, detail)
  }

  const blob = await response.blob()

  // Имя файла из Content-Disposition (если сервер его прислал).
  // Поддерживаем RFC 5987: prefer filename*=UTF-8''... over ASCII filename=...
  let filename = fallbackFilename
  const cd = response.headers.get('content-disposition')
  if (cd) {
    // Сначала пробуем UTF-8 форму: filename*=UTF-8''<urlencoded>
    const utf8Match = cd.match(/filename\*=([^;]+)/i)
    if (utf8Match && utf8Match[1]) {
      const raw = utf8Match[1].trim()
      // Формат: UTF-8''<urlencoded>
      const m = raw.match(/^[^']*''(.+)$/i)
      if (m && m[1]) {
        try {
          filename = decodeURIComponent(m[1])
        } catch {
          filename = m[1]
        }
      }
    }
    // Fallback на ASCII форму: filename="..."
    if (filename === fallbackFilename) {
      const match = cd.match(/filename="?([^";]+)"?/i)
      if (match && match[1]) filename = match[1]
    }
  }

  // Создаём временный <a> и кликаем по нему
  const objectUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objectUrl
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  // Освобождаем память через небольшую задержку (для надёжности download)
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
}

// ============================================================
// Types
// ============================================================
export interface Region {
  code: string
  name: string
  title?: string
}

export interface ParseResult {
  ok: boolean
  region_code?: string
  region_name?: string
  period?: string
  raw_query: string
  error?: string
}

export type TaskStatus =
  | 'pending'
  | 'fetching'
  | 'parsing'
  | 'analytics'
  | 'generating'
  | 'done'
  | 'failed'

export interface TaskFile {
  type: string
  filename: string
  size_bytes: number
  mime: string
}

export interface TaskStatusResponse {
  task_id: string
  status: TaskStatus
  progress: number
  region_code: string
  region_name: string
  period: string
  error?: string | null
  files: TaskFile[]
  analytics?: Record<string, unknown> | null
}

// Структурированный запрос на создание задачи (без текстового парсинга)
export interface StructuredTaskRequest {
  region_code: string
  region_name: string
  dat_list: string[]          // ['1.2025', '2.2025', ...]
  period_label: string         // '2025 год' / 'I квартал 2025'
}

// ============================================================
// Cameras
// ============================================================
export interface CameraRegionInfo {
  reg_code: string
  reg_name: string | null
  has_file: boolean
  file_size_bytes: number
  file_modified: string | null
  cameras_count: number
  cameras_with_piket: number
}

export interface CameraListResponse {
  regions: CameraRegionInfo[]
  total_regions: number
  total_cameras: number
}

export interface CameraUploadResponse {
  ok: boolean
  reg_code: string
  file_size_bytes: number
  cameras_count: number
  cameras_with_piket: number
  message: string
}

// ============================================================
// Analysis: Clusters / Point / LLM
// ============================================================
export type AnalysisStatus = 'idle' | 'running' | 'done' | 'failed'

export interface AnalysisStateResponse {
  status: AnalysisStatus
  progress: number
  stage: string
  error?: string | null
  started_at?: string | null
  finished_at?: string | null
}

export interface ClusterItem {
  road: string
  zone_type: string
  total_accidents: number
  deaths: number
  injured: number
  // None (смешанный тип, 5+ ДТП разных видов) приходит как null
  dominant_type: string | null
  type_counter: Record<string, number>
  center?: { lat: number; lon: number } | null
  start_pos?: number | null
  end_pos?: number | null
  dates: string[]
  dynamics: Record<string, any>
  camera_match?: Record<string, any> | null
  // Флаги для фильтрации: исчезнувший / АППГ-повторённый.
  // Нужны, чтобы исключать prev-очаги из «Топ-10 текущих».
  is_lost?: boolean
  is_prev_matched?: boolean
}

export interface ClustersSummary {
  total_clusters: number
  total_lost: number
  total_prev_matched?: number
  total_preclusters: number
  current_total_dtp: number
  current_deaths: number
  current_injured: number
  dynamics: Record<string, number>
  has_prev_data: boolean
  prev_label?: string | null
  current_label: string
  region_name: string
}

export interface ClustersResult {
  summary: ClustersSummary
  clusters: ClusterItem[]
  preclusters: ClusterItem[]
}

export interface ClustersResponse {
  state: AnalysisStateResponse
  result?: ClustersResult | null
}

export interface PointPeriodStats {
  total: number
  deaths: number
  injured: number
  alcohol: number
  pedestrians: number
  by_type: Record<string, number>
  by_road: Record<string, number>
  by_weather: Record<string, number>
  cards_count: number
  cards_preview: Array<{
    date: string
    time: string
    type: string
    road: string
    deaths: number
    injured: number
    dist_m: number
    lat: number
    lon: number
  }>
}

export interface PointStatsResponse {
  ok: boolean
  center: { lat: number; lon: number }
  radius_m: number
  current_label: string
  prev_label?: string | null
  current?: PointPeriodStats | null
  prev?: PointPeriodStats | null
  error?: string | null
}

export interface LLMProvidersResponse {
  free: boolean
  paid: boolean
  free_model: string
  paid_model: string
}

export interface LLMSummaryResult {
  text: string
  provider: string
  generated_at: string
}

export interface LLMSummaryResponse {
  state: AnalysisStateResponse
  result?: LLMSummaryResult | null
}

export interface LLMAskResponse {
  ok: boolean
  answer?: string | null
  provider?: string | null
  error?: string | null
}

export interface QAHistoryItem {
  question: string
  answer: string
  provider: string
  timestamp: string
}

// ============================================================
// НП БДД (Национальный проект «Безопасные качественные дороги»)
// ============================================================

export interface NpBddRegion {
  code: string
  name: string
}

export interface NpBddYearRecord {
  deaths: number
  vehicles: number
  tr: number
  frozen?: boolean
  frozen_at?: string
  source?: string
}

export type NpBddForecastMethod = 'central_only' | 'corridor'

export interface NpBddMonthlyChart {
  months: number[]
  tr_actual_cumulative: Record<string, number>
  tr_forecast_cumulative: Record<string, number>
  // Коридор прогноза (только для прогнозных месяцев; пусто, если corridor недоступен).
  tr_optimistic_cumulative?: Record<string, number>
  tr_pessimistic_cumulative?: Record<string, number>
  // Кумулятивные погибшие (для tooltip)
  deaths_actual_cumulative?: Record<string, number>
  deaths_forecast_cumulative?: Record<string, number>
  deaths_optimistic_cumulative?: Record<string, number>
  deaths_pessimistic_cumulative?: Record<string, number>
  plan_cumulative: Record<string, number>
  current_month: number
  plan_line_mode: 'linear' | 'horizontal'
  forecast_method?: NpBddForecastMethod
  corridor_available?: boolean
  seasonal_source?: 'per-region' | 'global' | 'legacy' | 'uniform' | 'unknown'
  seasonal_region_code?: string | null
  seasonal_samples_used?: number
}

export interface NpBddCurrentYear {
  year: number
  months_actual: number[]
  months_forecast: number[]
  deaths_by_month_actual: Record<string, number>
  deaths_ytd: number
  deaths_forecast_full_year: number
  // Коридор (null, если forecast_method='central_only' или corridor недоступен).
  deaths_forecast_optimistic?: number | null
  deaths_forecast_pessimistic?: number | null
  tr_actual_ytd: number
  tr_forecast_full_year: number
  tr_forecast_optimistic?: number | null
  tr_forecast_pessimistic?: number | null
  tr_plan: number
  monthly_chart: NpBddMonthlyChart
}

export interface NpBddKpi {
  tr_actual_ytd: number
  tr_forecast_full_year: number
  tr_forecast_optimistic?: number | null
  tr_forecast_pessimistic?: number | null
  tr_plan: number
  deviation_pct: number
  status: 'ok' | 'warning' | 'danger'
}

export interface NpBddSeasonalInfo {
  source: 'per-region' | 'global' | 'legacy' | 'uniform' | 'unknown'
  region_code: string | null
  samples_used: number
}

export interface NpBddData {
  region: { code: string; name: string }
  history: Record<string, NpBddYearRecord>
  current_year: NpBddCurrentYear
  plan_series: Record<string, number>
  kpi: NpBddKpi
  forecast_method?: NpBddForecastMethod
  corridor_available?: boolean
  corridor_years_used?: string[]
  seasonal?: NpBddSeasonalInfo
  calculated_at: string
}

export interface NpBddSettings {
  plan_line_mode: 'linear' | 'horizontal'
  forecast_method: NpBddForecastMethod
}

export interface NpBddFrozenYear {
  year: number
  tr: number
  deaths: number
  vehicles: number
  frozen_at?: string
  frozen_by?: string
  note?: string
}

// ============================================================
// API methods
// ============================================================

/**
 * Sprint 4: Универсальный SSE-клиент через fetch + ReadableStream.
 *
 * Почему НЕ EventSource:
 *  - EventSource не умеет передавать кастомные заголовки (нужен X-Tg-Init-Data)
 *  - EventSource поддерживает только GET (наш /ask/stream — POST)
 *  - EventSource авто-реконнектит, что плохо для стриминга LLM (дубликат ответов)
 *
 * Формат SSE, который мы парсим:
 *   event: delta\n
 *   data: текст токена\n
 *   \n
 *   event: done\n
 *   data: \n
 *   \n
 *   event: error\n
 *   data: {"error":"сообщение"}\n
 *   \n
 *   event: ping\n
 *   data: \n
 *   \n
 *
 * Поддерживает partial-чанки (TCP-фрагментация): накапливаем буфер,
 * режем по двойному \n\n (разделитель SSE-событий).
 */
async function consumeSSE(
  path: string,
  body: Record<string, unknown>,
  handlers: {
    onDelta: (text: string) => void
    onDone?: () => void
    onError?: (err: string, retryable?: boolean, errorType?: string) => void
  },
  signal?: AbortSignal,
): Promise<void> {
  const initData = getInitData()
  const headers = new Headers({
    'Content-Type': 'application/json',
    'X-Tg-Init-Data': initData,
    Accept: 'text/event-stream',
  })

  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
      signal,
    })
  } catch (err: any) {
    if (err?.name === 'AbortError') return
    handlers.onError?.(err?.message ?? 'Ошибка соединения')
    return
  }

  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try {
      const errBody = await response.json()
      detail = errBody?.detail ?? detail
    } catch {
      // не JSON
    }
    handlers.onError?.(detail)
    return
  }

  if (!response.body) {
    handlers.onError?.('Потоковый ответ не поддерживается')
    return
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      // Режем буфер по разделителю SSE-событий.
      // SSE spec допускает три разделителя: "\n\n", "\r\n\r\n", "\r\r".
      // sse_starlette по умолчанию использует "\r\n" → разделитель "\r\n\r\n".
      // Sprint 4 FIX: используем regex /\r?\n\r?\n/ — покрывает и \n\n, и \r\n\r\n.
      // Раньше был indexOf('\n\n'), который НЕ находил \r\n\r\n —
      // frontend не парсил events, chunks копились в буфере до конца стрима.
      let match: RegExpExecArray | null
      const SSE_EVENT_SEP = /\r?\n\r?\n/
      while ((match = SSE_EVENT_SEP.exec(buffer)) !== null) {
        const rawEvent = buffer.slice(0, match.index)
        buffer = buffer.slice(match.index + match[0].length)

        // Парсим событие
        let eventType = 'message'
        let dataLines: string[] = []
        for (const line of rawEvent.split(/\r?\n/)) {
          if (line.startsWith('event:')) {
            eventType = line.slice(6).trim()
          } else if (line.startsWith('data:')) {
            dataLines.push(line.slice(5).replace(/^ /, ''))
          }
          // строки, начинающиеся с ':' — комментарии (heartbeat), игнорируем
        }
        const data = dataLines.join('\n')

        if (eventType === 'delta') {
          handlers.onDelta(data)
        } else if (eventType === 'done') {
          handlers.onDone?.()
          return
        } else if (eventType === 'error') {
          let errMsg = data
          let retryable = false
          let errorType: string | undefined
          try {
            const parsed = JSON.parse(data)
            errMsg = parsed?.error ?? data
            retryable = Boolean(parsed?.retryable)
            errorType = parsed?.error_type
          } catch {
            // не JSON — оставляем как есть
          }
          handlers.onError?.(errMsg, retryable, errorType)
          return
        }
        // event: ping — игнорируем (heartbeat)
      }
    }
    // Поток закончился без явного done event — считаем, что всё OK
    handlers.onDone?.()
  } catch (err: any) {
    if (err?.name === 'AbortError') return
    handlers.onError?.(err?.message ?? 'Ошибка чтения потока')
  } finally {
    try {
      reader.releaseLock()
    } catch {
      // уже освобождён
    }
  }
}

export const api = {
  health: () => request<{ status: string }>('/health'),

  listRegions: () => request<Region[]>('/api/regions'),

  searchRegions: (q: string) =>
    request<Region[]>(`/api/regions/search?q=${encodeURIComponent(q)}`),

  parseQuery: (query: string) =>
    request<ParseResult>('/api/parse', {
      method: 'POST',
      body: JSON.stringify({ query }),
    }),

  // Structured-режим: регион и период выбраны из списка, парсинг не нужен.
  createStructuredTask: (params: StructuredTaskRequest) =>
    request<{ task_id: string; status: TaskStatus; region_code: string; region_name: string; period: string }>(
      '/api/dtp/tasks',
      {
        method: 'POST',
        body: JSON.stringify(params),
      }
    ),

  // Legacy-режим: текстовый запрос (для обратной совместимости).
  createTask: (params: { query?: string; region_code?: string; period?: string }) =>
    request<{ task_id: string; status: TaskStatus; region_code: string; region_name: string; period: string }>(
      '/api/dtp/tasks',
      {
        method: 'POST',
        body: JSON.stringify(params),
      }
    ),

  getTask: (taskId: string) =>
    request<TaskStatusResponse>(`/api/dtp/tasks/${taskId}`),

  listTasks: (limit = 20) =>
    request<TaskStatusResponse[]>(`/api/dtp/tasks?limit=${limit}`),

  getTaskFiles: (taskId: string) =>
    request<TaskFile[]>(`/api/dtp/tasks/${taskId}/files`),

  getMapUrl: (taskId: string) =>
    `${API_BASE}/api/dtp/tasks/${taskId}/map?tg_init_data=${encodeURIComponent(getInitData())}`,

  getDownloadUrl: (taskId: string, fileType: string) =>
    `${API_BASE}/api/dtp/tasks/${taskId}/download/${fileType}?tg_init_data=${encodeURIComponent(getInitData())}`,

  // ============================================================
  // Cameras
  // ============================================================
  listCameras: () => request<CameraListResponse>('/api/cameras'),

  getCamerasStatus: (regCode: string) =>
    request<CameraRegionInfo>(`/api/cameras/${regCode}`),

  uploadCameras: (regCode: string, file: File) => {
    const formData = new FormData()
    formData.append('file', file)
    return request<CameraUploadResponse>(`/api/cameras/${regCode}`, {
      method: 'POST',
      body: formData,
      // НЕ устанавливаем Content-Type — браузер сам поставит multipart/form-data
      // с правильным boundary. Заголовок X-Tg-Init-Data добавится в request().
    })
  },

  deleteCameras: (regCode: string) =>
    request<{ ok: boolean; reg_code: string; deleted: boolean }>(
      `/api/cameras/${regCode}`,
      { method: 'DELETE' }
    ),

  // ============================================================
  // Analysis: Clusters (очаги)
  // ============================================================
  startClusters: (taskId: string) =>
    request<ClustersResponse>(`/api/dtp/tasks/${taskId}/clusters`, {
      method: 'POST',
      body: JSON.stringify({}),
    }),

  getClusters: (taskId: string, wait: number = 0) =>
    request<ClustersResponse>(
      `/api/dtp/tasks/${taskId}/clusters` +
        (wait > 0 ? `?wait=${wait}` : '')
    ),

  getClustersMapUrl: (taskId: string) =>
    `${API_BASE}/api/dtp/tasks/${taskId}/clusters/map?tg_init_data=${encodeURIComponent(getInitData())}`,

  /**
   * Скачивает Excel с очагами (4 листа: очаги/динамика/детализация/предочаги).
   * Запускает браузерный download через создание <a> и клика по нему.
   */
  downloadClustersExcel: async (taskId: string): Promise<void> => {
    const url = `${API_BASE}/api/dtp/tasks/${taskId}/clusters/excel`
    await downloadBlobUrl(url, `dtp_ochagi_${taskId}.xlsx`)
  },

  // ============================================================
  // Analysis: Point statistics
  // ============================================================
  computePointStats: (taskId: string, lat: number, lon: number, radius_m: number) =>
    request<PointStatsResponse>(`/api/dtp/tasks/${taskId}/point`, {
      method: 'POST',
      body: JSON.stringify({ lat, lon, radius_m }),
    }),

  /**
   * Скачивает Excel со статистикой по точке (2 листа: текущий/прошлый период).
   * Требует предварительно выполненный computePointStats.
   */
  downloadPointStatsExcel: async (taskId: string): Promise<void> => {
    const url = `${API_BASE}/api/dtp/tasks/${taskId}/point/excel`
    await downloadBlobUrl(url, `point_stats_${taskId}.xlsx`)
  },

  /**
   * Возвращает URL HTML-карты точки (для <iframe>).
   * Карта: точка + радиус + ДТП (текущий/прошлый) + камеры в радиусе.
   */
  getPointStatsMapUrl: (
    taskId: string,
    lat: number,
    lon: number,
    radius_m: number
  ) =>
    `${API_BASE}/api/dtp/tasks/${taskId}/point/map` +
    `?lat=${lat}&lon=${lon}&radius_m=${radius_m}` +
    `&tg_init_data=${encodeURIComponent(getInitData())}`,

  // ============================================================
  // Analysis: LLM
  // ============================================================
  getLLMProvidersForTask: (taskId: string) =>
    request<LLMProvidersResponse>(`/api/dtp/tasks/${taskId}/llm/providers`),

  startLLMSummary: (taskId: string, provider: 'free' | 'paid') =>
    request<LLMSummaryResponse>(`/api/dtp/tasks/${taskId}/llm/summary`, {
      method: 'POST',
      body: JSON.stringify({ provider }),
    }),

  getLLMSummary: (taskId: string, wait: number = 0) =>
    request<LLMSummaryResponse>(
      `/api/dtp/tasks/${taskId}/llm/summary` +
        (wait > 0 ? `?wait=${wait}` : '')
    ),

  askLLM: (taskId: string, question: string, provider: 'free' | 'paid') =>
    request<LLMAskResponse>(`/api/dtp/tasks/${taskId}/llm/ask`, {
      method: 'POST',
      body: JSON.stringify({ question, provider }),
    }),

  /**
   * Sprint 4: SSE-стрим ответа на вопрос нейросети.
   *
   * Использует fetch + ReadableStream (НЕ EventSource, т.к. EventSource
   * не умеет передавать кастомные заголовки, а нам нужен X-Tg-Init-Data).
   *
   * @param taskId    — ID задачи
   * @param question  — текст вопроса
   * @param provider  — 'free' | 'paid'
   * @param handlers  — { onDelta, onDone, onError }
   * @param signal    — AbortSignal для отмены (кнопка «Стоп»)
   * @returns AbortController (вызовите .abort() для отмены)
   */
  askLLMStream: (
    taskId: string,
    question: string,
    provider: 'free' | 'paid',
    handlers: {
      onDelta: (text: string) => void
      onDone?: () => void
      onError?: (err: string, retryable?: boolean, errorType?: string) => void
    },
    signal?: AbortSignal,
  ): Promise<void> => {
    return consumeSSE(
      `/api/dtp/tasks/${taskId}/llm/ask/stream`,
      { question, provider },
      handlers,
      signal,
    )
  },

  /**
   * Sprint 4: SSE-стрим генерации аналитического резюме.
   *
   * Если есть cache hit — сервер эмитит весь текст одним delta и done (мгновенно).
   * Если cache miss — стримит token-by-token.
   */
  getLLMSummaryStream: (
    taskId: string,
    provider: 'free' | 'paid',
    handlers: {
      onDelta: (text: string) => void
      onDone?: () => void
      onError?: (err: string, retryable?: boolean, errorType?: string) => void
    },
    signal?: AbortSignal,
  ): Promise<void> => {
    return consumeSSE(
      `/api/dtp/tasks/${taskId}/llm/summary/stream`,
      { provider },
      handlers,
      signal,
    )
  },

  getQAHistory: (taskId: string) =>
    request<QAHistoryItem[]>(`/api/dtp/tasks/${taskId}/llm/qa-history`),

  // ============================================================
  // НП БДД
  // ============================================================
  npBddListRegions: () =>
    request<NpBddRegion[]>('/api/np-bdd/regions'),

  npBddGetData: (
    regionCode: string,
    planLineMode: 'linear' | 'horizontal' = 'linear',
    forecastMethod: NpBddForecastMethod = 'corridor',
  ) =>
    request<NpBddData>(
      `/api/np-bdd/data?region_code=${encodeURIComponent(regionCode)}` +
      `&plan_line_mode=${planLineMode}` +
      `&forecast_method=${forecastMethod}`
    ),

  npBddGetSettings: (regionCode: string) =>
    request<NpBddSettings>(
      `/api/np-bdd/settings?region_code=${encodeURIComponent(regionCode)}`
    ),

  npBddUpdateSettings: (
    regionCode: string,
    planLineMode: 'linear' | 'horizontal',
    forecastMethod?: NpBddForecastMethod,
  ) =>
    request<NpBddSettings>('/api/np-bdd/settings', {
      method: 'PATCH',
      body: JSON.stringify({
        region_code: regionCode,
        plan_line_mode: planLineMode,
        forecast_method: forecastMethod,
      }),
    }),

  npBddListFrozen: (regionCode: string) =>
    request<NpBddFrozenYear[]>(
      `/api/np-bdd/frozen?region_code=${encodeURIComponent(regionCode)}`
    ),

  npBddFreezeYear: (regionCode: string, year: number, note?: string) =>
    request<{ ok: boolean; region_code: string; year: number; record: NpBddFrozenYear }>(
      '/api/np-bdd/freeze',
      { method: 'POST', body: JSON.stringify({ region_code: regionCode, year, note }) }
    ),

  npBddUnfreezeYear: (regionCode: string, year: number) =>
    request<{ ok: boolean; region_code: string; year: number }>(
      '/api/np-bdd/unfreeze',
      { method: 'POST', body: JSON.stringify({ region_code: regionCode, year }) }
    ),
}
