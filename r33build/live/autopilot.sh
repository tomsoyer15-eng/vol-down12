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
close_after() {
    # Маршрут Е: ворота ДИНАМИЧЕСКИЕ (семнадцатый круг, №5) — из того же feed.e_close_gate,
    # что у замыкателя; фиксированное 1040 в недели рассинхронизации DST давало отказ
    # «рано» и тревогу вместо ожидания. Сбой вычисления — тревога и недостижимое время.
    if [ "$(route)" = E ]; then
        local g
        g=$(cd "$LIVE" && "$PY" -c 'import feed; print(f"{feed.e_close_gate():%H%M}")' 2>/dev/null)
        case "$g" in
            [0-9][0-9][0-9][0-9]) echo "$g"; return;;
        esac
        echo "ворота Е не вычислены (ответ: '$g')" > "$ST/ALARM-calendar-e.txt"
        (cd "$LIVE" && "$PY" diagnose.py "$ST/ALARM-calendar-e.txt" >> "$ST/ALARM-calendar-e.txt" 2>&1) || true
        echo 2359
    else
        echo 1605
    fi
}

# Свой замок В САМОМ СКРИПТЕ: строка crontab с flock — внешняя договорённость, её можно
# потерять при правке crontab; повторный вход должен быть невозможен независимо от неё.
exec 9>"$ST/autopilot.lock" 2>/dev/null || true
if ! flock -n 9; then
    # СТОРОЖ ЗАМКА (семнадцатый круг, №14): зависший владелец делал контур слепым — все
    # тики молча выходили, пропущенный ролл не замечал никто. Сердцебиение пишет владелец
    # в начале tick; его давность выше часа при занятом замке — тревога (пишется БЕЗ замка).
    hb="$ST/tick-heartbeat"
    if [ -f "$hb" ]; then
        age=$(( $(date +%s) - $(stat -c %Y "$hb" 2>/dev/null || echo 0) ))
        if [ "$age" -gt 3600 ] && [ ! -e "$ST/ALARM-lock.txt" ]; then
            {
                echo "замок занят, сердцебиение владельца устарело на ${age}с — процесс завис"
                echo "владелец замка (fuser):"
                fuser -v "$ST/autopilot.lock" 2>&1 | tail -3
            } > "$ST/ALARM-lock.txt"
            (cd "$LIVE" && "$PY" diagnose.py "$ST/ALARM-lock.txt" >> "$ST/ALARM-lock.txt" 2>&1) || true
            echo "$(date '+%F %T %Z') | ТРЕВОГА: замок автопилота завис (${age}с)" >> "$LOG"
        fi
    fi
    exit 0
fi
date +%s > "$ST/tick-heartbeat" 2>/dev/null || true

log() { echo "$(date '+%F %T %Z') | $*" >> "$LOG"; }

chicago() { TZ=America/Chicago date "+$1"; }

is_trade_day() {
    local dow; dow=$(chicago %u)                       # 6,7 — выходные
    [ "$dow" -ge 6 ] && return 1
    "$PY" - "$(route)" <<'PYEOF'
import sys
sys.path.insert(0, '/home/alex/claude-projects/vol-down12/r33build/live')
sys.path.insert(0, '/home/alex/claude-projects/vol-down12/r33build')
import pandas as pd, daily as DL, feed as FD, subprocess
d = pd.Timestamp(subprocess.run(['date', '+%F'], env={'TZ': 'America/Chicago'},
                                capture_output=True, text=True).stdout.strip())
route = sys.argv[1] if len(sys.argv) > 1 else 'F'
try:
    hol = (FD.eu_holidays(d.year) if route == 'E' else DL.holidays_for(d.year))
except Exception:
    sys.exit(2)          # непокрытый год: НЕ «выходной», а тревога у вызывающего
sys.exit(1 if d.normalize() in set(hol) else 0)
PYEOF
}

