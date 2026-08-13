#!/usr/bin/env bash
# Автопилот бумажного этапа ADD-FUT: работает из системного cron, вне сессии Claude.
#
# УСТРОЙСТВО. Cron дёргает `tick` каждые 10 минут; тикер САМ считает время Чикаго
# (America/Chicago), поэтому переходы на зимнее/летнее время ничего не сдвигают. Торговая
# сессия — раз в день после 08:45 по бирже, замыкание — раз в день после 16:05; отметки
# «сделано за дату» лежат в ~/.addfut. Праздники биржи берутся из той же таблицы, что и
# ролл (daily.holidays_for): в праздник тикер не торгует.
#
# ДИСЦИПЛИНА ОТКАЗА — КАК У КОНТУРА. Любая ошибка торговли пишет файл-тревогу
# ~/.addfut/ALARM-*.txt, и автопилот БОЛЬШЕ НЕ ТОРГУЕТ, пока человек не удалит файл:
# автоматика не лечит расхождения руками. «Уже замкнута» при замыкании — штатный ответ.
#
# Секреты: логин/пароль шлюза — в ~/.addfut/ibgw.env (600), НЕ в репозитории.
# pipefail ОБЯЗАТЕЛЕН: без него код возврата принадлежал grep в конце конвейера, и НАСТОЯЩАЯ
# авария питона читалась как успех — тревога не сработала бы никогда. Найдено первым же
# пробным тиком.
set -u -o pipefail
ROOT=/home/alex/claude-projects/vol-down12
PY=$ROOT/.venv/bin/python
LIVE=$ROOT/r33build/live
ST=$HOME/.addfut
LOG=$ST/autopilot.log
ENVF=$ST/ibgw.env
ROUTE_F=$ST/route.txt            # действующий маршрут пишет hand_over_book; нет файла — Ф

route() { cat "$ROUTE_F" 2>/dev/null || echo F; }

# ОКНА ПО МАРШРУТУ (десятый круг, №8): фонды маршрута Е торгуются в Европе (LSE/EBS,
# закрытие ~10:30 Чикаго). Торговать Е в 15:00 Чикаго значит вешать рыночные GTC на ночь
# чужой биржи; замыкание Е возможно уже после европейского закрытия.
trade_from()  { [ "$(route)" = E ] && echo 0845 || echo 0845; }
trade_till()  { [ "$(route)" = E ] && echo 0945 || echo 1530; }
close_after() { [ "$(route)" = E ] && echo 1040 || echo 1605; }

# Свой замок В САМОМ СКРИПТЕ: строка crontab с flock — внешняя договорённость, её можно
# потерять при правке crontab; повторный вход должен быть невозможен независимо от неё.
exec 9>"$ST/autopilot.lock" 2>/dev/null || true
flock -n 9 || exit 0

log() { echo "$(date '+%F %T %Z') | $*" >> "$LOG"; }

chicago() { TZ=America/Chicago date "+$1"; }

is_trade_day() {
    local dow; dow=$(chicago %u)                       # 6,7 — выходные
    [ "$dow" -ge 6 ] && return 1
    "$PY" - <<PYEOF
import sys
sys.path.insert(0, '$LIVE'); sys.path.insert(0, '$ROOT/r33build')
import pandas as pd, daily as DL
d = pd.Timestamp('$(chicago %F)')
sys.exit(1 if d.normalize() in set(DL.holidays_for(d.year)) else 0)
PYEOF
}

