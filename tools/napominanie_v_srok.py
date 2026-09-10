#!/usr/bin/env python3
"""Разовое напоминание письмом В НАЗНАЧЕННЫЙ ЧАС.

ЗАЧЕМ (просьба заказчика 10.09.2026). Ежедневный пульс уходит в 09:07, а заказчику нужно
напоминание к 08:00. Своего расписания у меня нет: строку в crontab агент прописывать не
вправе (она запускает агента с отключёнными разрешениями), пакета `at` на машине нет —
проверено. Остаётся отсоединённый процесс, который спит до срока и отправляет письмо.

ЧЕГО ЭТОТ СПОСОБ НЕ ДАЁТ И ПОЧЕМУ ЭТО НЕ СТРАШНО: перезагрузка машины его убьёт. Поэтому
он НЕ единственный канал — то же напоминание лежит в ~/.addfut/napominanie-<дата>.txt и
уйдёт с обычным пульсом в 09:07, а пульс запускается системным cron и перезагрузку
переживает. Два независимых канала на одно напоминание — осознанно.

ВЫЗОВ: python3 napominanie_v_srok.py 'ГГГГ-ММ-ДД ЧЧ:ММ' 'тема' файл-с-текстом
"""
import datetime
import pathlib
import subprocess
import sys
import time

ЖУРНАЛ = pathlib.Path('~/.addfut/napominanie-v-srok.log').expanduser()
ПОЧТАЛЬОН = pathlib.Path(__file__).with_name('trevoga_mail.py')


def записать(строка):
    with open(ЖУРНАЛ, 'a', encoding='utf-8') as ф:
        ф.write(f"{datetime.datetime.now():%F %T} {строка}\n")


def главная():
    срок = datetime.datetime.strptime(sys.argv[1], '%Y-%m-%d %H:%M')
    тема, путь = sys.argv[2], pathlib.Path(sys.argv[3])
    осталось = (срок - datetime.datetime.now()).total_seconds()
    записать(f'жду до {срок:%F %H:%M} ({осталось / 3600:.1f} ч), тема: {тема}')
    if осталось > 0:
        time.sleep(осталось)
    # ТЕКСТ ЧИТАЕТСЯ В МОМЕНТ ОТПРАВКИ, А НЕ ПРИ ЗАПУСКЕ: за ночь напоминание может
    # устареть, и правка файла обязана попасть в письмо без перезапуска процесса.
    if not путь.exists():
        записать(f'ОТМЕНА: файла {путь} нет — напоминание снято, письмо НЕ отправлено')
        return 0
    текст = путь.read_text(encoding='utf-8')
    ход = subprocess.run([sys.executable, str(ПОЧТАЛЬОН), тема],
                         input=текст, text=True, capture_output=True, timeout=180)
    записать(f'отправка: код {ход.returncode}; {ход.stdout.strip()[:120]}')
    if ход.returncode != 0:
        записать(f'stderr: {ход.stderr.strip()[:200]}')
    return ход.returncode


if __name__ == '__main__':
    raise SystemExit(главная())
