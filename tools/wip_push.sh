#!/usr/bin/env bash
# ОНЛАЙН-СТРАХОВКА ОТ ПОТЕРИ РАБОТЫ (решение заказчика 19.08.2026).
#
# ПОВОД: сессия помощника разорвалась, и результат работы пропал — незакоммиченные правки
# жили в единственном экземпляре на этой машине до самого конца круга. Прежний порядок
# («коммит и пуш после выпуска») защищает только ЗАВЕРШЁННОЕ; окно между правкой и выпуском
# оставалось непокрытым часами.
#
# ЧТО ДЕЛАЕТ (из системного cron, каждые полчаса):
#   1) все НЕЗАПУШЕННЫЕ коммиты уходят в зеркало и на GitHub;
#   2) НЕЗАКОММИЧЕННЫЕ правки tracked-файлов и новые (untracked, не игнорируемые) файлы
#      копируются ПОБАЙТОВО на Google Drive в wip/<дата>/ с --checksum: без изменений
#      выгрузка пуста и бесплатна, а копия не проходит через модель (правило 16.08).
#
# ЧЕГО НЕ ДЕЛАЕТ ОСОЗНАННО: не коммитит и не меняет дерево — незрелая работа не попадает
# в историю и не может уронить выпуск; wip/ на Drive — страховка, а не источник истины.
# Секреты не выгружаются: скрипт работает только в каталоге проекта, ~/.addfut не читает.
set -u -o pipefail
ROOT=/home/alex/claude-projects/vol-down12
LOG=$HOME/.addfut/wip-push.log
RCLONE="$HOME/bin/rclone"
DRIVE_REMOTE="${ADDFUT_DRIVE_REMOTE:-vol-down12-drive:vol-down12-backups/}"
cd "$ROOT" || exit 0

# ОДИН ЭКЗЕМПЛЯР: перекрытие с самим собой или с backup_push бессмысленно и шумно.
exec 9>"$HOME/.addfut/wip-push.lock" 2>/dev/null || exit 0
flock -n 9 || exit 0

log() { echo "$(date '+%F %T') | $*" >> "$LOG"; }

# --- 1) незапушенные коммиты ----------------------------------------------------------
for R in mirror origin; do
    AHEAD=$(git rev-list --count "$R/master..master" 2>/dev/null || echo '?')
    if [ "$AHEAD" = '?' ]; then
        log "wip: $R недоступен для сравнения — пробую пуш вслепую"
        timeout -k 15 120 git push -q "$R" master 2>/dev/null \
            && log "wip: $R догнал (пуш вслепую)" || log "wip: ОТКАЗ пуша в $R"
    elif [ "$AHEAD" != "0" ]; then
        if timeout -k 15 120 git push -q "$R" master 2>/dev/null; then
            log "wip: $R догнал master (+$AHEAD коммитов)"
        else
            # МОЛЧАТЬ ПРИ НЕСНЯТОЙ КОПИИ ЗАПРЕЩЕНО (класс route.txt, §7 PROJECT-STATE).
            log "wip: ОТКАЗ пуша в $R (+$AHEAD не выгружены)"
        fi
    fi
done

# --- 2) незакоммиченная работа -> Drive wip/<дата>/ -----------------------------------
LIST=$(mktemp /tmp/wip-list-XXXX)
{ git diff --name-only; git diff --cached --name-only; \
  git ls-files --others --exclude-standard; } 2>/dev/null | sort -u \
  | while IFS= read -r f; do [ -f "$f" ] && printf '%s\n' "$f"; done > "$LIST"
if [ -s "$LIST" ]; then
    if [ -x "$RCLONE" ]; then
        DST="${DRIVE_REMOTE}wip/$(date +%F)/"
        if timeout -k 30 300 "$RCLONE" copy "$ROOT" "$DST" --files-from "$LIST" \
               --checksum -q 2>>"$LOG"; then
            log "wip: на Drive выгружено срезом $(wc -l < "$LIST") файл(ов) -> $DST"
        else
            log "wip: ОТКАЗ выгрузки на Drive ($(wc -l < "$LIST") файлов НЕ покрыты)"
        fi
    else
        log "wip: Drive ПРОПУЩЕН — нет $RCLONE, незакоммиченное не покрыто"
    fi
fi
unlink "$LIST" 2>/dev/null || true
