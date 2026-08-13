#!/usr/bin/env python3
"""WORM-якорь вне машины (решение заказчика 13.08.2026, после 17-го круга).

Цепочка хэшей журнала §7 доказывает нетронутость только против правки БЕЗ пересчёта
хэшей: root на машине может переписать журнал вместе с цепочкой. Якорь выносит корень
доверия НАРУЖУ: после каждого замыкания в репозиторий пишется маленький датированный
файл с корнем цепочки §7, digest книги и хэшами реестра/замера, и уходит push-ем на
GitHub и в зеркало. Подмена прошлого потребует force-push с расхождением двух удалённых
историй — это обнаружимо, а не невидимо. Drive-копии якорей кладутся в сессиях разбора.

Якорь ДОПИСЫВАЕТСЯ (новый файл на каждый день), существующие файлы не переписываются —
каталог anchors/ ведёт себя как журнал, отсюда и имя WORM (write once, read many).
"""
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
ANCHORS = ROOT / 'anchors'


def _sha(p):
    try:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    except OSError:
        return 'ФАЙЛА НЕТ'


def main(day):
    sys.path.insert(0, str(HERE))
    import journal as J
    st = Path.home() / '.addfut'
    rt = st / 'route.txt'
    route = rt.read_text().strip() if rt.exists() else 'F'
    jp = st / f'journal-{route}.csv'
    n = J.verify(jp)                       # битая цепочка = якорь не пишется, тревога выше
    rows = J.read(jp)
    tail = rows[-1]['row_hash'] if rows else 'пусто'
    book = st / f'book-{route}.json'
    digest = json.loads(book.read_text(encoding='utf-8')).get('digest', 'нет')
    ANCHORS.mkdir(exist_ok=True)
    out = ANCHORS / f'worm-{day}.txt'
    if out.exists():
        # ПОВТОР ДНЯ НЕ ПЕРЕЗАПИСЫВАЕТ (write once): второе замыкание одной даты — след
        # отдельным файлом, а не тихая правка первого.
        k = 2
        while (ANCHORS / f'worm-{day}-{k}.txt').exists():
            k += 1
        out = ANCHORS / f'worm-{day}-{k}.txt'
    body = (f'WORM-якорь ADD-FUT v1.6.0 ред. 33 — {day} (маршрут {route})\n'
            f'журнал §7: строк {n}, корень цепочки {tail}\n'
            f'digest книги {book.name}: {digest}\n'
            f'sha256 журнала-файла: {_sha(jp)}\n'
            f'sha256 реестра: {_sha(HERE / "instruments_live.csv")}\n'
            f'sha256 замера маржи: {_sha(HERE / "margins_live.json")}\n'
            f'sha256 живого ряда сигналов: {_sha(st / "signals_live.csv")}\n')
    out.write_text(body, encoding='utf-8')
    print(f'якорь: {out.name}; корень §7 {tail[:16]}…')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else 'без-даты'))
