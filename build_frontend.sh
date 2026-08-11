#!/usr/bin/env bash
# ============================================================
# Сборка Mini App frontend для деплоя на bothost.ru
#
# Запускать ЛОКАЛЬНО (на вашей машине с установленным Node.js):
#   cd /path/to/gibdd-bot
#   bash build_frontend.sh
#
# После сборки загрузите папку miniapp/frontend/dist/
# на bothost вместе с остальным проектом.
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$SCRIPT_DIR/miniapp/frontend"
DIST_DIR="$FRONTEND_DIR/dist"

echo "=== Проверка Node.js ==="
if ! command -v node &>/dev/null; then
    echo "ОШИБКА: Node.js не установлен. Установите с https://nodejs.org (LTS)"
    exit 1
fi
echo "Node.js: $(node --version)"
echo "npm:     $(npm --version)"

echo ""
echo "=== Установка зависимостей ==="
cd "$FRONTEND_DIR"
if [ -f package-lock.json ]; then
    npm ci --no-audit --no-fund
else
    npm install --no-audit --no-fund
fi

echo ""
echo "=== Сборка ==="
npm run build

echo ""
echo "=== Готово ==="
if [ -d "$DIST_DIR" ]; then
    SIZE=$(du -sh "$DIST_DIR" | cut -f1)
    echo "Frontend собран в: $DIST_DIR"
    echo "Размер: $SIZE"
    echo ""
    echo "Файлы:"
    ls -lh "$DIST_DIR"
    echo ""
    echo "Теперь загрузите на bothost:"
    echo "  - Весь проект целиком (включая miniapp/frontend/dist/)"
    echo "  - ИЛИ только miniapp/frontend/dist/, если остальной код уже на bothost"
    echo ""
    echo "После деплоя проверьте: https://<BOTHOST_DOMAIN>/app/"
else
    echo "ОШИБКА: dist/ не создан"
    exit 1
fi
