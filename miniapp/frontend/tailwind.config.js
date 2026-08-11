/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      // Поддержка Telegram color scheme (tg-color-bg, tg-theme-* и т.д.)
      colors: {
        'tg-bg': 'var(--tg-color-bg, #ffffff)',
        'tg-text': 'var(--tg-color-text, #000000)',
        'tg-hint': 'var(--tg-color-hint, #999999)',
        'tg-link': 'var(--tg-color-link, #2481cc)',
        'tg-button': 'var(--tg-color-button, #2481cc)',
        'tg-button-text': 'var(--tg-color-button-text, #ffffff)',
        'tg-secondary-bg': 'var(--tg-color-secondary-bg, #f1f1f1)',
        'tg-section-bg': 'var(--tg-color-section-bg, #ffffff)',
        'tg-section-header': 'var(--tg-color-section-header-text, #999999)',
        'tg-destructive': 'var(--tg-color-destructive, #ff3b30)',
      },
      fontFamily: {
        sans: ['var(--tg-font, system-ui, -apple-system, sans-serif)'],
      },
    },
  },
  plugins: [],
}
