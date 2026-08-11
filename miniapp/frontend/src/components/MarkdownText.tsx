/**
 * MarkdownText — лёгкий markdown-рендерер для LLM-ответов.
 *
 * Sprint 5: LLM теперь может возвращать markdown (bold, italic, заголовки,
 * списки, code). Этот компонент рендерит текст через marked() и
 * санитизирует результат перед вставкой в DOM.
 *
 * Особенности:
 *  - streaming mode: добавляет мигающий курсор ▌ в конце текста
 *  - sanitize: удаляем <script>, <iframe>, on* атрибуты (LLM не должен
 *    их генерить, но подстраховка от prompt injection не лишняя)
 *  - стили под Telegram Mini App (var(--tg-color-*))
 *
 * Зависимости: marked (npm install marked) — ~25KB в bundle.
 */
import { useMemo } from 'react'
import { marked } from 'marked'

// Конфигурируем marked один раз: GitHub-flavored markdown без громоздких опций.
marked.setOptions({
  gfm: true,
  breaks: true, // \n → <br> (одиночный перенос строки тоже виден)
})

interface MarkdownTextProps {
  text: string
  /** Показывать мигающий курсор в конце (для streaming-режима). */
  streaming?: boolean
}

/**
 * Санитизирует HTML: удаляет опасные теги и атрибуты.
 * marked по умолчанию НЕ пропускает HTML в исходном тексте (экранирует),
 * но если включить gfm, могут проскочить inline-HTML. Подстрахуемся.
 */
function sanitizeHtml(html: string): string {
  // Удаляем <script>, <iframe>, <object>, <embed>, <style> целиком
  html = html.replace(
    /<(script|iframe|object|embed|style|link)[\s\S]*?<\/\1\s*>/gi,
    '',
  )
  // Удаляем все on* атрибуты (onclick, onload и т.д.)
  html = html.replace(/\son\w+\s*=\s*"[^"]*"/gi, '')
  html = html.replace(/\son\w+\s*=\s*'[^']*'/gi, '')
  // Удаляем javascript: URL
  html = html.replace(/(href|src)\s*=\s*["']javascript:[^"']*["']/gi, '$1="#"')
  return html
}

export function MarkdownText({ text, streaming = false }: MarkdownTextProps) {
  const html = useMemo(() => {
    if (!text) return ''
    try {
      const raw = marked.parse(text, { async: false }) as string
      return sanitizeHtml(raw)
    } catch {
      // Если marked упал — показываем сырой текст (экранированный браузером)
      return text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/\n/g, '<br/>')
    }
  }, [text])

  return (
    <div
      className="md-text"
      style={{
        // Стили для типичных markdown-элементов — переопределяем дефолтный
        // marked-стиль, чтобы вписаться в Telegram Mini App тему.
        // Используем CSS-переменные темы для совместимости с dark/light.
        ['--md-accent' as any]: 'var(--tg-color-button, #2481cc)',
        ['--md-text' as any]: 'var(--tg-color-text, #000)',
        ['--md-secondary' as any]: 'var(--tg-color-subtitle, #888)',
        ['--md-bg' as any]: 'var(--tg-color-secondary-bg, #f1f1f1)',
        ['--md-border' as any]: 'var(--tg-color-section-separator, rgba(0,0,0,0.1))',
      }}
    >
      <div dangerouslySetInnerHTML={{ __html: html }} />
      {streaming && (
        <span
          className="md-cursor"
          style={{
            display: 'inline-block',
            marginLeft: '2px',
            animation: 'md-blink 1s steps(2) infinite',
            color: 'var(--md-accent)',
            fontWeight: 'bold',
          }}
        >
          ▌
        </span>
      )}
      {/* Inline <style> для markdown-элементов — один раз на каждый компонент.
          Дублирование незначительное (~500 байт), зато стили изолированы
          от глобального CSS. */}
      <style>{`
        .md-text h1, .md-text h2, .md-text h3, .md-text h4 {
          font-size: 1em;
          font-weight: 700;
          margin: 0.6em 0 0.3em;
          color: var(--md-text);
        }
        .md-text h1 { font-size: 1.15em; }
        .md-text h2 { font-size: 1.1em; }
        .md-text h3 { font-size: 1.05em; }
        .md-text h4 { font-size: 1em; opacity: 0.85; }
        .md-text p { margin: 0.4em 0; line-height: 1.5; }
        .md-text ul, .md-text ol {
          margin: 0.4em 0;
          padding-left: 1.4em;
        }
        .md-text li { margin: 0.15em 0; line-height: 1.45; }
        .md-text ul li { list-style: disc; }
        .md-text ol li { list-style: decimal; }
        .md-text strong { font-weight: 700; color: var(--md-text); }
        .md-text em { font-style: italic; }
        .md-text code {
          background: var(--md-bg);
          padding: 1px 4px;
          border-radius: 3px;
          font-family: ui-monospace, "SF Mono", Menlo, monospace;
          font-size: 0.9em;
        }
        .md-text pre {
          background: var(--md-bg);
          padding: 8px 10px;
          border-radius: 6px;
          overflow-x: auto;
          margin: 0.5em 0;
        }
        .md-text pre code {
          background: transparent;
          padding: 0;
        }
        .md-text blockquote {
          border-left: 3px solid var(--md-accent);
          padding-left: 10px;
          margin: 0.5em 0;
          color: var(--md-secondary);
          font-style: italic;
        }
        .md-text a {
          color: var(--md-accent);
          text-decoration: underline;
        }
        .md-text table {
          border-collapse: collapse;
          margin: 0.5em 0;
          font-size: 0.9em;
          display: block;
          overflow-x: auto;
        }
        .md-text th, .md-text td {
          border: 1px solid var(--md-border);
          padding: 4px 8px;
          text-align: left;
        }
        .md-text th { background: var(--md-bg); font-weight: 600; }
        .md-text hr {
          border: none;
          border-top: 1px solid var(--md-border);
          margin: 0.6em 0;
        }
        @keyframes md-blink {
          0%, 50% { opacity: 1; }
          50.01%, 100% { opacity: 0; }
        }
      `}</style>
    </div>
  )
}
