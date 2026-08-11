/**
 * StructuredForm — форма с выбором региона из списка и периода чипами.
 *
 * Заменяет прежний текстовый ввод: никаких ошибок распознавания,
 * пользователь выбирает регион из 82 существующих и отмечает месяцы.
 *
 * Логика:
 *  - Регион: combobox с поиском по имени (использует /api/regions).
 *  - Год: переключатель 2023 / 2024 / 2025 / 2026.
 *  - Месяцы: 12 чипов, можно выбрать несколько (или все через пресет).
 *  - Пресеты: «Весь год», «I квартал», «II квартал», «Полгода».
 *
 * Блок сворачиваемый:
 *  - По умолчанию развёрнут (пользователь выбирает параметры).
 *  - Автоматически сворачивается после успешного нажатия «Выгрузить данные».
 *  - В свёрнутом виде показывает краткую сводку региона + периода и
 *    кнопку «Развернуть» для повторного запроса с другими параметрами.
 *
 * Отправляет на backend уже готовый region_code + dat_list —
 * парсинг текста не выполняется.
 */
import { useMemo, useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { api, type Region } from '@/lib/api'
import { haptic, showAlert } from '@/lib/telegram'
import { cn } from '@/lib/utils'

interface StructuredFormProps {
  onTaskCreated: (taskId: string) => void
}

const MONTH_LABELS = [
  'Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн',
  'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек',
]

const YEARS = [2026, 2025, 2024, 2023]

const PRESETS: { label: string; months: number[] }[] = [
  { label: 'Весь год', months: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12] },
  { label: 'I квартал', months: [1, 2, 3] },
  { label: 'II квартал', months: [4, 5, 6] },
  { label: 'III квартал', months: [7, 8, 9] },
  { label: 'IV квартал', months: [10, 11, 12] },
  { label: 'Полгода', months: [1, 2, 3, 4, 5, 6] },
]

