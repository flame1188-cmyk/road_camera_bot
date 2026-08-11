import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/** Объединяет классы Tailwind с правильным разрешением конфликтов. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}

/** Форматирует размер файла в человекочитаемый вид. */
export function formatSize(bytes: number): string {
  if (bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(1024))
  return `${(bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

/** Человекочитаемый статус задачи. */
export function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: 'Ожидание',
    fetching: 'Выгрузка данных ГИБДД',
    parsing: 'Парсинг карточек',
    analytics: 'Расчёт аналитики',
    generating: 'Генерация файлов',
    done: 'Готово',
    failed: 'Ошибка',
  }
  return labels[status] ?? status
}
