#!/usr/bin/env python3
"""Сборка PDF нормативного текста спецификации из его текстового исходника.

Исходник разбит маркерами «===== PAGE N =====»; каждая такая часть становится страницей,
при переполнении — доливается следующей. Шрифт DejaVu Sans Mono: моноширинный и содержит
кириллицу, поэтому переносы предсказуемы и текст не подменяется квадратами.

Назначение — сделать документ записи бинарным файлом с устойчивым хэшем, как в прежних
редакциях пакета. Содержание не меняется: PDF есть представление текста, а не его редакция.
"""
import sys, textwrap
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
# Type 42 (TrueType) вместо умолчательного Type 3: только так текст в PDF остаётся
# извлекаемым, а документ записи — читаемым машиной, а не картинкой.
matplotlib.rcParams['pdf.fonttype'] = 42
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

FONT = 'DejaVu Sans Mono'
COLS, ROWS = 104, 62          # знаков в строке и строк на странице A4
FS = 6.6


def layout(src):
    """Текст -> список страниц (списков строк)."""
    blocks, cur = [], []
    for line in src.splitlines():
        if line.startswith('===== PAGE'):
            if cur: blocks.append(cur)
            cur = []
            continue
        cur.append(line)
    if cur: blocks.append(cur)

    lines = []
    for b in blocks:
        for raw in b:
            if not raw.strip():
                lines.append('')
            else:
                lines.extend(textwrap.wrap(raw, COLS, subsequent_indent='   ',
                                           break_long_words=False, break_on_hyphens=False)
                             or [''])
        lines.append('\x00')                     # мягкая граница исходной страницы
    pages, cur = [], []
    for ln in lines:
        if ln == '\x00':
            if len(cur) > ROWS * 0.55:           # граница уважается, если страница набрана
                pages.append(cur); cur = []
            continue
        if len(cur) >= ROWS:
            pages.append(cur); cur = []
        cur.append(ln)
    if cur: pages.append(cur)
    return pages


def build(src_path, out_path, title):
    pages = layout(Path(src_path).read_text(encoding='utf-8'))
    with PdfPages(out_path) as pdf:
        for k, page in enumerate(pages, 1):
            fig = plt.figure(figsize=(8.27, 11.69))
            fig.text(0.055, 0.975, title, fontfamily=FONT, fontsize=FS - 0.6, color='#555555')
            fig.text(0.945, 0.975, f'стр. {k} из {len(pages)}', fontfamily=FONT,
                     fontsize=FS - 0.6, color='#555555', ha='right')
            fig.text(0.055, 0.955, '\n'.join(page), fontfamily=FONT, fontsize=FS,
                     va='top', ha='left', linespacing=1.42)
            pdf.savefig(fig); plt.close(fig)
    return len(pages)


if __name__ == '__main__':
    src, out, title = sys.argv[1], sys.argv[2], sys.argv[3]
    n = build(src, out, title)
    print(f'{out}: страниц {n}')