export function StructuredForm({ onTaskCreated }: StructuredFormProps) {
  // === Состояние формы ===
  const [regionQuery, setRegionQuery] = useState('')
  const [selectedRegion, setSelectedRegion] = useState<Region | null>(null)
  const [year, setYear] = useState<number>(YEARS[0])
  const [selectedMonths, setSelectedMonths] = useState<number[]>([])

  // Сворачиваемый блок: по умолчанию развёрнут.
  // После успешной отправки задачи — автоматически сворачивается.
  const [collapsed, setCollapsed] = useState(false)

  // === Загрузка регионов ===
  const regionsQuery = useQuery({
    queryKey: ['regions'],
    queryFn: api.listRegions,
    staleTime: 5 * 60 * 1000, // 5 минут — справочник не меняется часто
  })

  // Фильтрация по подстроке (на клиенте, регионов всего 82)
  const filteredRegions = useMemo(() => {
    if (!regionsQuery.data) return []
    const q = regionQuery.trim().toLowerCase()
    if (!q) return regionsQuery.data
    return regionsQuery.data.filter((r) =>
      r.name.toLowerCase().includes(q) || r.code.includes(q)
    )
  }, [regionsQuery.data, regionQuery])

  // === Мутация создания задачи ===
  const createMutation = useMutation({
    mutationFn: () => {
      if (!selectedRegion) throw new Error('Выберите регион')
      if (selectedMonths.length === 0) throw new Error('Выберите период')
      const dat_list = selectedMonths.map((m) => `${m}.${year}`)
      const period_label = buildPeriodLabel(selectedMonths, year)
      return api.createStructuredTask({
        region_code: selectedRegion.code,
        region_name: selectedRegion.name,
        dat_list,
        period_label,
      })
    },
    onSuccess: (data) => {
      haptic('success')
      // Автоматически сворачиваем форму после успешной отправки,
      // чтобы освободить место для прогресс-индикатора и результатов.
      setCollapsed(true)
      onTaskCreated(data.task_id)
    },
    onError: async (err: Error) => {
      haptic('error')
      await showAlert(`Не удалось создать задачу:\n${err.message}`)
    },
  })

  // === Обработчики ===
  const handleMonthToggle = (m: number) => {
    haptic('light')
    setSelectedMonths((prev) =>
      prev.includes(m) ? prev.filter((x) => x !== m) : [...prev, m].sort((a, b) => a - b)
    )
  }

  const handlePreset = (months: number[]) => {
    haptic('medium')
    setSelectedMonths([...months])
  }

  const handleSelectRegion = (r: Region) => {
    haptic('light')
    setSelectedRegion(r)
    setRegionQuery('')
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!selectedRegion || selectedMonths.length === 0) {
      haptic('warning')
      return
    }
    createMutation.mutate()
  }

  const handleToggleCollapse = () => {
    haptic('light')
    setCollapsed((v) => !v)
  }

  const isLoading = createMutation.isPending
  const canSubmit = selectedRegion !== null && selectedMonths.length > 0 && !isLoading
  const periodLabel = selectedMonths.length > 0
    ? buildPeriodLabel(selectedMonths, year)
    : ''

  // === Свёрнутый режим: компактная полоска с кнопкой «Развернуть» ===
  if (collapsed) {
    return (
      <div className="tg-card">
        <button
          type="button"
          onClick={handleToggleCollapse}
          className="w-full flex items-center justify-between text-left active:opacity-70"
        >
          <div className="min-w-0 flex-1 pr-3">
            <div className="text-[10px] opacity-60 uppercase tracking-wider mb-0.5">
              Параметры запроса
            </div>
            <div className="text-sm font-medium truncate">
              {selectedRegion ? selectedRegion.name : 'Регион не выбран'}
              {selectedRegion && (
                <span className="opacity-50"> · код {selectedRegion.code}</span>
              )}
            </div>
            <div className="text-xs opacity-70 truncate">
              {periodLabel || 'Период не выбран'}
            </div>
          </div>
          <div
            className="shrink-0 px-3 py-2 rounded-lg text-xs font-medium"
            style={{
              backgroundColor: 'var(--tg-color-secondary-bg, #f1f1f1)',
              color: 'var(--tg-color-link, #2481cc)',
            }}
          >
            ▼ Развернуть
          </div>
        </button>
      </div>
    )
  }

  // === Развёрнутый режим: полная форма ===
  return (
    <div className="tg-card">
      <div className="flex items-center justify-between mb-3">
        <div className="tg-section-header">Запрос данных ДТП</div>
        {/* Кнопка свернуть (иконка ▲) — видна только если уже есть выбранные параметры */}
        {canSubmit && (
          <button
            type="button"
            onClick={handleToggleCollapse}
            className="text-xs px-2 py-1 rounded-md"
            style={{
              backgroundColor: 'var(--tg-color-secondary-bg, #f1f1f1)',
              color: 'var(--tg-color-link, #2481cc)',
            }}
            title="Свернуть"
          >
            ▲ Свернуть
          </button>
        )}
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        {/* === Регион === */}
        <div>
          <label className="block text-xs opacity-60 mb-1.5">Регион</label>
          {selectedRegion ? (
            <button
              type="button"
              onClick={() => {
                haptic('light')
                setSelectedRegion(null)
              }}
              className="w-full text-left p-3 rounded-xl flex items-center justify-between"
              style={{
                backgroundColor: 'var(--tg-color-secondary-bg, #f1f1f1)',
              }}
            >
              <span className="text-sm font-medium">{selectedRegion.name}</span>
              <span className="text-xs opacity-50">код {selectedRegion.code} · изменить</span>
            </button>
          ) : (
            <div>
              <input
                type="text"
                className="tg-input"
                placeholder="Начните вводить название региона…"
                value={regionQuery}
                onChange={(e) => setRegionQuery(e.target.value)}
                disabled={isLoading}
                autoFocus
              />
              {regionsQuery.isLoading && (
                <div className="text-xs opacity-60 mt-1.5">Загружаем справочник…</div>
              )}
              {regionsQuery.isError && (
                <div className="text-xs mt-1.5" style={{ color: 'var(--tg-color-destructive, #ff3b30)' }}>
                  Не удалось загрузить регионы. Попробуйте позже.
                </div>
              )}
              {filteredRegions.length > 0 && (
                <div
                  className="mt-1.5 rounded-xl overflow-hidden max-h-52 overflow-y-auto"
                  style={{
                    backgroundColor: 'var(--tg-color-secondary-bg, #f1f1f1)',
                  }}
                >
                  {filteredRegions.slice(0, 30).map((r) => (
                    <button
                      key={r.code}
                      type="button"
                      onClick={() => handleSelectRegion(r)}
                      disabled={isLoading}
                      className="w-full text-left px-3 py-2.5 text-sm active:opacity-70"
                      style={{ borderBottom: '1px solid rgba(0,0,0,0.05)' }}
                    >
                      <div className="font-medium">{r.name}</div>
                      <div className="text-xs opacity-50">код {r.code}</div>
                    </button>
                  ))}
                  {filteredRegions.length > 30 && (
                    <div className="px-3 py-2 text-xs opacity-50 text-center">
                      …и ещё {filteredRegions.length - 30}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* === Год === */}
        <div>
          <label className="block text-xs opacity-60 mb-1.5">Год</label>
          <div className="flex gap-1.5">
            {YEARS.map((y) => (
              <button
                key={y}
                type="button"
                onClick={() => {
                  haptic('light')
                  setYear(y)
                }}
                disabled={isLoading}
                className={cn('flex-1 py-2 rounded-lg text-sm font-medium')}
                style={{
                  backgroundColor:
                    year === y
                      ? 'var(--tg-color-button, #2481cc)'
                      : 'var(--tg-color-secondary-bg, #f1f1f1)',
                  color:
                    year === y
                      ? 'var(--tg-color-button-text, #ffffff)'
                      : 'var(--tg-color-text, #000000)',
                }}
              >
                {y}
              </button>
            ))}
          </div>
        </div>

        {/* === Месяцы === */}
        <div>
          <label className="block text-xs opacity-60 mb-1.5">Месяцы</label>
          <div className="grid grid-cols-6 gap-1.5 mb-2">
            {MONTH_LABELS.map((label, idx) => {
              const month = idx + 1
              const selected = selectedMonths.includes(month)
              return (
                <button
                  key={month}
                  type="button"
                  onClick={() => handleMonthToggle(month)}
                  disabled={isLoading}
                  className="py-2 rounded-lg text-xs font-medium"
                  style={{
                    backgroundColor: selected
                      ? 'var(--tg-color-button, #2481cc)'
                      : 'var(--tg-color-secondary-bg, #f1f1f1)',
                    color: selected
                      ? 'var(--tg-color-button-text, #ffffff)'
                      : 'var(--tg-color-text, #000000)',
                  }}
                >
                  {label}
                </button>
              )
            })}
          </div>
          {/* Пресеты */}
          <div className="flex flex-wrap gap-1.5">
            {PRESETS.map((p) => {
              const active =
                selectedMonths.length === p.months.length &&
                p.months.every((m) => selectedMonths.includes(m))
              return (
                <button
                  key={p.label}
                  type="button"
                  onClick={() => handlePreset(p.months)}
                  disabled={isLoading}
                  className="text-xs px-2.5 py-1 rounded-full"
                  style={{
                    backgroundColor: active
                      ? 'var(--tg-color-button, #2481cc)'
                      : 'var(--tg-color-secondary-bg, #f1f1f1)',
                    color: active
                      ? 'var(--tg-color-button-text, #ffffff)'
                      : 'var(--tg-color-link, #2481cc)',
                  }}
                >
                  {p.label}
                </button>
              )
            })}
          </div>
        </div>

        {/* === Превью === */}
        {canSubmit && (
          <div
            className="text-xs p-2.5 rounded-lg"
            style={{ backgroundColor: 'var(--tg-color-secondary-bg, #f1f1f1)' }}
          >
            <div className="font-medium mb-0.5">
              {selectedRegion?.name} ({selectedRegion?.code})
            </div>
            <div className="opacity-70">{periodLabel}</div>
          </div>
        )}

        {/* === Submit === */}
        <button
          type="submit"
          className="w-full py-3 px-4 rounded-xl font-semibold text-sm transition-opacity active:opacity-80"
          disabled={!canSubmit}
          style={{
            backgroundColor: 'var(--tg-color-button, #2481cc)',
            color: 'var(--tg-color-button-text, #ffffff)',
            opacity: canSubmit ? 1 : 0.5,
          }}
        >
          {isLoading ? 'Создаём задачу…' : 'Выгрузить данные'}
        </button>
      </form>
    </div>
  )
}

// ============================================================
// Helpers
// ============================================================
function buildPeriodLabel(months: number[], year: number): string {
  if (months.length === 0) return ''
  if (months.length === 12) return `${year} год`
  if (months.length === 1) {
    const monthName = [
      'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
      'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
    ][months[0] - 1]
    return `${monthName} ${year}`
  }
  // Сортируем и проверяем — квартал ли это
  const sorted = [...months].sort((a, b) => a - b)
  const quarterPresets: Record<string, number[]> = {
    'I квартал': [1, 2, 3],
    'II квартал': [4, 5, 6],
    'III квартал': [7, 8, 9],
    'IV квартал': [10, 11, 12],
    '1-е полугодие': [1, 2, 3, 4, 5, 6],
    '2-е полугодие': [7, 8, 9, 10, 11, 12],
  }
  for (const [label, m] of Object.entries(quarterPresets)) {
    if (m.length === sorted.length && m.every((v, i) => v === sorted[i])) {
      return `${label} ${year}`
    }
  }
  // Произвольный набор месяцев
  return `${months.length} мес. ${year}`
}
