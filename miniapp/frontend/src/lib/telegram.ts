/**
 * Обёртка над Telegram WebApp SDK.
 *
 * Документация: https://core.telegram.org/bots/webapps
 *
 * В dev-режиме (вне Telegram) window.Telegram.WebApp недоступен —
 * возвращаем mock, чтобы UI не падал.
 */

declare global {
  interface Window {
    Telegram?: {
      WebApp?: TelegramWebApp
    }
  }
}

export interface TelegramUser {
  id: number
  first_name: string
  last_name?: string
  username?: string
  language_code?: string
  is_premium?: boolean
}

export interface TelegramWebApp {
  initData: string
  initDataUnsafe: {
    user?: TelegramUser
    auth_date?: number
    hash?: string
    start_param?: string
  }
  version: string
  platform: 'ios' | 'android' | 'web' | 'tdesktop' | 'unknown'
  colorScheme: 'light' | 'dark'
  themeParams: Record<string, string>
  isExpanded: boolean
  viewportHeight: number
  viewportStableHeight: number
  headerColor: string
  backgroundColor: string
  /**
   * true, если Mini App сейчас в полноэкранном режиме.
   * Доступно с версии WebApp SDK 8.0+.
   */
  isFullscreen?: boolean

  ready: () => void
  expand: () => void
  close: () => void
  setHeaderColor: (color: 'bg_color' | 'secondary_bg_color') => void
  setBackgroundColor: (color: string) => void
  enableClosingConfirmation: () => void
  disableVerticalSwipes?: () => void
  /**
   * Запросить переход в полноэкранный режим. Доступно с SDK 8.0+.
   * На некоторых платформах требует user gesture — вызывающая сторона
   * должна быть готова сделать fallback на обработчик клика.
   * Возвращает Promise, который резолвится при успехе и реджектится
   * при ошибке/отмене.
   */
  requestFullscreen?: () => Promise<void>
  exitFullscreen?: () => Promise<void>
  isVersionAtLeast?: (version: string) => boolean
  onEvent: (event: string, cb: () => void) => void
  offEvent: (event: string, cb: () => void) => void
  MainButton: {
    text: string
    color: string
    textColor: string
    isVisible: boolean
    isActive: boolean
    setText: (text: string) => void
    show: () => void
    hide: () => void
    enable: () => void
    disable: () => void
    onClick: (cb: () => void) => void
    offClick: (cb: () => void) => void
  }
  BackButton: {
    isVisible: boolean
    show: () => void
    hide: () => void
    onClick: (cb: () => void) => void
    offClick: (cb: () => void) => void
  }
  HapticFeedback: {
    impactOccurred: (style: 'light' | 'medium' | 'heavy' | 'rigid' | 'soft') => void
    notificationOccurred: (type: 'error' | 'success' | 'warning') => void
    selectionChanged: () => void
  }
  showAlert: (message: string, cb?: () => void) => void
  showConfirm: (message: string, cb: (ok: boolean) => void) => void
  openLink: (url: string) => void
}

function getWebApp(): TelegramWebApp | null {
  if (typeof window === 'undefined') return null
  return window.Telegram?.WebApp ?? null
}

let initialized = false

export function initTelegram(): void {
  if (initialized) return
  const wa = getWebApp()
  if (!wa) {
    console.warn(
      '[telegram] WebApp SDK не обнаружен. Запуск в dev-режиме вне Telegram.'
    )
    return
  }

  // Сигналим Telegram, что приложение готово к отображению
  wa.ready()
  // Разворачиваем Mini App на максимальную высоту внутри окна Telegram.
  // На мобильных — раскрывает MiniApp на весь экран (поведение по умолчанию).
  // На десктопе — раскрывает по высоте внутри текущего окна Telegram,
  // но НЕ меняет размер самого окна (для этого есть кнопка «Полный экран»).
  wa.expand()
  // Запрашиваем подтверждение перед закрытием (если есть активная задача)
  // wa.enableClosingConfirmation()

  // Применяем цветовую схему Telegram
  applyTheme(wa)

  // Блокируем вертикальные свайпы (iOS) — чтобы не закрывали Mini App
  try {
    wa.disableVerticalSwipes?.()
  } catch {
    // Метод доступен не на всех версиях SDK
  }

  // ВАЖНО: настоящий fullscreen (requestFullscreen) НЕ вызываем автоматически
  // при загрузке — он прячет панель задач и системные элементы управления,
  // что неудобно для десктопа. Пользователь может включить его кнопкой
  // «⤢ Полный экран» в шапке приложения.

  initialized = true
  console.info(
    `[telegram] WebApp initialized. Platform: ${wa.platform}, ` +
    `version: ${wa.version}, scheme: ${wa.colorScheme}`
  )
}