ensure_gw() {
    if exec 3<>/dev/tcp/127.0.0.1/4002 2>/dev/null; then
        exec 3<&-
        # ПОРТ — ТОЛЬКО ПОВОД ПОЖАТЬ РУКУ (одиннадцатый круг, №7): полуживой шлюз с
        # открытым сокетом прежде допускался к торговле в обход заявленной проверки API.
        if "$PY" - <<'HS0'
import sys
sys.path.insert(0, '/home/alex/claude-projects/vol-down12/r33build/live')
import tz
from ib_insync import IB
ib = IB()
try:
    ib.connect('127.0.0.1', 4002, clientId=97, timeout=15)
    ok = bool(ib.managedAccounts())
    ib.disconnect()
    sys.exit(0 if ok else 1)
except Exception:
    sys.exit(1)
HS0
        then rm -f "$ST/gw-fails"; return 0; fi
    fi
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
        (cd "$LIVE" && "$PY" diagnose.py "$ST/ALARM-gateway.txt" >> "$ST/ALARM-gateway.txt" 2>&1) || true
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
    elif (cd "$LIVE" && "$PY" - "$day" <<'BK' >/dev/null 2>&1
import sys
from pathlib import Path
import daily, state as ST
rt = Path.home() / '.addfut' / 'route.txt'
route = rt.read_text().strip() if rt.exists() else 'F'
cls = daily.BookE if route == 'E' else daily.Book
b, _, _ = ST.load(ST.book_path(route), cls)   # load сверяет digest: порча файла = не штатно
sys.exit(0 if (b is not None and b.last_session == sys.argv[1]) else 1)
BK
    )
    then
        # ШТАТНОСТЬ — ПО КНИГЕ (пятнадцатый круг, №3): день считается отторгованным, если
        # книга несёт СЕГОДНЯШНЮЮ дату. Фразы и коды возврата хрупки: реальный повтор дал
        # rc=2 «не замкнута», а не ожидавшийся rc=1 «не новее».
        # ЕДИНСТВЕННЫЙ штатный отказ — «день уже отторгован». Первая попытка сужения не
        # применилась из-за несовпавшей строки, и это увидела ВНЕШНЯЯ рецензия, а не моя
        # проверка: правка без последующего grep — не правка. Любой другой ОТКАЗ (сигнал,
        # множитель, незамкнутость, посторонние позиции) — тревога.
        touch "$ST/traded-$day"; log "торговля $day: штатный отказ (день уже отторгован)"
    else
        echo "$out" > "$ST/ALARM-trade-$day.txt"
        (cd "$LIVE" && "$PY" diagnose.py "$ST/ALARM-trade-$day.txt" >> "$ST/ALARM-trade-$day.txt" 2>&1) || true
        log "ТРЕВОГА торговли $day (код $rc) — автопилот остановлен до ручного разбора"
        return 1
    fi
}

run_close() {
    local day=$1 out rc
    out=$(cd "$LIVE" && timeout -k 30 400 "$PY" session.py --close --route "$(route)" 2>&1 | grep -vE '^Error [0-9]+'); rc=$?
    echo "$out" >> "$LOG"
    _closed_ok=0
    if [ $rc -ne 0 ]; then
        (cd "$LIVE" && "$PY" - "$day" <<'BK2' >/dev/null 2>&1
import sys
from pathlib import Path
import daily, state as ST
rt = Path.home() / '.addfut' / 'route.txt'
route = rt.read_text().strip() if rt.exists() else 'F'
cls = daily.BookE if route == 'E' else daily.Book
b, _, _ = ST.load(ST.book_path(route), cls)   # digest + ДАТА: чужой день не «уже замкнут»
sys.exit(0 if (b is not None and b.last_session == sys.argv[1]
               and b.close_provisional is False) else 1)
BK2
        ) && _closed_ok=1
    fi
    if [ $rc -eq 0 ] || [ "$_closed_ok" = 1 ]; then
        touch "$ST/closed-$day"; log "замыкание $day: ок"
        backup_state "$day"
    else
        echo "$out" > "$ST/ALARM-close-$day.txt"
        (cd "$LIVE" && "$PY" diagnose.py "$ST/ALARM-close-$day.txt" >> "$ST/ALARM-close-$day.txt" 2>&1) || true
        log "ТРЕВОГА замыкания $day (код $rc)"
        return 1
    fi
}

