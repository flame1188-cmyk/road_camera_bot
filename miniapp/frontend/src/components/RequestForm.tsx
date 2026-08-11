/**
 * Форма запроса: ввод естественным языком или строгий формат.
 */
import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { api, type ParseResult } from '@/lib/api'
import { haptic, showAlert } from '@/lib/telegram'
import { cn } from '@/lib/utils'

interface RequestFormProps {
  onTaskCreated: (taskId: string) => void
}

const EXAMPLES = [
  'Вологодская область за 2025 год',
  'Алтайский край за I квартал 2025',
  'Москва за декабрь 2025',
  '2.2024 1119',
]

export function RequestForm({ onTaskCreated }: RequestFormProps) {
  const [query, setQuery] = useState('')

  // Мутация парсинга (для preview)
  const parseMutation = useMutation({
    mutationFn: (q: string) => api.parseQuery(q),
    onSuccess: (result: ParseResult) => {
      if (result.ok) {
        haptic('success')
      } else {
        haptic('warning')
      }
    },
  })

  // Мутация создания задачи
  const createMutation = useMutation({
    mutationFn: (q: string) => api.createTask({ query: q }),
    onSuccess: (data) => {
      haptic('success')
      onTaskCreated(data.task_id)
    },
    onError: async (err: Error) => {
      haptic('error')
      await showAlert(`Не удалось создать задачу:\n${err.message}`)
    },
  })

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const trimmed = query.trim()
    if (trimmed.length < 2) {
      haptic('warning')
      return
    }
    createMutation.mutate(trimmed)
  }

  const handlePreview = () => {
    const trimmed = query.trim()
    if (trimmed.length < 2) return
    parseMutation.mutate(trimmed)
  }

  const parseResult = parseMutation.data
  const isLoading = createMutation.isPending

  return (
    <div className="tg-card">
      <div className="tg-section-header">Запрос данных ДТП</div>

      <form onSubmit={handleSubmit} className="space-y-3">
        <textarea
          className="tg-input resize-none"
          rows={3}
          placeholder="Например: Вологодская область за 2025 год"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          disabled={isLoading}
          autoFocus
        />

        {/* Подсказки */}
        <div className="flex flex-wrap gap-2">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              type="button"
              onClick={() => {
                setQuery(ex)
                haptic('light')
              }}
              disabled={isLoading}
              className="text-xs px-2.5 py-1 rounded-full"
              style={{
                backgroundColor: 'var(--tg-color-secondary-bg, #f1f1f1)',
                color: 'var(--tg-color-link, #2481cc)',
              }}
            >
              {ex}
            </button>
          ))}
        </div>

        {/* Preview парсинга */}
        {parseMutation.isPending && (
          <div className="text-xs opacity-60">Парсим запрос…</div>
        )}
        {parseResult?.ok && (
          <div className="text-xs p-2.5 rounded-lg" style={{
            backgroundColor: 'var(--tg-color-secondary-bg, #f1f1f1)',
          }}>
            <div className="font-medium mb-0.5">
              Регион: {parseResult.region_name} ({parseResult.region_code})
            </div>
            <div className="opacity-70">Период: {parseResult.period}</div>
          </div>
        )}
        {parseResult && !parseResult.ok && (
          <div className="text-xs p-2.5 rounded-lg" style={{
            backgroundColor: 'rgba(255, 59, 48, 0.1)',
            color: 'var(--tg-color-destructive, #ff3b30)',
          }}>
            {parseResult.error}
          </div>
        )}

        {/* Кнопки */}
        <div className="flex gap-2">
          <button
            type="button"
            onClick={handlePreview}
            disabled={isLoading || query.trim().length < 2}
            className="flex-1 py-3 px-4 rounded-xl font-medium text-sm transition-opacity active:opacity-80"
            style={{
              backgroundColor: 'var(--tg-color-secondary-bg, #f1f1f1)',
              color: 'var(--tg-color-link, #2481cc)',
            }}
          >
            Проверить
          </button>
          <button
            type="submit"
            className={cn('flex-[2] py-3 px-4 rounded-xl font-semibold text-sm')}
            disabled={isLoading || query.trim().length < 2}
            style={{
              backgroundColor: 'var(--tg-color-button, #2481cc)',
              color: 'var(--tg-color-button-text, #ffffff)',
            }}
          >
            {isLoading ? 'Создаём задачу…' : 'Выгрузить данные'}
          </button>
        </div>
      </form>
    </div>
  )
}
