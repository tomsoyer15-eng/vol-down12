#!/usr/bin/env bash
# Выгрузка копий в разные места. Вызывается автопилотом после замыкания дня.
# origin (GitHub) может ещё не существовать — это не ошибка, а ожидание репозитория.
set -u
cd /home/alex/claude-projects/vol-down12 || exit 0
git push -q mirror master 2>/dev/null && echo "$(date '+%F %T') зеркало ok" >> ~/.addfut/autopilot.log
git push -q origin master 2>/dev/null && echo "$(date '+%F %T') github ok" >> ~/.addfut/autopilot.log
# бэкапы состояния — вторая локальная точка (другой каталог, переживает rm -rf проекта)
mkdir -p ~/state-mirror && cp -u ~/.addfut-backups/addfut-*.tgz ~/state-mirror/ 2>/dev/null

# --- GOOGLE DRIVE: ПОБАЙТОВАЯ КОПИЯ (16.08.2026, решение заказчика) -------------------
# Прежде на Drive попадали только ПЕРЕПЕЧАТКИ через сессию помощника: текст проходил через
# модель, а не через файловую систему, поэтому копия не была побайтовой и не могла служить
# доказательством. rclone копирует байты и сверяет контрольную сумму на стороне Google.
#
# ОТСУТСТВИЕ rclone ИЛИ УДАЛЁНКИ — НЕ ОТКАЗ ВЫГРУЗКИ, но и НЕ МОЛЧАНИЕ: строка в журнале
# обязана появиться в любом случае, иначе «копия есть» и «копию не пытались снять»
# выглядят одинаково — тот же класс, что молчаливое умолчание route.txt (§7).
RCLONE="$HOME/bin/rclone"
# ПОДПАПКА ЯВНО (17.08, при подключении Drive): корень Диска — личный, там лежит всё
# остальное; выгрузка обязана идти в утверждённую заказчиком папку vol-down12-backups,
# а не рассыпаться каталогами по корню.
DRIVE_REMOTE="${ADDFUT_DRIVE_REMOTE:-vol-down12-drive:vol-down12-backups/}"
if [ ! -x "$RCLONE" ]; then
  echo "$(date '+%F %T') drive ПРОПУЩЕН: нет $RCLONE" >> ~/.addfut/autopilot.log
elif ! "$RCLONE" listremotes 2>/dev/null | grep -qx "${DRIVE_REMOTE%%:*}:"; then
  # СВЕРЯЕТСЯ ИМЯ УДАЛЁНКИ, А НЕ ВСЯ СТРОКА: listremotes печатает «имя:», а DRIVE_REMOTE
  # может нести и подпапку («имя:papka/»). Точное сравнение со всей строкой объявляло бы
  # настроенную удалёнку отсутствующей и МОЛЧА пропускало выгрузку — поймано прогоном на
  # локальной удалёнке.
  echo "$(date '+%F %T') drive ПРОПУЩЕН: удалёнка ${DRIVE_REMOTE} не настроена" >> ~/.addfut/autopilot.log
else
  # Что выгружаем (состав утверждён заказчиком 16.08): корень доверия, история выпусков,
  # состояние проекта, нормативный текст, все тексты рецензий, датированные архивы
  # выпусков, сжатые бэкапы состояния счёта и bundle всего репозитория.
  _drv_ok=1
  for _pair in \
      "docs/r33-sha256.txt:koren" \
      "releases/INDEX.txt:koren" \
      "PROJECT-STATE.md:sostoyanie" \
      "docs/addfut-v1.6.0-r33.txt:normativ" ; do
    _src="${_pair%%:*}"; _dst="${_pair##*:}"
    "$RCLONE" copy --checksum "$_src" "${DRIVE_REMOTE}${_dst}/" 2>>~/.addfut/autopilot.log || _drv_ok=0
  done
  # рецензии и датированные пакеты — каталогами целиком
  "$RCLONE" copy --checksum --include 'kritika-krug*.md' docs "${DRIVE_REMOTE}retsenzii/" \
      2>>~/.addfut/autopilot.log || _drv_ok=0
  "$RCLONE" copy --checksum --include 'paket-r33-*.zip' releases "${DRIVE_REMOTE}vypuski/" \
      2>>~/.addfut/autopilot.log || _drv_ok=0
  # БЭКАПЫ СОСТОЯНИЯ СЧЁТА (решение заказчика 16.08): книга, журнал §7, реестр, замер маржи.
  # Это данные торгового счёта, и они уходят в облако сознательно — ради восстановления
  # живого контура с нуля вне машины. Локальные точки (~/state-mirror) остаются.
  "$RCLONE" copy --checksum --include 'addfut-*.tgz' ~/.addfut-backups "${DRIVE_REMOTE}sostoyanie-scheta/" \
      2>>~/.addfut/autopilot.log || _drv_ok=0
  # ЗЕРКАЛО РЕПОЗИТОРИЯ — ОДНИМ BUNDLE, А НЕ ПОФАЙЛОВОЙ КОПИЕЙ КАТАЛОГА (решение заказчика
  # 16.08). Копирование bare-репозитория по файлам даёт НЕСОГЛАСОВАННЫЙ снимок: паки и
  # loose-объекты меняются во время обхода, и восстановленная копия может оказаться битой —
  # тот же класс, что «два снимка позиций» в §8. Bundle снимается атомарно, сам себя
  # проверяет (`git bundle verify`) и разворачивается обычным `git clone <файл>`.
  _bnd="$HOME/git-mirrors/vol-down12.bundle"
  if git bundle create "$_bnd.tmp" --all >/dev/null 2>&1 && git bundle verify "$_bnd.tmp" >/dev/null 2>&1; then
    mv -f "$_bnd.tmp" "$_bnd"
    "$RCLONE" copy --checksum "$_bnd" "${DRIVE_REMOTE}git-zerkalo/" 2>>~/.addfut/autopilot.log || _drv_ok=0
  else
    rm -f "$_bnd.tmp"
    echo "$(date '+%F %T') drive: bundle НЕ СНЯТ или не прошёл verify" >> ~/.addfut/autopilot.log
    _drv_ok=0
  fi
  if [ "$_drv_ok" = 1 ]; then
    echo "$(date '+%F %T') drive ok" >> ~/.addfut/autopilot.log
  else
    echo "$(date '+%F %T') drive ОШИБКА выгрузки — копия вне машины НЕ обновлена" >> ~/.addfut/autopilot.log
  fi
fi