tick() {
    local day hm
    day=$(chicago %F); hm=$(chicago %H%M)
    ls "$ST"/ALARM-*.txt >/dev/null 2>&1 && return 0     # тревога: стоим до ручного разбора
    is_trade_day; _td=$?
    if [ "$_td" -eq 2 ]; then
        echo "календарь маршрута $(route) не покрывает текущий год" > "$ST/ALARM-calendar.txt"
        (cd "$LIVE" && "$PY" diagnose.py "$ST/ALARM-calendar.txt" >> "$ST/ALARM-calendar.txt" 2>&1) || true
        log "ТРЕВОГА: календарь не покрыт — автопилот остановлен"
        return 0
    fi
    [ "$_td" -eq 0 ] || return 0
    if [ "$hm" -ge "$(trade_till)" ] && [ ! -e "$ST/traded-$day" ] && [ ! -e "$ST/ALARM-missed-$day.txt" ]; then
        # ДЕНЬ ПРОПУЩЕН: все тики до конца окна потеряны (машина спала). Торговать в 16:05
        # и тут же замыкать — значит оставить рыночные GTC на ночь; вместо этого тревога.
        echo "тики торгового окна $day пропущены (первый после $hm)" > "$ST/ALARM-missed-$day.txt"
        (cd "$LIVE" && "$PY" diagnose.py "$ST/ALARM-missed-$day.txt" >> "$ST/ALARM-missed-$day.txt" 2>&1) || true
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
            if sout=$(cd "$LIVE" && timeout -k 30 300 "$PY" signal_update.py 2>&1 | grep -vE '^Error [0-9]+'); then
                echo "$sout" >> "$LOG"; touch "$ST/sigup-$mon"; log "сигнал $mon: обновлён/сверен"
            else
                echo "$sout" > "$ST/ALARM-signal-$mon.txt"
                (cd "$LIVE" && "$PY" diagnose.py "$ST/ALARM-signal-$mon.txt" >> "$ST/ALARM-signal-$mon.txt" 2>&1) || true
                log "ТРЕВОГА сигнала $mon — торговли не будет до ручного разбора"
                return 0
            fi
        fi
        # ОКНО ПЕРЕПРОВЕРЯЕТСЯ ПОСЛЕ ПОДГОТОВКИ (семнадцатый круг, №13): подъём шлюза и
        # месячный сигнал занимают минуты — заявка, поданная за краем окна, висела бы GTC
        # до чужой сессии. Пропущенное окно поймает штатный механизм ALARM-missed.
        hm=$(chicago %H%M)
        if [ "$hm" -ge "$(trade_till)" ]; then
            log "окно ушло за время подготовки (сейчас $hm) — торговля не начинается"
        else
            run_trade "$day"
        fi
    fi
    if [ "$hm" -ge "$(close_after)" ] && [ -e "$ST/traded-$day" ] && [ ! -e "$ST/closed-$day" ]; then
        ensure_gw && run_close "$day"
    fi
    find "$ST" -maxdepth 1 -name 'traded-*' -mtime +14 -delete 2>/dev/null
    find "$ST" -maxdepth 1 -name 'closed-*' -mtime +14 -delete 2>/dev/null
}

backup_state() {
    # РЕЗЕРВНАЯ КОПИЯ МАШИННОГО СОСТОЯНИЯ после каждого замыкания: книга, журнал §7, живой
    # ряд сигналов и уровни. СЕКРЕТЫ (ibgw.env) НЕ КОПИРУЮТСЯ. Храним 30 последних.
    # УНИКАЛЬНОЕ ИМЯ И АТОМАРНАЯ ПУБЛИКАЦИЯ (тринадцатый круг, №10): повторное событие
    # той же даты (замыкание после перехода) не затирает предыдущий снимок, падение tar не
    # оставляет усечённый архив под рабочим именем.
    local day=$1 bdir=$HOME/.addfut-backups stamp
    stamp=$(date -u +%H%M%S)
    mkdir -p "$bdir"
    if tar -czf "$bdir/.addfut-$day-$stamp.tmp" -C "$HOME" \
        --exclude='.addfut/ibgw.env' --exclude='.addfut/zoneinfo' \
        --exclude='.addfut/*.lock' .addfut 2>/dev/null \
       && mv "$bdir/.addfut-$day-$stamp.tmp" "$bdir/addfut-$day-$stamp.tgz"; then
        log "резервная копия состояния: addfut-$day-$stamp.tgz"
        # WORM-ЯКОРЬ ВНЕ МАШИНЫ (решение заказчика 13.08.2026): корень цепочки §7 и
        # digest книги — датированным файлом в репозиторий, коммитом до выгрузки; подмена
        # прошлого потребует force-push с расхождением двух удалённых историй.
        if wa=$(cd "$LIVE" && "$PY" worm_anchor.py "$day" 2>&1); then
            log "WORM: $wa"
            ( cd /home/alex/claude-projects/vol-down12 \
              && git add anchors/ >/dev/null 2>&1 \
              && git commit -q -m "WORM-якорь $day" >/dev/null 2>&1 ) || true
        else
            echo "WORM-якорь $day не создан: $wa" > "$ST/ALARM-worm-$day.txt"
            (cd "$LIVE" && "$PY" diagnose.py "$ST/ALARM-worm-$day.txt" >> "$ST/ALARM-worm-$day.txt" 2>&1) || true
            log "ТРЕВОГА: WORM-якорь не создан — автопилот остановлен"
            return 1
        fi
        # ВНЕШНЯЯ ВЫГРУЗКА ПОД ПРОВЕРКОЙ (пятнадцатый круг, №7): разовый сбой сети — не
        # тревога (локальная копия есть, догонит следующим замыканием), но три подряд
        # означают, что внешних копий давно нет, — стоим до ручного разбора.
        if timeout -k 30 180 /bin/bash /home/alex/claude-projects/vol-down12/tools/backup_push.sh >>"$LOG" 2>&1; then
            rm -f "$ST/push-fails"
        else
            local pf=0
            [ -f "$ST/push-fails" ] && pf=$(cat "$ST/push-fails")
            pf=$((pf + 1)); echo "$pf" > "$ST/push-fails"
            log "внешняя выгрузка копий не удалась ($pf подряд)"
            if [ "$pf" -ge 3 ]; then
                echo "backup_push.sh не удаётся $pf замыканий подряд — внешних копий нет" > "$ST/ALARM-push.txt"
                (cd "$LIVE" && "$PY" diagnose.py "$ST/ALARM-push.txt" >> "$ST/ALARM-push.txt" 2>&1) || true
                log "ТРЕВОГА: внешние копии не выгружаются — автопилот остановлен"
                return 1
            fi
        fi
    else
        rm -f "$bdir/.addfut-$day-$stamp.tmp"
        echo "tar не создал копию состояния за $day" > "$ST/ALARM-backup-$day.txt"
        (cd "$LIVE" && "$PY" diagnose.py "$ST/ALARM-backup-$day.txt" >> "$ST/ALARM-backup-$day.txt" 2>&1) || true
        log "ТРЕВОГА: резервная копия $day не создана — автопилот остановлен"
        return 1
    fi
    ls -1t "$bdir"/addfut-*.tgz 2>/dev/null | tail -n +61 | xargs -r rm -f
}

