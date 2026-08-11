/**
 * Хук для polling статуса длительных аналитических операций.
 *
 * Sprint 5: useLLMSummaryPolling УДАЛЁН — LLM-резюме теперь работает
 * только через SSE-стрим, без long-polling fallback'а на ?wait=25.
 * При монтировании вкладки LLMAnalysisView делает one-shot GET /llm/summary
 * (без wait), чтобы показать готовое резюме из кэша. Для генерации
 * используется POST /llm/summary/stream (SSE).
 *
 * Остался только useClustersPolling — кластеры не имеют SSE-эндпоинта.
 */
import { useQuery } from '@tanstack/react-query'
import { api, type ClustersResponse } from '@/lib/api'

const LONG_POLL_WAIT_SEC = 25  // сколько ждать на backend (до 60)
const REFETCH_AFTER_TIMEOUT_MS = 100  // почти мгновенно — long polling сам контролирует ритм
const REFETCH_INITIAL_MS = 1000  // первая попытка после запуска операции

// ============================================================
// Clusters polling (long polling)
// ============================================================
export function useClustersPolling(taskId: string | null, enabled: boolean) {
  return useQuery<ClustersResponse>({
    queryKey: ['clusters', taskId],
    queryFn: () => api.getClusters(taskId!, LONG_POLL_WAIT_SEC),
    enabled: !!taskId && enabled,
    refetchInterval: (query) => {
      const data = query.state.data
      if (!data) return REFETCH_INITIAL_MS
      if (
        data.state.status === 'done' ||
        data.state.status === 'failed'
      ) {
        return false
      }
      // running — long polling сам подождёт, повторяем сразу после его таймаута
      return REFETCH_AFTER_TIMEOUT_MS
    },
  })
}
