#!/usr/bin/env bash
# Запуск IB Gateway на машине без монитора.
#
# Шлюз IBKR — приложение с окном и без X11 не стартует вовсе (HeadlessException на
# заставке, ещё до входа в учётную запись). Поэтому поднимается виртуальный экран Xvfb,
# а входом и удержанием сессии занимается IBC.
#
# УЧЁТНЫЕ ДАННЫЕ В ЭТОТ ФАЙЛ И В config.ini НЕ ВПИСЫВАЮТСЯ. Логин и пароль берутся из
# окружения (IB_LOGIN, IB_PASSWORD) и подставляются во ВРЕМЕННУЮ копию конфигурации с
# правами 600, которая удаляется при выходе. В репозиторий попадает только шаблон.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IBC_DIR="${IBC_DIR:-$HOME/ibc}"
GW_DIR="${GW_DIR:-$HOME/Jts}"
GW_VER="${GW_VER:-1045}"
DISPLAY_NUM="${DISPLAY_NUM:-:97}"
MODE="${TRADING_MODE:-paper}"

command -v Xvfb >/dev/null || {
  echo "ОШИБКА: Xvfb не установлен. Нужен один раз и с правами root:" >&2
  echo "    sudo apt-get install -y xvfb" >&2
  exit 3
}
[ -d "$GW_DIR/ibgateway/$GW_VER/jars" ] || { echo "ОШИБКА: шлюз не найден в $GW_DIR" >&2; exit 3; }
[ -f "$IBC_DIR/IBC.jar" ]  || { echo "ОШИБКА: IBC не найден в $IBC_DIR" >&2; exit 3; }
: "${IB_LOGIN:?задайте IB_LOGIN в окружении}"
: "${IB_PASSWORD:?задайте IB_PASSWORD в окружении}"

TMP_INI="$(mktemp)"; chmod 600 "$TMP_INI"
XVFB_PID=""
cleanup() {
  rm -f "$TMP_INI"
  [ -n "$XVFB_PID" ] && kill "$XVFB_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

python3 - "$HERE/config.ini" "$TMP_INI" <<'PY'
import os, re, sys
src, dst = sys.argv[1], sys.argv[2]
s = open(src).read()
s = re.sub(r'(?m)^IbLoginId=.*$',  'IbLoginId='  + os.environ['IB_LOGIN'],    s)
s = re.sub(r'(?m)^IbPassword=.*$', 'IbPassword=' + os.environ['IB_PASSWORD'], s)
open(dst, 'w').write(s)
PY

# ЖИВОЙ ДИСПЛЕЙ ПЕРЕИСПОЛЬЗУЕТСЯ (инцидент 14.08.2026; двадцать второй круг, №11).
# Прежде скрипт БЕЗУСЛОВНО поднимал свой Xvfb на :97 и выходил кодом 4, если дисплей уже
# занят. А занят он был с 12.08 — с первого запуска. Поэтому суточный перезапуск шлюза,
# не поднявший API, оказался неисправимым: ensure_gw трижды звал start.sh, тот трижды
# падал на «Xvfb не поднялся», и автопилот остановился тревогой. Позиция осталась бы без
# ребаланса и ролла до ручного вмешательства.
#
# ЧУЖОЙ ДИСПЛЕЙ НЕ УБИВАЕМ И НЕ ЧИНИМ (О-5): если сервер на :97 жив — просто работаем на
# нём, а свой не поднимаем и в cleanup не трогаем.
if xdpyinfo -display "$DISPLAY_NUM" >/dev/null 2>&1 \
   || ps -eo args= | grep -qE "^Xvfb $DISPLAY_NUM( |$)"; then
  echo "экран $DISPLAY_NUM уже поднят — используем существующий (свой не запускаем)"
else
  # Осиротевший замок без процесса мешает старту: снимаем ТОЛЬКО если сервера нет.
  if [ -e "/tmp/.X${DISPLAY_NUM#:}-lock" ]; then
    echo "замок /tmp/.X${DISPLAY_NUM#:}-lock без живого Xvfb — снимаю" >&2
    rm -f "/tmp/.X${DISPLAY_NUM#:}-lock"
  fi
  Xvfb "$DISPLAY_NUM" -screen 0 1280x1024x24 -nolisten tcp >/dev/null 2>&1 &
  XVFB_PID=$!
  sleep 2
  kill -0 "$XVFB_PID" 2>/dev/null || { echo "ОШИБКА: Xvfb не поднялся" >&2; exit 4; }
fi

export DISPLAY="$DISPLAY_NUM"
echo "экран $DISPLAY_NUM поднят (pid $XVFB_PID); шлюз $GW_VER, режим $MODE"
echo "порт API: $(grep -E '^OverrideTwsApiPort=' "$HERE/config.ini" | cut -d= -f2)"
echo "ReadOnlyApi: $(grep -E '^ReadOnlyApi=' "$HERE/config.ini" | cut -d= -f2) (заявки запрещены, пока стоит yes)"

# gatewaystart.sh НЕ разбирает аргументы: это шаблон с зашитыми путями, который полагается
# править. Поэтому пусковой файл генерируется из него подстановкой — так исходник IBC
# остаётся нетронутым, а пути и версия задаются нами.
LAUNCH="$(mktemp)"; chmod 700 "$LAUNCH"
sed -e "s|^IBC_PATH=.*|IBC_PATH=$IBC_DIR|" \
    -e "s|^TWS_PATH=.*|TWS_PATH=$GW_DIR|" \
    -e "s|^IBC_INI=.*|IBC_INI=$TMP_INI|" \
    -e "s|^TWS_MAJOR_VRSN=.*|TWS_MAJOR_VRSN=$GW_VER|" \
    -e "s|^TRADING_MODE=.*|TRADING_MODE=$MODE|" \
    -e "s|^TWS_SETTINGS_PATH=.*|TWS_SETTINGS_PATH=$HOME/.ibgw-settings|" \
    -e "s|^LOG_PATH=.*|LOG_PATH=$HOME/.ibgw-logs|" \
    "$IBC_DIR/gatewaystart.sh" > "$LAUNCH"

# НЕ exec. Он подменяет процесс оболочки, поэтому trap на выходе не отработает никогда и
# временный файл с паролем останется на диске вместе с Xvfb. Шлюз запускается потомком,
# оболочка ждёт его и убирает за собой при любом исходе, включая сигнал.
"$LAUNCH" -inline &
GW_PID=$!
cleanup_all() { rm -f "$TMP_INI" "$LAUNCH"; kill "$GW_PID" 2>/dev/null || true;
                [ -n "$XVFB_PID" ] && kill "$XVFB_PID" 2>/dev/null || true; }
trap cleanup_all EXIT INT TERM
wait "$GW_PID"
