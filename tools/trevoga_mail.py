#!/usr/bin/env python3
"""Письмо заказчику о нештатном событии пилота. Стандартная библиотека, без зависимостей.

НАСТРОЙКА — ~/.addfut/mail.conf (права 600, В РЕПОЗИТОРИЙ НЕ ПОПАДАЕТ, правило 6):
    ADRES=tomsoyer15@gmail.com
    PAROL=шестнадцатизначный пароль приложения Google
Пароль приложения создаётся на https://myaccount.google.com/apppasswords (нужна включённая
двухэтапная проверка). Обычный пароль от почты НЕ подходит и в файл не пишется.

ВЫЗОВ:  python3 trevoga_mail.py "тема" [файл-с-текстом]   (без файла — текст из stdin)
        python3 trevoga_mail.py --test                     (проверочное письмо)

ПОВЕДЕНИЕ ПРИ ОТСУТСТВИИ НАСТРОЙКИ — громкий отказ кодом 2, а не молчание: вызывающий
обязан увидеть в журнале, что тревога НЕ ушла. Само наблюдение от почты не зависит.
"""
import pathlib
import smtplib
import socket
import sys
from email.message import EmailMessage
from email.utils import formatdate

КОНФ = pathlib.Path('~/.addfut/mail.conf').expanduser()


def настройка():
    if not КОНФ.exists():
        print(f'почта не настроена: нет {КОНФ} — письмо НЕ отправлено', file=sys.stderr)
        raise SystemExit(2)
    из = {}
    for стр in КОНФ.read_text(encoding='utf-8').splitlines():
        стр = стр.strip()
        if стр and not стр.startswith('#') and '=' in стр:
            к, з = стр.split('=', 1)
            из[к.strip()] = з.strip()
    if not из.get('ADRES') or not из.get('PAROL'):
        print(f'почта не настроена: в {КОНФ} нужны ADRES= и PAROL=', file=sys.stderr)
        raise SystemExit(2)
    # Google показывает пароль приложения группами по четыре с пробелами — пробелы не
    # значимы, снимаем сами: скопированный «как есть» пароль не должен ломать вход.
    return из['ADRES'], из['PAROL'].replace(' ', '')


def отправить(тема, текст):
    адрес, пароль = настройка()
    п = EmailMessage()
    п['From'] = адрес
    п['To'] = адрес
    п['Date'] = formatdate(localtime=True)
    п['Subject'] = f'[ADD-FUT пилот] {тема}'
    # ПОМЕТКА «ВАЖНОЕ» (просьба заказчика 29.08.2026): каждое письмо этого канала — событие,
    # а не рассылка. Заголовки важности ставятся всегда; Gmail дополнительно сортирует их
    # фильтром заказчика по теме «[ADD-FUT пилот]».
    п['Importance'] = 'High'
    п['X-Priority'] = '1 (Highest)'
    п['Priority'] = 'urgent'
    машина = socket.gethostname()
    п.set_content(f'{текст}\n\n-- \nмашина {машина}, наблюдатель vol-down12')
    with smtplib.SMTP('smtp.gmail.com', 587, timeout=60) as с:
        с.starttls()
        с.login(адрес, пароль)
        с.send_message(п)
    print(f'письмо отправлено: {тема}')


if __name__ == '__main__':
    if len(sys.argv) >= 2 and sys.argv[1] == '--test':
        отправить('проверка связи', 'Тестовое письмо механизма тревог. Если вы его читаете '
                                    '— почтовый канал работает.')
    elif len(sys.argv) >= 2:
        тело = (pathlib.Path(sys.argv[2]).read_text(encoding='utf-8')
                if len(sys.argv) >= 3 else sys.stdin.read())
        отправить(sys.argv[1], тело)
    else:
        print(__doc__)
        raise SystemExit(1)
