#!/usr/bin/env python3
"""Извлечение текста из PDF пакета без внешних библиотек.

Понимает оба способа вложения шрифта, встречающиеся в документах проекта:
простые шрифты с однобайтовыми кодами (прежние редакции) и составные Type0/Identity-H
с двухбайтовыми (ред. 33 и далее — только так кириллица остаётся извлекаемой).
Соответствие кода символу берётся из ToUnicode CMap самого файла, а не угадывается.
"""
import re, zlib, sys, base64

data = open(sys.argv[1], 'rb').read()
objs = {int(m.group(1)): m.group(2)
        for m in re.finditer(rb'(\d+) 0 obj\r?\n(.*?)\r?\nendobj', data, re.S)}


def stream_of(n):
    b = objs[n]; i = b.find(b'stream')
    if i < 0: return None
    s = b[i + 6:]
    s = s[2:] if s.startswith(b'\r\n') else (s[1:] if s.startswith(b'\n') else s)
    s = s[:s.rfind(b'endstream')]
    hdr = b[:i]
    if b'ASCII85Decode' in hdr:
        t = s.strip()
        if t.endswith(b'~>'): t = t[:-2]
        s = base64.a85decode(t)
    if b'FlateDecode' in hdr:
        s = zlib.decompress(s)
    return s


def parse_cmap(n):
    s = stream_of(n).decode('latin-1'); mp = {}
    for blk in re.findall(r'beginbfchar(.*?)endbfchar', s, re.S):
        for a, b in re.findall(r'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', blk):
            mp[int(a, 16)] = ''.join(chr(int(b[i:i + 4], 16)) for i in range(0, len(b), 4))
    # bfrange существует в двух формах — <lo> <hi> <dst> и <lo> <hi> [<u1> <u2> ...].
    # Записи разбираются ПООЧЕРЁДНО одним чередующимся шаблоном: раздельные проходы
    # сшивают тройки из соседних записей и молча портят карту.
    ENTRY = r'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(?:\[(.*?)\]|<([0-9A-Fa-f]+)>)'
    for blk in re.findall(r'beginbfrange(.*?)endbfrange', s, re.S):
        for a, b, arr, dst in re.findall(ENTRY, blk, re.S):
            lo, hi = int(a, 16), int(b, 16)
            if arr:
                for k, u in zip(range(lo, hi + 1), re.findall(r'<([0-9A-Fa-f]+)>', arr)):
                    mp[k] = ''.join(chr(int(u[i:i + 4], 16)) for i in range(0, len(u), 4))
            else:
                for k in range(lo, hi + 1):
                    mp[k] = chr(int(dst, 16) + k - lo)
    return mp


# объект шрифта -> (карта кодов, ширина кода в байтах)
FONTS = {}
for n, b in objs.items():
    if b'/Type /Font' in b and b'/ToUnicode' in b:
        tu = int(re.search(rb'/ToUnicode (\d+) 0 R', b).group(1))
        wide = b'/Type0' in b or b'Identity-H' in b
        FONTS[n] = (parse_cmap(tu), 2 if wide else 1)
        nm = re.search(rb'/Name /(\S+)', b)              # прежние редакции: имя в шрифте
        if nm: FONTS[nm.group(1).decode()] = FONTS[n]


def page_fonts(body):
    """Имя ресурса (/F1) -> объект шрифта, из словаря /Resources страницы."""
    out = {}
    res = re.search(rb'/Resources\s+(\d+) 0 R', body)
    src = objs[int(res.group(1))] if res else body
    ref = re.search(rb'/Font\s+(\d+) 0 R', src)          # словарь шрифтов ссылкой
    fd = objs[int(ref.group(1))] if ref else None
    if fd is None:
        inl = re.search(rb'/Font\s*<<(.*?)>>', src, re.S)  # или встроенный
        fd = inl.group(1) if inl else b''
    for nm, on in re.findall(rb'/(\S+)\s+(\d+) 0 R', fd):
        if int(on) in FONTS: out[nm.decode()] = FONTS[int(on)]
    return out


def unesc(s):
    out = bytearray(); i = 0
    while i < len(s):
        c = s[i]
        if c == 0x5c:
            n = s[i + 1]; i += 2
            d = {0x6e: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12, 0x28: 40, 0x29: 41, 0x5c: 92}
            if n in d: out.append(d[n])
            elif 0x30 <= n <= 0x37:
                o = chr(n)
                while len(o) < 3 and i < len(s) and 0x30 <= s[i] <= 0x37:
                    o += chr(s[i]); i += 1
                out.append(int(o, 8) & 0xFF)
            else: out.append(n)
        else:
            out.append(c); i += 1
    return bytes(out)


def decode(raw, font):
    if not font:
        return raw.decode('cp1252', 'replace')
    mp, w = font
    if w == 2:
        codes = [int.from_bytes(raw[i:i + 2], 'big') for i in range(0, len(raw) - 1, 2)]
    else:
        codes = list(raw)
    return ''.join(mp.get(c, '') for c in codes)


pages = [(int(re.search(rb'/Contents (\d+) 0 R', b).group(1)), b)
         for n, b in sorted(objs.items())
         if b'/Type /Page' in b and b'/Contents' in b and b'/Type /Pages' not in b]

# Позиционирование ловится вместе с операндами: строка рвётся только при сдвиге ПО
# ВЕРТИКАЛИ. Иначе каждое переключение шрифта (латиница/кириллица идут разными
# подмножествами) даёт разрыв, и извлечённый текст рассыпается по словам.
TOK = (rb'/(\S+)\s+[\d.]+\s+Tf|\((?:[^()\\]|\\.)*\)|<([0-9A-Fa-f\s]+)>'
       rb'|(-?[\d.]+)\s+(-?[\d.]+)\s+T[dD]|T\*|ET')

for pg, (cn, body) in enumerate(pages, 1):
    cs = stream_of(cn); pf = page_fonts(body)
    print(f"\n===== PAGE {pg} =====")
    font = None; lines = []; cur = ''
    for tok in re.finditer(TOK, cs, re.S):
        t = tok.group(0)
        if t.startswith(b'/'):
            font = pf.get(tok.group(1).decode()) or FONTS.get(tok.group(1).decode())
        elif t.startswith(b'('):
            cur += decode(unesc(t[1:t.rfind(b')')]), font)
        elif t.startswith(b'<'):
            h = re.sub(rb'\s', b'', tok.group(2))
            cur += decode(bytes.fromhex(h.decode()), font)
        elif tok.group(4) is not None:                   # x y Td/TD
            if float(tok.group(4)) != 0.0 and cur:
                lines.append(cur); cur = ''
        else:                                            # T* / ET — всегда новая строка
            if cur: lines.append(cur); cur = ''
    if cur: lines.append(cur)
    print('\n'.join(lines))
