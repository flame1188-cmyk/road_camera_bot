/**
 * NpBddView — вкладка «НП БДД» (Национальный проект «Безопасность дорожного движения»).
 *
 * Показывает:
 *  1. Селектор региона + переключатель linear/horizontal для линии плана.
 *  2. 4 KPI-карточки: Тр факт (YTD), Тр прогноз (на конец года), План, Отклонение.
 *  3. График 1: динамика Тр 2023→2030 (факт + прогноз + план).
 *  4. График 2: кумулятивный Тр по месяцам текущего года (факт + прогноз + план).
 *  5. Кнопка «Заморозить год» для админ-действий.
 */
import { type ReactNode, useEffect, useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api, type NpBddData, type NpBddForecastMethod, type NpBddRegion } from '@/lib/api'
import { haptic, showAlert, showConfirm } from '@/lib/telegram'
import { cn } from '@/lib/utils'

const MONTH_SHORT = [
  'Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн',
  'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек',
]

const STATUS_COLORS: Record<string, string> = {
  ok: 'text-green-600 dark:text-green-400',
  warning: 'text-yellow-600 dark:text-yellow-400',
  danger: 'text-red-600 dark:text-red-400',
}

const STATUS_LABELS: Record<string, string> = {
  ok: 'Выполняется',
  warning: 'На грани',
  danger: 'Угроза срыва',
}

// Описание методик прогноза для выпадающего списка и информационной панели.
// forecast_method (значение в API) → { label, description }
const FORECAST_METHOD_INFO: Record<NpBddForecastMethod, {
  label: string
  short: string
  description: string
  formula: string
}> = {
  central_only: {
    label: 'Центр (avg per-year)',
    short: 'Центр',
    description:
      'Прогноз на конец года = deaths_ytd / avg(cum_share[current_month]). ' +
      'Использует среднюю кумулятивную долю текущего месяца по истории региона ' +
      '(per-region профиль, при отсутствии — global). Одна линия прогноза, без коридора.',
    formula: 'прогноз = YTD / средн(cum_share[мес])',
  },
  corridor: {
    label: 'Коридор (min/max per-year)',
    short: 'Коридор',
    description:
      'Центральная линия = текущий метод. Оптимистичная = YTD / max(cum_share_Y[мес]) ' +
      'по историческим годам (самая «передняя» сезонность → меньший остаток). ' +
      'Пессимистичная = YTD / min(cum_share_Y[мес]) (самая «задняя» сезонность → больший остаток). ' +
      'Коридор сужается к декабрю. Требует ≥ 2 лет истории, иначе отключается.',
    formula: 'оптимист = YTD / max(cum_share_Y[мес])  •  пессимист = YTD / min(cum_share_Y[мес])',
  },
}

interface NpBddViewProps {
  // placeholder для будущей интеграции, пока нет
}

// ============================================================
// i-иконка с popover: показывает описание метода прогноза
// ============================================================
function ForecastMethodInfo({
  method,
  corridorAvailable,
}: {
  method: NpBddForecastMethod
  corridorAvailable?: boolean
}) {
  const [open, setOpen] = useState(false)
  const info = FORECAST_METHOD_INFO[method]

  // Закрытие по клику вне popover
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      const target = e.target as HTMLElement
      if (!target.closest('[data-method-info]')) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  return (
    <div className="relative" data-method-info>
      <button
        type="button"
        onClick={() => { haptic('light'); setOpen((v) => !v) }}
        className="inline-flex items-center justify-center w-5 h-5 rounded-full border border-current opacity-60 hover:opacity-100 text-xs font-bold"
        style={{ lineHeight: 1 }}
        aria-label="Описание метода прогноза"
      >
        i
      </button>
      {open && (
        <div
          className="absolute right-0 top-6 z-50 w-72 max-w-[80vw] p-3 rounded-xl shadow-lg text-xs leading-relaxed"
          style={{
            background: 'var(--tg-color-section-bg, #fff)',
            border: '1px solid var(--tg-color-hint, #ccc)',
            color: 'var(--tg-color-text, #000)',
          }}
        >
          <div className="font-semibold mb-1">{info.label}</div>
          <div className="opacity-80 mb-2">{info.description}</div>
          <div className="font-mono bg-tg-secondary-bg px-2 py-1 rounded mb-2">{info.formula}</div>
          {method === 'corridor' && corridorAvailable === false && (
            <div className="text-yellow-600 dark:text-yellow-400">
              ⚠ Для текущего региона коридор недоступен (нужно ≥ 2 лет истории).
            </div>
          )}
          <div className="text-[10px] opacity-50 mt-2 text-right">Клик вне области — закрыть</div>
        </div>
      )}
    </div>
  )
}

