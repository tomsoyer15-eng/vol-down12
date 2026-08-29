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
import imaplib
import pathlib
import smtplib
import socket
import sys
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

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


def _в_папку(адрес, пароль, msgid, ярлык):
    """Положить только что отправленное письмо в папку заказчика и поставить звёздочку.

    ЗАЧЕМ ТАК (29.08.2026): фильтр Gmail снаружи создать нельзя — Google даёт это только
    человеку в браузере, — а заказчик просил, чтобы письма сами ложились в папку. Пароль
    приложения открывает и IMAP, поэтому сортируем со стороны отправителя: находим своё
    письмо по Message-ID и вешаем ярлык (X-GM-LABELS — гугловское расширение IMAP; ярлык
    Gmail и есть папка) плюс звёздочку (флаг Flagged).
    ОТКАЗ ПОМЕТКИ НЕ ОТКАЗ ПИСЬМА: письмо уже ушло; здесь любой сбой — предупреждение,
    а не ошибка, иначе косметика глушила бы тревогу.
    """
    м = imaplib.IMAP4_SSL('imap.gmail.com', 993, timeout=60)
    try:
        м.login(адрес, пароль)
        _код, _папки = м.list()
        _все = 'INBOX'
        for _п in _папки:
            _с = _п.decode(errors='replace')
            if '\\All' in _с:
                _все = _с.split(' "/" ')[-1].strip('"')
        м.select(f'"{_все}"', readonly=False)
        _uid = None
        for _ in range(10):                        # письмо доезжает до ящика не мгновенно
            _код, _д = м.uid('SEARCH', 'HEADER', 'Message-ID', msgid)
            if _д and _д[0]:
                _uid = _д[0].split()[-1].decode()
                break
            time.sleep(3)
        if not _uid:
            print(f'папка: письмо ещё не видно в ящике — ярлык не поставлен', file=sys.stderr)
            return False
        м.uid('STORE', _uid, '+X-GM-LABELS', f'("{ярлык}")')
        м.uid('STORE', _uid, '+FLAGS', r'(\Flagged)')
        return True
    finally:
        try:
            м.logout()
        except Exception:
            pass


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
    msgid = make_msgid()
    п['Message-ID'] = msgid
    with smtplib.SMTP('smtp.gmail.com', 587, timeout=60) as с:
        с.starttls()
        с.login(адрес, пароль)
        с.send_message(п)
    print(f'письмо отправлено: {тема}')
    ярлык = _ярлык_из_конфа()
    try:
        if _в_папку(адрес, пароль, msgid, ярлык):
            print(f'положено в папку «{ярлык}», звёздочка стоит')
    except Exception as _ошибка:
        print(f'папка: пометка не удалась ({_ошибка}) — письмо при этом ушло', file=sys.stderr)


def _ярлык_из_конфа():
    for стр in КОНФ.read_text(encoding='utf-8').splitlines():
        if стр.strip().startswith('YARLIK='):
            return стр.split('=', 1)[1].strip()
    return 'ADD-FUT'


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