function applyTheme(wa: TelegramWebApp): void {
  const root = document.documentElement

  // Маппим themeParams в CSS-переменные
  const tp = wa.themeParams || {}
  const map: Record<string, string> = {
    bg_color: '--tg-color-bg',
    text_color: '--tg-color-text',
    hint_color: '--tg-color-hint',
    link_color: '--tg-color-link',
    button_color: '--tg-color-button',
    button_text_color: '--tg-color-button-text',
    secondary_bg_color: '--tg-color-secondary-bg',
    section_bg_color: '--tg-color-section-bg',
    section_header_text_color: '--tg-color-section-header-text',
    destructive_text_color: '--tg-color-destructive',
  }

  for (const [tgKey, cssVar] of Object.entries(map)) {
    if (tp[tgKey]) {
      root.style.setProperty(cssVar, tp[tgKey])
    }
  }

  // Dark mode
  if (wa.colorScheme === 'dark') {
    root.classList.add('dark')
  } else {
    root.classList.remove('dark')
  }

  // Фон страницы = фону Telegram
  if (tp.bg_color) {
    document.body.style.backgroundColor = tp.bg_color
  }
  if (tp.text_color) {
    document.body.style.color = tp.text_color
  }
}

export function getWebAppSafe(): TelegramWebApp | null {
  return getWebApp()
}

export function getInitData(): string {
  return getWebApp()?.initData ?? ''
}

export function getCurrentUser(): TelegramUser | null {
  return getWebApp()?.initDataUnsafe?.user ?? null
}

export function isInsideTelegram(): boolean {
  return !!getWebApp()
}

/**
 * Детектит Telegram Desktop (Windows/Mac/Linux).
 * На desktop Mini App открывается в вертикальном окне, которое
 * пользователь может растянуть мышью. Возвращаем true, чтобы UI
 * мог переключиться в широкую раскладку (max-w-5xl вместо max-w-xl).
 */
export function isTelegramDesktop(): boolean {
  const wa = getWebApp()
  if (!wa) {
    // Вне Telegram (dev-режим в браузере) — считаем десктопом по ширине окна
    return typeof window !== 'undefined' && window.innerWidth >= 900
  }
  return wa.platform === 'tdesktop'
}

/**
 * Возвращает рекомендуемый max-width для контейнера приложения.
 * На мобильных (ios/android) — узкая вертикальная раскладка.
 * На десктопе (tdesktop или широкий браузер) — широкая.
 */
export function getContainerMaxWidth(): string {
  return isTelegramDesktop() ? 'max-w-5xl' : 'max-w-xl'
}

export function haptic(
  type: 'light' | 'medium' | 'heavy' | 'success' | 'warning' | 'error' = 'light'
): void {
  const wa = getWebApp()
  if (!wa?.HapticFeedback) return
  if (type === 'success' || type === 'warning' || type === 'error') {
    wa.HapticFeedback.notificationOccurred(type)
  } else {
    wa.HapticFeedback.impactOccurred(type)
  }
}

export function showAlert(message: string): Promise<void> {
  return new Promise((resolve) => {
    const wa = getWebApp()
    if (wa?.showAlert) {
      wa.showAlert(message, () => resolve())
    } else {
      window.alert(message)
      resolve()
    }
  })
}

