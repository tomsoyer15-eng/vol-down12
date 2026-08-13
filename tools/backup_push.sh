#!/usr/bin/env bash
# Выгрузка копий в разные места. Вызывается автопилотом после замыкания дня.
# origin (GitHub) может ещё не существовать — это не ошибка, а ожидание репозитория.
set -u
cd /home/alex/claude-projects/vol-down12 || exit 0
git push -q mirror master 2>/dev/null && echo "$(date '+%F %T') зеркало ok" >> ~/.addfut/autopilot.log
git push -q origin master 2>/dev/null && echo "$(date '+%F %T') github ok" >> ~/.addfut/autopilot.log
# бэкапы состояния — вторая локальная точка (другой каталог, переживает rm -rf проекта)
mkdir -p ~/state-mirror && cp -u ~/.addfut-backups/addfut-*.tgz ~/state-mirror/ 2>/dev/null