status() {
    local day; day=$(chicago %F)
    echo "Чикаго: $(chicago '%F %H:%M %Z'); торговый день: $(is_trade_day && echo да || echo нет)"
    echo "сегодня: торговля $( [ -e "$ST/traded-$day" ] && echo сделана || echo нет ), \
замыкание $( [ -e "$ST/closed-$day" ] && echo сделано || echo нет )"
    ls "$ST"/ALARM-*.txt 2>/dev/null && echo "^^^ ТРЕВОГА: автопилот стоит" || echo "тревог нет"
    tail -5 "$LOG" 2>/dev/null
}

guard_manual() {
    # РУЧНОЙ ВХОД ПРОХОДИТ ТЕ ЖЕ ВОРОТА (четырнадцатый круг, №10): вызов trade в 15:00 по
    # маршруту Е отправил бы GTC на закрытую Европу, а без sigup — торговлю без сигнала.
    ls "$ST"/ALARM-*.txt >/dev/null 2>&1 && { echo "стоит ТРЕВОГА — сначала разбор"; return 1; }
    is_trade_day; [ $? -eq 0 ] || { echo "не торговый день маршрута $(route)"; return 1; }
    return 0
}

case "${1:-tick}" in
    tick)   tick ;;
    trade)
        guard_manual || exit 1
        hm=$(chicago %H%M); day=$(chicago %F)
        { [ "$hm" -ge "$(trade_from)" ] && [ "$hm" -lt "$(trade_till)" ]; } \
            || { echo "вне торгового окна маршрута $(route)"; exit 1; }
        mon=$(chicago %Y-%m)
        [ -e "$ST/sigup-$mon" ] || { echo "сигнал месяца не обновлён (sigup-$mon)"; exit 1; }
        ensure_gw && run_trade "$day" ;;
    close)
        guard_manual || exit 1
        hm=$(chicago %H%M)
        [ "$hm" -ge "$(close_after)" ] || { echo "до окна замыкания маршрута $(route)"; exit 1; }
        ensure_gw && run_close "$(chicago %F)" ;;
    diagnose)
        found=0
        for f in "$ST"/ALARM-*.txt; do
            [ -e "$f" ] || continue
            found=1
            echo "=== $f ==="
            (cd "$LIVE" && "$PY" diagnose.py "$f") || true
        done
        [ "$found" = 1 ] || echo "тревог нет"
        ;;
    status) status ;;
    *) echo "команды: tick|trade|close|status"; exit 1 ;;
esac