export function NpBddView(_: NpBddViewProps = {}) {
  const queryClient = useQueryClient()
  const [selectedRegion, setSelectedRegion] = useState<string>('')
  const [planLineMode, setPlanLineMode] = useState<'linear' | 'horizontal'>('linear')
  const [forecastMethod, setForecastMethod] = useState<NpBddForecastMethod>('corridor')

  // --- Список регионов ---
  const regionsQuery = useQuery({
    queryKey: ['np-bdd-regions'],
    queryFn: api.npBddListRegions,
    staleTime: 30 * 60 * 1000, // 30 минут
  })

  // Автовыбор первого региона
  useEffect(() => {
    if (!selectedRegion && regionsQuery.data && regionsQuery.data.length > 0) {
      setSelectedRegion(regionsQuery.data[0].code)
    }
  }, [regionsQuery.data, selectedRegion])

  // --- Настройки региона (для подхвата plan_line_mode) ---
  const settingsQuery = useQuery({
    queryKey: ['np-bdd-settings', selectedRegion],
    queryFn: () => api.npBddGetSettings(selectedRegion),
    enabled: !!selectedRegion,
    staleTime: 60 * 60 * 1000,
  })

  useEffect(() => {
    if (settingsQuery.data?.plan_line_mode) {
      setPlanLineMode(settingsQuery.data.plan_line_mode)
    }
    if (settingsQuery.data?.forecast_method) {
      setForecastMethod(settingsQuery.data.forecast_method)
    }
  }, [settingsQuery.data])

  // --- Главный payload ---
  const dataQuery = useQuery({
    queryKey: ['np-bdd-data', selectedRegion, planLineMode, forecastMethod],
    queryFn: () => api.npBddGetData(selectedRegion, planLineMode, forecastMethod),
    enabled: !!selectedRegion,
    staleTime: 5 * 60 * 1000, // 5 минут на клиенте
    retry: 1,
  })

  // --- Список замороженных лет ---
  const frozenQuery = useQuery({
    queryKey: ['np-bdd-frozen', selectedRegion],
    queryFn: () => api.npBddListFrozen(selectedRegion),
    enabled: !!selectedRegion,
    staleTime: 60 * 1000,
  })

  // --- Мутация: переключение plan_line_mode / forecast_method ---
  const updateSettingsMutation = useMutation({
    mutationFn: (params: {
      plan_line_mode: 'linear' | 'horizontal'
      forecast_method?: NpBddForecastMethod
    }) =>
      api.npBddUpdateSettings(
        selectedRegion,
        params.plan_line_mode,
        params.forecast_method,
      ),
    onSuccess: (data) => {
      haptic('light')
      setPlanLineMode(data.plan_line_mode)
      if (data.forecast_method) {
        setForecastMethod(data.forecast_method)
      }
      // Инвалидируем data, чтобы перетянуть с новым режимом
      queryClient.invalidateQueries({ queryKey: ['np-bdd-data', selectedRegion] })
    },
    onError: (err: Error) => {
      haptic('error')
      showAlert(`Не удалось сохранить настройку: ${err.message}`)
    },
  })

  // --- Мутация: заморозка года ---
  const freezeMutation = useMutation({
    mutationFn: ({ year, note }: { year: number; note?: string }) =>
      api.npBddFreezeYear(selectedRegion, year, note),
    onSuccess: () => {
      haptic('success')
      queryClient.invalidateQueries({ queryKey: ['np-bdd-frozen', selectedRegion] })
      queryClient.invalidateQueries({ queryKey: ['np-bdd-data', selectedRegion] })
    },
    onError: (err: Error) => {
      haptic('error')
      showAlert(`Не удалось заморозить год: ${err.message}`)
    },
  })

  // --- Мутация: разморозка ---
  const unfreezeMutation = useMutation({
    mutationFn: (year: number) => api.npBddUnfreezeYear(selectedRegion, year),
    onSuccess: () => {
      haptic('light')
      queryClient.invalidateQueries({ queryKey: ['np-bdd-frozen', selectedRegion] })
      queryClient.invalidateQueries({ queryKey: ['np-bdd-data', selectedRegion] })
    },
    onError: (err: Error) => {
      haptic('error')
      showAlert(`Не удалось разморозить: ${err.message}`)
    },
  })

  const handleTogglePlanLine = () => {
    const newMode = planLineMode === 'linear' ? 'horizontal' : 'linear'
    updateSettingsMutation.mutate({
      plan_line_mode: newMode,
      forecast_method: forecastMethod,
    })
  }

  const handleFreeze = async (year: number) => {
    const ok = await showConfirm(
      `Заморозить ${year} год?\n\nПосле заморозки данные за этот год не будут пересчитываться. ` +
      `Используйте это после финализации данных ГИБДД (обычно через 2-3 месяца после окончания года).`
    )
    if (!ok) return
    freezeMutation.mutate({ year })
  }

  const handleUnfreeze = async (year: number) => {
    const ok = await showConfirm(`Разморозить ${year} год?`)
    if (!ok) return
    unfreezeMutation.mutate(year)
  }

  // --- Подготовка данных для графиков ---

  // График 1: точки по годам 2023..2030.
  // Для каждой точки: год, fact (если есть), plan.
  const chart1Data = useMemo(() => {
    if (!dataQuery.data) return []
    const d = dataQuery.data
    const years = Object.keys(d.plan_series).sort()
    return years.map((year) => {
      const planVal = d.plan_series[year]
      let factVal: number | null = null
      let factDeaths: number | null = null
      let isForecast = false
      if (d.history[year]) {
        factVal = d.history[year].tr
        factDeaths = d.history[year].deaths
      } else if (d.current_year.year.toString() === year) {
        factVal = d.current_year.tr_forecast_full_year
        // Для текущего года — прогноз погибших на конец года (может быть коридор)
        factDeaths = d.current_year.deaths_forecast_full_year
        isForecast = true
      }
      // Плановых погибших у нас нет (есть только tr_plan); оставляем null
      return {
        year,
        plan: planVal,
        fact: factVal,
        isForecast,
        // Для tooltip: кумулятивные погибшие за год
        factDeaths,
        // Оптимист/пессимист для текущего года (для tooltip в коридоре)
        optimisticDeaths: isForecast ? d.current_year.deaths_forecast_optimistic ?? null : null,
        pessimisticDeaths: isForecast ? d.current_year.deaths_forecast_pessimistic ?? null : null,
      }
    })
  }, [dataQuery.data])

  // График 2: кумулятивный Тр по месяцам.
  //
  // ВАЖНО: линии прогноза/коридора должны РАЗВЕТВЛЯТЬСЯ от последней
  // фактической точки — без разрыва по оси X и без «хвоста» факта.
  //
  // Логика:
  //   - На последнем фактическом месяце (m = current_month) рисуем fact,
  //     и в этой же точке стартуют все прогнозные линии — все равны lastActual.
  //   - На следующих месяцах (m > current_month) fact = null (не продлеваем!),
  //     прогнозные линии = своим значениям.
  //   - На прошлых месяцах (m < current_month) fact = actual, прогнозы = null.
  //
  // В итоге Recharts рисует: fact (1..m) ── точка ветвления ── три прогнозные
  // линии (m..12), расходящиеся из последней фактической точки.
  const chart2Data = useMemo(() => {
    if (!dataQuery.data) return []
    const mc = dataQuery.data.current_year.monthly_chart
    const lastActualMonth = mc.current_month
    const lastActualKey = String(lastActualMonth)
    const lastActualValue = mc.tr_actual_cumulative?.[lastActualKey]
    const lastActualDeaths = mc.deaths_actual_cumulative?.[lastActualKey]

    return mc.months.map((m) => {
      const key = String(m)
      const actual = mc.tr_actual_cumulative[key]
      const forecast = mc.tr_forecast_cumulative[key]
      const optimistic = mc.tr_optimistic_cumulative?.[key]
      const pessimistic = mc.tr_pessimistic_cumulative?.[key]
      const actualDeaths = mc.deaths_actual_cumulative?.[key]
      const forecastDeaths = mc.deaths_forecast_cumulative?.[key]
      const optimisticDeaths = mc.deaths_optimistic_cumulative?.[key]
      const pessimisticDeaths = mc.deaths_pessimistic_cumulative?.[key]
      const plan = mc.plan_cumulative[key]

      // Точка ветвления: последний фактический месяц.
      // В ней fact и все прогнозные линии равны lastActual.
      const isJointPoint = m === lastActualMonth && lastActualValue !== undefined

      // Для прогнозных линий: на точке ветвления = lastActual,
      // иначе = своё значение (если определено).
      const joinTr = isJointPoint ? lastActualValue! : null
      const joinDeaths = isJointPoint && lastActualDeaths !== undefined ? lastActualDeaths : null

      return {
        month: MONTH_SHORT[m - 1] || `М${m}`,
        // fact: только actual, НЕ продлеваем на будущие месяцы
        fact: actual !== undefined ? actual : null,
        // Прогнозные линии: на точке ветвления = lastActual, дальше = своим значениям
        forecast: isJointPoint
          ? joinTr
          : (forecast !== undefined ? forecast : null),
        optimistic: isJointPoint
          ? joinTr
          : (optimistic !== undefined ? optimistic : null),
        pessimistic: isJointPoint
          ? joinTr
          : (pessimistic !== undefined ? pessimistic : null),
        plan,
        // То же самое для погибших (для tooltip)
        factDeaths: actualDeaths !== undefined ? actualDeaths : null,
        forecastDeaths: isJointPoint
          ? joinDeaths
          : (forecastDeaths !== undefined ? forecastDeaths : null),
        optimisticDeaths: isJointPoint
          ? joinDeaths
          : (optimisticDeaths !== undefined ? optimisticDeaths : null),
        pessimisticDeaths: isJointPoint
          ? joinDeaths
          : (pessimisticDeaths !== undefined ? pessimisticDeaths : null),
      }
    })
  }, [dataQuery.data])

  // --- Загрузка состояний ---
  if (regionsQuery.isLoading) {
    return <div className="tg-card">Загрузка справочника регионов…</div>
  }
  if (regionsQuery.isError) {
    return (
      <div className="tg-card text-red-600 dark:text-red-400">
        Не удалось загрузить список регионов: {(regionsQuery.error as Error).message}
      </div>
    )
  }
  if (!regionsQuery.data || regionsQuery.data.length === 0) {
    return (
      <div className="tg-card">
        Нет данных по регионам. Попросите администратора загрузить Excel-файлы
        Ктс и плановых значений Тр.
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {/* Заголовок вкладки */}
      <div className="tg-card">
        <h2 className="text-lg font-semibold mb-1">НП БДД</h2>
        <p className="text-sm text-tg-hint">
          Мониторинг показателя Тр (погибших на 10 000 ТС) в рамках
          национального проекта «Безопасность дорожного движения».
        </p>
      </div>

      {/* Селектор региона + переключатель плана */}
      <div className="tg-card space-y-3">
        <div>
          <label className="tg-section-header block mb-1">Регион</label>
          <select
            className="tg-input w-full"
            value={selectedRegion}
            onChange={(e) => { haptic('light'); setSelectedRegion(e.target.value) }}
          >
            {regionsQuery.data.map((r: NpBddRegion) => (
              <option key={r.code} value={r.code}>{r.name}</option>
            ))}
          </select>
        </div>

        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="tg-section-header text-xs">Линия плана (график 2)</div>
            <div className="text-sm text-tg-hint mt-0.5">
              {planLineMode === 'linear'
                ? 'Линейный рост от 0 до годового плана'
                : 'Горизонтальная линия на уровне годового плана'}
            </div>
          </div>
          <button
            className="tg-button !py-2 !px-3 text-sm"
            onClick={handleTogglePlanLine}
            disabled={updateSettingsMutation.isPending}
          >
            {planLineMode === 'linear' ? 'Линейный' : 'Горизонтальный'}
          </button>
        </div>

        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <div className="tg-section-header text-xs flex-1">Метод прогноза (график 2)</div>
            <ForecastMethodInfo method={forecastMethod} corridorAvailable={dataQuery.data?.corridor_available} />
          </div>
          <select
            className="tg-input w-full !py-2 text-sm"
            value={forecastMethod}
            onChange={(e) => {
              haptic('light')
              const newMethod = e.target.value as NpBddForecastMethod
              updateSettingsMutation.mutate({
                plan_line_mode: planLineMode,
                forecast_method: newMethod,
              })
            }}
            disabled={updateSettingsMutation.isPending}
          >
            {(Object.keys(FORECAST_METHOD_INFO) as NpBddForecastMethod[]).map((method) => (
              <option key={method} value={method}>
                {FORECAST_METHOD_INFO[method].label}
              </option>
            ))}
          </select>
          {forecastMethod === 'corridor' && dataQuery.data && !dataQuery.data.corridor_available && (
            <div className="text-xs text-yellow-600 dark:text-yellow-400 mt-1">
              ⚠ Коридор недоступен для этого региона (нужно ≥ 2 лет истории).
              Используется центральная линия.
            </div>
          )}
        </div>
      </div>

      {/* KPI-карточки + графики */}
      {dataQuery.isLoading && <div className="tg-card">Загрузка данных…</div>}
      {dataQuery.isError && (
        <div className="tg-card text-red-600 dark:text-red-400">
          Не удалось загрузить данные: {(dataQuery.error as Error).message}
        </div>
      )}
      {dataQuery.data && (
        <NpBddContent
          data={dataQuery.data}
          chart1Data={chart1Data}
          chart2Data={chart2Data}
          frozenYears={frozenQuery.data ?? []}
          onFreeze={handleFreeze}
          onUnfreeze={handleUnfreeze}
          freezePending={freezeMutation.isPending}
          unfreezePending={unfreezeMutation.isPending}
        />
      )}
    </div>
  )
}

// ============================================================
// Контент с KPI и графиками
// ============================================================

interface NpBddContentProps {
  data: NpBddData
  chart1Data: Array<{
    year: string
    plan: number
    fact: number | null
    isForecast: boolean
    factDeaths: number | null
    optimisticDeaths: number | null
    pessimisticDeaths: number | null
  }>
  chart2Data: Array<{
    month: string
    fact: number | null
    forecast: number | null
    optimistic: number | null
    pessimistic: number | null
    plan: number
    factDeaths: number | null
    forecastDeaths: number | null
    optimisticDeaths: number | null
    pessimisticDeaths: number | null
  }>
  frozenYears: Array<{ year: number; tr: number; deaths: number; frozen_at?: string; note?: string }>
  onFreeze: (year: number) => void
  onUnfreeze: (year: number) => void
  freezePending: boolean
  unfreezePending: boolean
}

function NpBddContent({
  data, chart1Data, chart2Data, frozenYears,
  onFreeze, onUnfreeze, freezePending, unfreezePending,
}: NpBddContentProps) {
  const { kpi, region, current_year, seasonal } = data
  const corridorOn = data.forecast_method === 'corridor' && data.corridor_available === true

  // Замороженные годы как Set для быстрой проверки
  const frozenSet = useMemo(() => new Set(frozenYears.map((f) => f.year)), [frozenYears])

  // Годы для кнопок заморозки: 2023, 2024, ..., текущий год - 1
  const freezableYears = useMemo(() => {
    const currentYear = current_year.year
    const years: number[] = []
    for (let y = 2023; y < currentYear; y++) years.push(y)
    return years
  }, [current_year.year])

  // Человекочитаемая подпись источника сезонности.
  const seasonalHint = useMemo(() => {
    if (!seasonal) return undefined
    if (seasonal.source === 'per-region') {
      return `Per-region профиль (${seasonal.samples_used} года истории)`
    }
    if (seasonal.source === 'global') {
      return `Глобальный профиль (${seasonal.samples_used} регион-лет)`
    }
    if (seasonal.source === 'uniform') {
      return 'Uniform 1/12 (нет истории)'
    }
    return undefined
  }, [seasonal])

  return (
    <>
      {/* 4 KPI-карточки */}
      <div className="grid grid-cols-2 gap-2">
        <KpiCard
          label={`Тр факт (${current_year.months_actual.length} мес)`}
          value={kpi.tr_actual_ytd.toFixed(3)}
          hint={`${current_year.deaths_ytd} погибших YTD`}
        />
        <KpiCard
          label="Тр прогноз (конец года)"
          value={kpi.tr_forecast_full_year.toFixed(3)}
          hint={
            corridorOn && kpi.tr_forecast_optimistic != null && kpi.tr_forecast_pessimistic != null
              ? `Коридор: ${kpi.tr_forecast_optimistic.toFixed(3)} – ${kpi.tr_forecast_pessimistic.toFixed(3)} (≈ ${current_year.deaths_forecast_optimistic ?? '—'} – ${current_year.deaths_forecast_pessimistic ?? '—'} погибших)`
              : `≈ ${current_year.deaths_forecast_full_year} погибших`
          }
          highlight={kpi.status}
        />
        {(() => {
          // Плановое количество погибших = tr_plan * Ктс / 10000.
          // Ктс вычисляем обратно из факта: Ктс = deaths_ytd * 10000 / tr_actual_ytd.
          // Если факта ещё нет (tr_actual_ytd = 0), плановые погибшие не определены.
          const trActual = kpi.tr_actual_ytd
          const deathsYtd = current_year.deaths_ytd
          const trPlan = kpi.tr_plan
          const planDeaths = trActual > 0
            ? Math.round(trPlan * deathsYtd / trActual)
            : null
          const forecastDeaths = current_year.deaths_forecast_full_year
          const delta = planDeaths !== null ? forecastDeaths - planDeaths : null
          const deltaSign = delta !== null && delta > 0 ? '+' : ''

          // === Логика для варианта B (с коридором) ===
          // Цвет плашки = по центральному прогнозу (kpi.status).
          // Если центр ok и пессимист не выполняет → ⚠ предупреждение о риске.
          // Если центр danger и оптимист выполняет → ✓ указание на возможность.
          const optDeaths = current_year.deaths_forecast_optimistic ?? null
          const pessDeaths = current_year.deaths_forecast_pessimistic ?? null
          const corridorHas = corridorOn && optDeaths !== null && pessDeaths !== null

          // План выполняется = deaths <= planDeaths (т.е. delta <= 0)
          const centralAchieved = delta !== null ? delta <= 0 : null
          const optimistAchieved =
            planDeaths !== null && optDeaths !== null ? optDeaths <= planDeaths : null
          const pessimistAchieved =
            planDeaths !== null && pessDeaths !== null ? pessDeaths <= planDeaths : null

          // Дельты по коридору (для hint)
          const deltaOpt =
            planDeaths !== null && optDeaths !== null ? optDeaths - planDeaths : null
          const deltaPess =
            planDeaths !== null && pessDeaths !== null ? pessDeaths - planDeaths : null
          const fmtDelta = (d: number | null): string =>
            d === null ? '—' : `${d > 0 ? '+' : ''}${d}`

          // Формируем hint в зависимости от сценария
          let deviationHint: ReactNode
          if (corridorHas && centralAchieved !== null && optimistAchieved !== null && pessimistAchieved !== null) {
            // Коридор: показываем оптим./пессим. + индикатор риска
            const lines: ReactNode[] = []
            lines.push(
              <div key="opt" style={{ color: optimistAchieved ? '#34c759' : '#ff9500' }}>
                {optimistAchieved ? '✓' : '⚠'} Оптим.: {fmtDelta(deltaOpt)} погибших
              </div>
            )
            lines.push(
              <div key="pess" style={{ color: pessimistAchieved ? '#34c759' : '#ff9500' }}>
                {pessimistAchieved ? '✓' : '⚠'} Пессим.: {fmtDelta(deltaPess)} погибших
              </div>
            )

            if (centralAchieved && !pessimistAchieved) {
              // Центр выполняется, но пессимист не выполняет → предупреждение о риске
              lines.push(
                <div key="warn" style={{ marginTop: 2, color: '#ff9500', fontWeight: 600 }}>
                  ⚠ Внимание: при негативном сценарии план не выполняется
                </div>
              )
            } else if (!centralAchieved && optimistAchieved) {
              // Центр не выполняется, но оптимист выполняет → указание на возможность
              lines.push(
                <div key="opt-note" style={{ marginTop: 2, color: '#34c759', fontWeight: 600 }}>
                  ✓ Возможен позитивный сценарий: план выполняется
                </div>
              )
            }
            deviationHint = <>{lines.map((l, i) => <div key={i}>{l}</div>)}</>
          } else if (delta !== null) {
            // Центральный метод или коридор недоступен — старый формат
            deviationHint = `${STATUS_LABELS[kpi.status]} • Δ = ${deltaSign}${delta} погибших от плана`
          } else {
            deviationHint = STATUS_LABELS[kpi.status]
          }

          return (
            <>
              <KpiCard
                label={`План ${current_year.year}`}
                value={trPlan.toFixed(3)}
                hint={planDeaths !== null ? `Цель: ≤ ${planDeaths} погибших` : 'Из паспорта НП БДД'}
              />
              <KpiCard
                label="Отклонение от плана"
                value={`${kpi.deviation_pct > 0 ? '+' : ''}${kpi.deviation_pct}%`}
                hint={deviationHint}
                highlight={kpi.status}
              />
            </>
          )
        })()}
      </div>

      {/* Подпись об источнике сезонности */}
      {seasonalHint && (
        <div className="text-xs opacity-60 -mt-1 mb-1 px-1">
          📊 Сезонность: {seasonalHint}
        </div>
      )}

      {/* График 1: динамика 2023→2030 */}
      <div className="tg-card">
        <h3 className="font-semibold mb-1">Динамика Тр 2023 → 2030</h3>
        <p className="text-xs text-tg-hint mb-3">
          Факт (история + прогноз текущего года) и плановые значения из паспорта НП БДД.
        </p>
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chart1Data} margin={{ top: 5, right: 10, bottom: 5, left: -10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--tg-color-hint, #999)" opacity={0.3} />
              <XAxis dataKey="year" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip
                content={({ active, payload, label }) => {
                  if (!active || !payload || payload.length === 0) return null
                  const pointRecord = payload[0]?.payload as
                    | {
                        year: string
                        isForecast?: boolean
                        factDeaths?: number | null
                        optimisticDeaths?: number | null
                        pessimisticDeaths?: number | null
                      }
                    | undefined
                  const isForecast = pointRecord?.isForecast === true
                  const factDeaths = pointRecord?.factDeaths ?? null
                  const optDeaths = pointRecord?.optimisticDeaths ?? null
                  const pessDeaths = pointRecord?.pessimisticDeaths ?? null

                  const colorMap: Record<string, string> = {
                    plan: 'var(--tg-color-link, #2481cc)',
                    fact: 'var(--tg-color-destructive, #ff3b30)',
                  }
                  const labelMap: Record<string, string> = {
                    plan: 'План',
                    fact: isForecast ? 'Прогноз' : 'Факт',
                  }

                  return (
                    <div
                      style={{
                        background: 'var(--tg-color-section-bg, #fff)',
                        border: '1px solid var(--tg-color-hint, #ccc)',
                        borderRadius: '8px',
                        fontSize: '12px',
                        padding: '8px 10px',
                        color: 'var(--tg-color-text, #000)',
                      }}
                    >
                      <div style={{ fontWeight: 600, marginBottom: 4 }}>{label}</div>
                      {payload.map((entry) => {
                        const dk = String(entry.dataKey ?? '')
                        const trVal = typeof entry.value === 'number' ? entry.value : null
                        // Для fact показываем погибших (факт или прогноз на конец года)
                        const deaths = dk === 'fact' ? factDeaths : null
                        return (
                          <div
                            key={dk}
                            style={{
                              display: 'flex',
                              alignItems: 'center',
                              gap: 6,
                              marginTop: 2,
                            }}
                          >
                            <span
                              style={{
                                display: 'inline-block',
                                width: 10,
                                height: 10,
                                borderRadius: 2,
                                background: colorMap[dk] ?? '#999',
                                flexShrink: 0,
                              }}
                            />
                            <span>
                              {labelMap[dk] ?? dk}:{' '}
                              <span style={{ fontWeight: 600 }}>
                                {trVal !== null ? trVal.toFixed(3) : '—'}
                              </span>
                              {deaths !== null && deaths !== undefined && (
                                <span style={{ opacity: 0.7 }}>
                                  {' '}({deaths} погибш.)
                                </span>
                              )}
                            </span>
                          </div>
                        )
                      })}
                      {isForecast && (optDeaths !== null || pessDeaths !== null) && (
                        <div
                          style={{
                            marginTop: 4,
                            paddingTop: 4,
                            borderTop: '1px solid var(--tg-color-hint, #ccc)',
                            opacity: 0.85,
                            fontSize: '11px',
                          }}
                        >
                          {optDeaths !== null && (
                            <div>
                              <span style={{ color: '#34c759' }}>●</span> Оптим.: {optDeaths} погибш.
                            </div>
                          )}
                          {pessDeaths !== null && (
                            <div>
                              <span style={{ color: '#ff9500' }}>●</span> Пессим.: {pessDeaths} погибш.
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )
                }}
              />
              <Legend wrapperStyle={{ fontSize: '11px' }} />
              <Line
                type="monotone"
                dataKey="plan"
                name="План"
                stroke="var(--tg-color-link, #2481cc)"
                strokeWidth={2}
                strokeDasharray="5 5"
                dot={{ r: 3 }}
              />
              <Line
                type="monotone"
                dataKey="fact"
                name="Факт / прогноз"
                stroke="var(--tg-color-destructive, #ff3b30)"
                strokeWidth={2.5}
                dot={({ cx, cy, payload }) => {
                  if (cy === null || cy === undefined) return <></>
                  const isForecast = payload?.isForecast
                  return (
                    <circle
                      key={payload?.year}
                      cx={cx}
                      cy={cy}
                      r={4}
                      fill={isForecast ? 'var(--tg-color-link, #2481cc)' : 'var(--tg-color-destructive, #ff3b30)'}
                      stroke="var(--tg-color-bg, #fff)"
                      strokeWidth={1.5}
                    />
                  )
                }}
                connectNulls
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="text-xs text-tg-hint mt-2">
          ● Красная точка — факт, ● синяя точка — прогноз на конец текущего года.
        </div>
      </div>

      {/* График 2: кумулятивный Тр по месяцам текущего года */}
      <div className="tg-card">
        <h3 className="font-semibold mb-1">
          Текущий {current_year.year}: кумулятивный Тр по месяцам
        </h3>
        <p className="text-xs text-tg-hint mb-3">
          Сплошная — факт (прошедшие месяцы), пунктир — прогноз (будущие), линия плана — {data.current_year.monthly_chart.plan_line_mode === 'linear' ? 'линейный рост' : 'горизонталь'}.
          Наведите на точку — увидите Тр и кумулятивное число погибших.
          {corridorOn && ' Коридор (зелёный/оранжевый) — min/max по историческим годам.'}
        </p>
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chart2Data} margin={{ top: 5, right: 10, bottom: 5, left: -10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--tg-color-hint, #999)" opacity={0.3} />
              <XAxis dataKey="month" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip
                content={({ active, payload, label }) => {
                  if (!active || !payload || payload.length === 0) return null
                  const deathsKeyMap: Record<string, string> = {
                    fact: 'factDeaths',
                    forecast: 'forecastDeaths',
                    optimistic: 'optimisticDeaths',
                    pessimistic: 'pessimisticDeaths',
                  }
                  const colorMap: Record<string, string> = {
                    fact: 'var(--tg-color-destructive, #ff3b30)',
                    forecast: 'var(--tg-color-link, #2481cc)',
                    optimistic: '#34c759',
                    pessimistic: '#ff9500',
                    plan: 'var(--tg-color-hint, #999)',
                  }
                  const labelMap: Record<string, string> = {
                    fact: 'Факт',
                    forecast: 'Прогноз',
                    optimistic: 'Оптимист.',
                    pessimistic: 'Пессимист.',
                    plan: 'План',
                  }
                  const pointRecord = payload[0]?.payload as Record<string, number | null> | undefined
                  return (
                    <div
                      style={{
                        background: 'var(--tg-color-section-bg, #fff)',
                        border: '1px solid var(--tg-color-hint, #ccc)',
                        borderRadius: '8px',
                        fontSize: '12px',
                        padding: '8px 10px',
                        color: 'var(--tg-color-text, #000)',
                      }}
                    >
                      <div style={{ fontWeight: 600, marginBottom: 4 }}>{label}</div>
                      {payload.map((entry) => {
                        const dk = String(entry.dataKey ?? '')
                        const deathsField = deathsKeyMap[dk]
                        const deathsVal = deathsField && pointRecord
                          ? pointRecord[deathsField]
                          : null
                        const trVal = typeof entry.value === 'number' ? entry.value : null
                        return (
                          <div key={dk} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
                            <span style={{
                              display: 'inline-block',
                              width: 10, height: 10,
                              background: colorMap[dk] ?? '#999',
                              borderRadius: 2,
                              flexShrink: 0,
                            }} />
                            <span style={{ minWidth: 70 }}>{labelMap[dk] ?? dk}:</span>
                            <span style={{ fontVariantNumeric: 'tabular-nums', fontWeight: 600 }}>
                              {trVal !== null ? trVal.toFixed(3) : '—'}
                            </span>
                            {deathsVal !== null && deathsVal !== undefined && (
                              <span style={{ opacity: 0.7, fontSize: '11px' }}>
                                ({deathsVal} погибш.)
                              </span>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  )
                }}
              />
              <Legend wrapperStyle={{ fontSize: '11px' }} />
              <Line
                type="monotone"
                dataKey="fact"
                name="Факт (кум.)"
                stroke="var(--tg-color-destructive, #ff3b30)"
                strokeWidth={2.5}
                dot={{ r: 3 }}
                connectNulls
              />
              <Line
                type="monotone"
                dataKey="forecast"
                name="Прогноз (кум.)"
                stroke="var(--tg-color-link, #2481cc)"
                strokeWidth={2}
                strokeDasharray="5 5"
                dot={{ r: 3 }}
                connectNulls
              />
              {corridorOn && (
                <>
                  <Line
                    type="monotone"
                    dataKey="optimistic"
                    name="Оптимист. (min)"
                    stroke="#34c759"
                    strokeWidth={1.5}
                    strokeDasharray="3 3"
                    dot={false}
                    connectNulls
                  />
                  <Line
                    type="monotone"
                    dataKey="pessimistic"
                    name="Пессимист. (max)"
                    stroke="#ff9500"
                    strokeWidth={1.5}
                    strokeDasharray="3 3"
                    dot={false}
                    connectNulls
                  />
                </>
              )}
              <Line
                type="monotone"
                dataKey="plan"
                name={`План (${data.current_year.monthly_chart.plan_line_mode === 'linear' ? 'линейн.' : 'гориз.'})`}
                stroke="var(--tg-color-hint, #999)"
                strokeWidth={1.5}
                strokeDasharray="2 2"
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Замороженные годы */}
      <div className="tg-card">
        <h3 className="font-semibold mb-2">Заморозка лет</h3>
        <p className="text-xs text-tg-hint mb-3">
          После финализации данных ГИБДД (через 2-3 месяца после окончания года)
          заморозьте год, чтобы он не пересчитывался.
        </p>

        {frozenYears.length > 0 && (
          <div className="space-y-1 mb-3">
            <div className="tg-section-header text-xs">Заморожено:</div>
            {frozenYears.map((f) => (
              <div
                key={f.year}
                className="flex items-center justify-between gap-2 py-1 px-2 rounded-lg bg-tg-secondary-bg"
              >
                <div className="text-sm">
                  <span className="font-medium">{f.year}</span>{' '}
                  <span className="text-tg-hint">
                    Тр={f.tr.toFixed(3)}, {f.deaths} погибших
                  </span>
                  {f.note && <div className="text-xs text-tg-hint italic">{f.note}</div>}
                </div>
                <button
                  className="text-xs text-red-600 dark:text-red-400"
                  onClick={() => onUnfreeze(f.year)}
                  disabled={unfreezePending}
                >
                  Разморозить
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="tg-section-header text-xs mb-1">Доступно для заморозки:</div>
        <div className="flex flex-wrap gap-1">
          {freezableYears.map((y) => {
            const isFrozen = frozenSet.has(y)
            const hist = data.history[String(y)]
            return (
              <button
                key={y}
                className={cn(
                  'px-2 py-1 rounded-lg text-xs',
                  isFrozen
                    ? 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300'
                    : 'bg-tg-secondary-bg hover:opacity-80'
                )}
                onClick={() => !isFrozen && onFreeze(y)}
                disabled={isFrozen || freezePending || !hist}
                title={hist ? `Тр=${hist.tr}, ${hist.deaths} погибших` : 'Нет данных за год'}
              >
                {y}
                {isFrozen && ' ✓'}
                {!isFrozen && hist && `: ${hist.tr.toFixed(2)}`}
              </button>
            )
          })}
        </div>
      </div>

      {/* Подвал: расчёт-время */}
      <div className="text-xs text-tg-hint text-center">
        Регион: {region.name} ({region.code}) · расчёт от {new Date(data.calculated_at).toLocaleString('ru-RU')}
      </div>
    </>
  )
}

// ============================================================
// KPI-карточка
// ============================================================

interface KpiCardProps {
  label: string
  value: string
  hint?: ReactNode
  highlight?: 'ok' | 'warning' | 'danger'
}

function KpiCard({ label, value, hint, highlight }: KpiCardProps) {
  return (
    <div
      className={cn(
        'tg-card !mb-0 flex flex-col justify-between',
        highlight === 'ok' && 'border-l-4 border-green-500',
        highlight === 'warning' && 'border-l-4 border-yellow-500',
        highlight === 'danger' && 'border-l-4 border-red-500',
      )}
    >
      <div>
        <div className="tg-section-header text-xs">{label}</div>
        <div
          className={cn(
            'text-xl font-bold mt-0.5',
            highlight && STATUS_COLORS[highlight],
          )}
        >
          {value}
        </div>
      </div>
      {hint && <div className="text-xs text-tg-hint mt-1 leading-snug">{hint}</div>}
    </div>
  )
}