ensure_gw() {
    if exec 3<>/dev/tcp/127.0.0.1/4002 2>/dev/null; then exec 3<&-; rm -f "$ST/gw-fails"; return 0; fi
    log "шлюз не отвечает — запускаю"
    [ -f "$ENVF" ] || { log "ТРЕВОГА: нет $ENVF — запускать шлюз нечем"; return 1; }
    set -a; . "$ENVF"; set +a
    ( cd "$LIVE/ibgw" && nohup ./start.sh >> "$ST/ibgw-launch.log" 2>&1 & )
    for _ in $(seq 1 30); do
        sleep 10
        if exec 3<>/dev/tcp/127.0.0.1/4002 2>/dev/null; then exec 3<&-; break; fi
    done
    # ПОРТ — НЕ РУКОПОЖАТИЕ: сокет может слушать при мёртвом API. Пробуем подключиться.
    if "$PY" - <<'HS'
import sys
sys.path.insert(0, '/home/alex/claude-projects/vol-down12/r33build/live')
import tz
from ib_insync import IB
ib = IB()
try:
    ib.connect('127.0.0.1', 4002, clientId=96, timeout=20)
    ok = bool(ib.managedAccounts())
    ib.disconnect()
    sys.exit(0 if ok else 1)
except Exception:
    sys.exit(1)
HS
    then rm -f "$ST/gw-fails"; log "шлюз поднят, API отвечает"; return 0; fi
    n=$(( $(cat "$ST/gw-fails" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$ST/gw-fails"
    log "шлюз/API не отвечает (подряд: $n)"
    if [ "$n" -ge 3 ]; then
        echo "шлюз или API мертвы три тика подряд" > "$ST/ALARM-gateway.txt"
        log "ТРЕВОГА: шлюз мёртв три тика подряд — автопилот остановлен"
    fi
    return 1
}

run_trade() {
    local day=$1 out rc
    out=$(cd "$LIVE" && timeout -k 30 500 "$PY" session.py --live --route "$(route)" 2>&1 | grep -vE '^Error [0-9]+'); rc=$?
    echo "$out" >> "$LOG"
    if [ $rc -eq 0 ]; then
        touch "$ST/traded-$day"; log "торговля $day: ок"
    elif echo "$out" | grep -qE 'не новее'; then
        # ЕДИНСТВЕННЫЙ штатный отказ — «день уже отторгован». Первая попытка сужения не
        # применилась из-за несовпавшей строки, и это увидела ВНЕШНЯЯ рецензия, а не моя
        # проверка: правка без последующего grep — не правка. Любой другой ОТКАЗ (сигнал,
        # множитель, незамкнутость, посторонние позиции) — тревога.
        touch "$ST/traded-$day"; log "торговля $day: штатный отказ (день уже отторгован)"
    else
        echo "$out" > "$ST/ALARM-trade-$day.txt"
        log "ТРЕВОГА торговли $day (код $rc) — автопилот остановлен до ручного разбора"
    fi
}

run_close() {
    local day=$1 out rc
    out=$(cd "$LIVE" && timeout -k 30 400 "$PY" session.py --close --route "$(route)" 2>&1 | grep -vE '^Error [0-9]+'); rc=$?
    echo "$out" >> "$LOG"
    if [ $rc -eq 0 ] || echo "$out" | grep -q 'уже замкнута'; then
        touch "$ST/closed-$day"; log "замыкание $day: ок"
    else
        echo "$out" > "$ST/ALARM-close-$day.txt"
        log "ТРЕВОГА замыкания $day (код $rc)"
    fi
}

tick() {
    local day hm
    day=$(chicago %F); hm=$(chicago %H%M)
    ls "$ST"/ALARM-*.txt >/dev/null 2>&1 && return 0     # тревога: стоим до ручного разбора
    is_trade_day || return 0
    if [ "$hm" -ge "$(trade_till)" ] && [ ! -e "$ST/traded-$day" ] && [ ! -e "$ST/ALARM-missed-$day.txt" ]; then
        # ДЕНЬ ПРОПУЩЕН: все тики до конца окна потеряны (машина спала). Торговать в 16:05
        # и тут же замыкать — значит оставить рыночные GTC на ночь; вместо этого тревога.
        echo "тики торгового окна $day пропущены (первый после $hm)" > "$ST/ALARM-missed-$day.txt"
        log "ТРЕВОГА: торговое окно $day пропущено — сессии не будет"
        return 0
    fi
    if [ "$hm" -ge "$(trade_from)" ] && [ "$hm" -lt "$(trade_till)" ] && [ ! -e "$ST/traded-$day" ]; then
        ensure_gw || return 0
        # МЕСЯЧНЫЙ СИГНАЛ ПЕРЕД ПЕРВОЙ ТОРГОВЛЕЙ МЕСЯЦА: строка месяца действия обязана
        # существовать до сделки; сбой обновления — тревога, торговли нет.
        local mon; mon=$(chicago %Y-%m)
        if [ ! -e "$ST/sigup-$mon" ]; then
            local sout
            if sout=$(cd "$LIVE" && timeout 300 "$PY" signal_update.py 2>&1 | grep -vE '^Error [0-9]+'); then
                echo "$sout" >> "$LOG"; touch "$ST/sigup-$mon"; log "сигнал $mon: обновлён/сверен"
            else
                echo "$sout" > "$ST/ALARM-signal-$mon.txt"
                log "ТРЕВОГА сигнала $mon — торговли не будет до ручного разбора"
                return 0
            fi
        fi
        run_trade "$day"
    fi
    if [ "$hm" -ge 1605 ] 2>/dev/null && [ -e "$ST/traded-$day" ] && [ ! -e "$ST/closed-$day" ]; then
        ensure_gw && run_close "$day"
    fi
    find "$ST" -maxdepth 1 -name 'traded-*' -mtime +14 -delete 2>/dev/null
    find "$ST" -maxdepth 1 -name 'closed-*' -mtime +14 -delete 2>/dev/null
}

status() {
    local day; day=$(chicago %F)
    echo "Чикаго: $(chicago '%F %H:%M %Z'); торговый день: $(is_trade_day && echo да || echo нет)"
    echo "сегодня: торговля $( [ -e "$ST/traded-$day" ] && echo сделана || echo нет ), \
замыкание $( [ -e "$ST/closed-$day" ] && echo сделано || echo нет )"
    ls "$ST"/ALARM-*.txt 2>/dev/null && echo "^^^ ТРЕВОГА: автопилот стоит" || echo "тревог нет"
    tail -5 "$LOG" 2>/dev/null
}

case "${1:-tick}" in
    tick)   tick ;;
    trade)  ensure_gw && run_trade "$(chicago %F)" ;;
    close)  ensure_gw && run_close "$(chicago %F)" ;;
    status) status ;;
    *) echo "команды: tick|trade|close|status"; exit 1 ;;
esac
