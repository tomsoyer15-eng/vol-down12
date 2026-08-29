#!/usr/bin/env bash
# Плановый наблюдатель paper pilot: запускается системным cron по будням утром.
# Оболочка — только запускалка (правило 8д): вся задача описана в nablyudatel-prompt.md,
# решения принимает безголовая сессия Claude, читающая CLAUDE.md и PROJECT-STATE.
# Работает и при закрытой интерактивной сессии — в этом весь смысл (поручение 29.08.2026).
set -u
DIR="/home/alex/claude-projects/vol-down12"
LOG="$HOME/.addfut/nablyudatel.log"
export PATH="$HOME/.local/bin:$PATH"
cd "$DIR" || { echo "$(date '+%F %T') наблюдатель: нет каталога проекта" >> "$LOG"; exit 3; }
echo "$(date '+%F %T') наблюдатель: старт" >> "$LOG"
# --dangerously-skip-permissions: сессия без человека не может отвечать на запросы прав.
# Сторож правила 11 (PreToolUse-хук) при этом ОСТАЁТСЯ активен и режет разрушительное.
timeout 2400 claude -p --dangerously-skip-permissions "$(cat "$DIR/tools/nablyudatel-prompt.md")" \
  >> "$LOG" 2>&1
RC=$?
echo "$(date '+%F %T') наблюдатель: завершён, код $RC" >> "$LOG"
exit "$RC"