export function showConfirm(message: string): Promise<boolean> {
  return new Promise((resolve) => {
    const wa = getWebApp()
    if (wa?.showConfirm) {
      wa.showConfirm(message, (ok) => resolve(ok))
    } else {
      resolve(window.confirm(message))
    }
  })
}

export function setMainButton(
  text: string,
  onClick: () => void,
  options: { color?: string; textColor?: string } = {}
): () => void {
  const wa = getWebApp()
  if (!wa?.MainButton) return () => {}

  wa.MainButton.setText(text)
  if (options.color) wa.MainButton.color = options.color
  if (options.textColor) wa.MainButton.textColor = options.textColor
  wa.MainButton.onClick(onClick)
  wa.MainButton.show()
  wa.MainButton.enable()

  return () => {
    wa.MainButton.offClick(onClick)
    wa.MainButton.hide()
  }
}

export function hideMainButton(): void {
  getWebApp()?.MainButton?.hide()
}

/**
 * Доступен ли полноэкранный режим в текущем клиенте Telegram.
 * Проверяет и наличие метода requestFullscreen, и версию SDK >= 8.0.
 */
export function isFullscreenSupported(): boolean {
  const wa = getWebApp()
  if (!wa) return false
  if (typeof wa.requestFullscreen !== 'function') return false
  // На очень старых клиентах метод может быть определён, но не работать —
  // страхуемся проверкой версии SDK.
  if (typeof wa.isVersionAtLeast === 'function') {
    return wa.isVersionAtLeast('8.0')
  }
  // Если isVersionAtLeast нет — доверяем наличию requestFullscreen.
  return true
}

/**
 * Mini App сейчас в полноэкранном режиме?
 */
export function isFullscreenActive(): boolean {
  return !!getWebApp()?.isFullscreen
}

/**
 * Запросить полноэкранный режим. Возвращает Promise (ресолвится при успехе).
 * Если метод недоступен — Promise сразу реджектится.
 */
export function requestAppFullscreen(): Promise<void> {
  const wa = getWebApp()
  if (!wa || typeof wa.requestFullscreen !== 'function') {
    return Promise.reject(new Error('requestFullscreen is not supported'))
  }
  return wa.requestFullscreen()
}

/**
 * Выйти из полноэкранного режима.
 */
export function exitAppFullscreen(): Promise<void> {
  const wa = getWebApp()
  if (!wa || typeof wa.exitFullscreen !== 'function') {
    return Promise.reject(new Error('exitFullscreen is not supported'))
  }
  return wa.exitFullscreen()
}

/**
 * Подписка на изменение полноэкранного режима.
 * Возвращает функцию отписки.
 */
export function onFullscreenChange(cb: (isFullscreen: boolean) => void): () => void {
  const wa = getWebApp()
  if (!wa) return () => {}

  const handler = () => cb(!!wa.isFullscreen)
  wa.onEvent('fullscreenChanged', handler)
  return () => {
    wa.offEvent('fullscreenChanged', handler)
  }
}

/**
 * Развернуть Mini App на максимальную высоту внутри окна Telegram.
 *
 * На мобильных — раскрывает MiniApp на весь экран (поведение по умолчанию).
 *
 * На десктопе — раскрывает по высоте внутри текущего окна Telegram,
 * но НЕ меняет размер самого окна. Для полноэкранного режима используйте
 * requestAppFullscreen().
 */
export function expandApp(): void {
  getWebApp()?.expand()
}

/**
 * Текущее состояние expand (MiniApp развёрнут по высоте).
 */
export function isExpandedActive(): boolean {
  return !!getWebApp()?.isExpanded
}

/**
 * Подписка на изменение expand-режима.
 * Возвращает функцию отписки.
 */
export function onExpandedChange(cb: (isExpanded: boolean) => void): () => void {
  const wa = getWebApp()
  if (!wa) return () => {}

  const handler = () => cb(!!wa.isExpanded)
  wa.onEvent('viewportChanged', handler)
  return () => {
    wa.offEvent('viewportChanged', handler)
  }
}
