#!/usr/bin/env python3
"""Страница статуса на Google Drive: «зашёл — глянул — убедился», обновляется внутри дня.

ЗАЧЕМ (поручение заказчика 29.08.2026). Дублированный канал к почте: письма приходят по
событиям и по утрам, а страница отвечает в любой момент дня. Её ВРЕМЯ ОБНОВЛЕНИЯ — второй
пульс: файл пишется каждые полчаса, и если отметка старше сорока минут, значит машина или
её выгрузка лежат. Мёртвая машина не может обновить страницу — свежесть и есть проверка.

Чистый питон + rclone (уже настроен для копий). Без Claude и без шлюза: страница читает
ФАЙЛЫ (книга, журналы), а не дёргает брокера — внутридневная переоценка счёта сознательно
не запрашивается, NLV даётся на последнее замыкание. Любой кусок данных не смеет валить
страницу: недоступное честно помечается «?».
"""
import datetime
import json
import os
import pathlib
import re
import subprocess
import time

ДОМ = pathlib.Path('~/.addfut').expanduser()
ПРОЕКТ = pathlib.Path(__file__).resolve().parent.parent
RCLONE = pathlib.Path('~/bin/rclone').expanduser()
УДАЛЁНКА = os.environ.get('ADDFUT_DRIVE_REMOTE', 'vol-down12-drive:vol-down12-backups/')
ЖУРНАЛ = ДОМ / 'status-stranitsa.log'

СОБЫТИЯ_ВПЕРЕДИ = [                      # короткий календарь; прошедшее скрывается само
    ('2026-08-31', 'замыкание августа (письмо утром 01.09)'),
    ('2026-09-02', 'месячный переход — сентябрьский сигнал вступает в силу'),
    ('2026-09-04', 'ПЕРЕВОД ПИЛОТА НА МАРШРУТ Е (исполнитель 16:51, письмо сразу после)'),
    ('2026-09-07', 'Labor Day — биржа закрыта, контур пропустит день'),
    ('2026-09-23', 'предел возраста замера маржи — до этого дня обслуживание first_connect'),
    ('2026-11-20', 'расчёт даты ноябрьского ролла'),
]


def безопасно(что, иначе='?'):
    try:
        return что()
    except Exception:
        return иначе


def собрать():
    сейчас = datetime.datetime.now()
    з = []
    з.append('ADD-FUT — СТРАНИЦА СТАТУСА (бумажный пилот)')
    з.append('=' * 56)
    з.append(f'Обновлено: {сейчас.strftime("%d.%m.%Y %H:%M")} (по времени машины, шаг 30 мин)')
    з.append('ПРАВИЛО СВЕЖЕСТИ: отметка старше 40 минут = машина или выгрузка лежат.')
    з.append('')

    def тик():
        в = time.time() - float((ДОМ / 'tick-heartbeat').read_text().strip())
        return f'{в / 60:.0f} мин назад' + ('' if в < 1500 else '  <-- ДОЛГО, ЭТО НЕ НОРМА')
    з.append(f'Пульс робота (тик): {безопасно(тик)}')

    тревоги = безопасно(lambda: [ф.name for ф in ДОМ.glob('ALARM*')
                                 if '.resolved' not in ф.name], None)
    if тревоги:
        з.append('ТРЕВОГИ: ' + '; '.join(тревоги) + '  <-- АВТОПИЛОТ ОСТАНОВЛЕН, см. почту')
    elif тревоги == []:
        з.append('Тревоги: нет')
    else:
        з.append('Тревоги: ?')
    з.append('')

    def книга():
        к = json.loads((ДОМ / 'book-F.json').read_text(encoding='utf-8'))['payload']
        б = к['book']
        маршрут = безопасно(lambda: (ДОМ / 'route.txt').read_text().strip(), '?')
        н_а = (f"{б['es_held']} ES + {б['n_e'] - 10 * б['es_held']} MES ({б['ser_a']})"
               if б.get('unit_is_mes') and б['n_e'] else
               ('выключена сигналом' if not б['n_e'] else f"{б['n_e']} ед. ({б['ser_a']})"))
        н_б = f"{б['n_b']} ZN ({б['ser_b']})" if б['n_b'] else 'выключена сигналом'
        return (маршрут, н_а, н_б, б.get('prev_close_lev', 0),
                к.get('session_no', '?'), б.get('last_session', '?'))
    м, н_а, н_б, плечо, сессия, посл = безопасно(книга, ('?',) * 6)
    з.append(f'Маршрут: {"Ф (фьючерсы)" if м == "F" else "Е (фонды)" if м == "E" else м}')
    з.append(f'Нога А (акции США):   {н_а}')
    з.append(f'Нога Б (трежерис):    {н_б}')
    з.append(f'Плечо на замыкании:   {плечо if isinstance(плечо, str) else f"{плечо:.4f}"} (потолок 2,00)')
    з.append('')

    def нлв():
        т = (ДОМ / 'autopilot.log').read_text(encoding='utf-8', errors='replace')
        м2 = re.findall(r'замкнуто: NLV закрытия ([\d,]+\.\d+)', т)
        return м2[-1] if м2 else '?'
    з.append(f'Счёт (NLV на последнее замыкание, сессия №{сессия} от {посл}): {безопасно(нлв)} $')
    з.append('Внутридневная переоценка не запрашивается сознательно: страница читает файлы,')
    з.append('а не дёргает брокера. Свежий счёт — в завтрашнем пульсе и после замыкания.')
    з.append('')

    def события_лога():
        т = (ДОМ / 'autopilot.log').read_text(encoding='utf-8', errors='replace').splitlines()
        важное = [с for с in т if re.search(
            r'торговля 20|замыкание 20|решение|ролл|перевод|ВНИМАНИЕ|ТРЕВОГА', с)]
        return важное[-6:]
    з.append('Последние записи робота:')
    for с in безопасно(события_лога, ['  ?']):
        з.append('  ' + с[:110])
    з.append('')

    з.append('Ближайшее по плану:')
    сегодня = сейчас.strftime('%Y-%m-%d')
    for дата, что in СОБЫТИЯ_ВПЕРЕДИ:
        if дата >= сегодня:
            з.append(f'  {дата[8:10]}.{дата[5:7]} — {что}')
    з.append('')
    з.append('Каналы: письма (папка ADD-FUT: пульс ежедневно, сводка по понедельникам,')
    з.append('события, тревоги) + эта страница каждые полчаса. Тишина внутри дня — норма.')
    return '\n'.join(з) + '\n'


def главная():
    текст = собрать()
    файл = ПРОЕКТ / 'docs' / 'ADD-FUT-STATUS.txt'
    файл.write_text(текст, encoding='utf-8')
    метка = datetime.datetime.now().strftime('%F %T')
    if not RCLONE.exists():
        with ЖУРНАЛ.open('a', encoding='utf-8') as ф:
            ф.write(f'{метка} нет rclone — страница не выгружена\n')
        return 2
    ход = subprocess.run([str(RCLONE), 'copyto', str(файл),
                          УДАЛЁНКА + 'ADD-FUT-STATUS.txt'],
                         capture_output=True, text=True, timeout=120)
    with ЖУРНАЛ.open('a', encoding='utf-8') as ф:
        ф.write(f'{метка} выгрузка: код {ход.returncode}'
                + (f' ({ход.stderr.strip()[:120]})' if ход.returncode else '') + '\n')
    return ход.returncode


if __name__ == '__main__':
    raise SystemExit(главная())
