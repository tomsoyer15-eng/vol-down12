#!/usr/bin/env python3
"""Выпуск пакета: манифест -> архив -> РАСПАКОВКА В ЧИСТЫЙ КАТАЛОГ -> selfcheck.

Порядок замкнут намеренно. Прежде архив собирался из списка и на самом архиве не
запускался — и оказалось, что он не запускался вовсе: ни замороженного ядра sim_v13.py, ни
исходных рядов data/*.csv внутри не было. Строка «ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ» имеет смысл только
если получена на РАСПАКОВАННОМ архиве, где недостающий файл неоткуда подхватить.
"""
import hashlib, re, shutil, subprocess, sys, tempfile, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
R, PY = ROOT / 'r33build', ROOT / '.venv/bin/python'
Z = ROOT / 'paket-ADD-FUT-v1_6_0-r33.zip'
MAN = R / 'MANIFEST-192.txt'


def refresh_manifest():
    out, miss = [], []
    for ln in MAN.read_text(encoding='utf-8').splitlines():
        m = re.match(r'^([0-9a-f]{64})\s+(\S+)$', ln.strip())
        if not m:
            out.append(ln); continue
        f = R / m.group(2)
        if not f.exists():
            miss.append(m.group(2)); out.append(ln); continue
        out.append(f'{hashlib.sha256(f.read_bytes()).hexdigest()}  {m.group(2)}')
    if miss:
        sys.exit(f'НЕТ ФАЙЛОВ ИЗ МАНИФЕСТА: {miss}')
    MAN.write_text('\n'.join(out) + '\n', encoding='utf-8')
    return [l.split(None, 1)[1] for l in out if re.match(r'^[0-9a-f]{64}\s', l)]


def main():
    names = refresh_manifest()
    with zipfile.ZipFile(Z, 'w', zipfile.ZIP_DEFLATED) as f:
        for n in names + [MAN.name]:
            f.write(R / n, n)
    h = hashlib.sha256(Z.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory() as t:
        zipfile.ZipFile(Z).extractall(t)
        # НЕЗАВИСИМАЯ СВЕРКА ХЭШЕЙ ДО ЗАПУСКА ПРОВЕРЯЮЩЕГО (двенадцатый круг, №8):
        # selfcheck сам исключает себя из уровня 0б, и изменённый проверяющий код мог бы
        # заверить сам себя. Здесь release, а не selfcheck, сверяет КАЖДЫЙ файл архива —
        # включая selfcheck_v192.py — с манифестом, и только затем исполняет его.
        man = Path(t) / 'MANIFEST-192.txt'
        bad = []
        for ln in man.read_text(encoding='utf-8').splitlines():
            mm = re.match(r'^([0-9a-f]{64})\s+(\S+)$', ln.strip())
            if not mm:
                continue
            fp = Path(t) / mm.group(2)
            if not fp.exists():
                bad.append(f'{mm.group(2)}: отсутствует'); continue
            if hashlib.sha256(fp.read_bytes()).hexdigest() != mm.group(1):
                bad.append(f'{mm.group(2)}: хэш не совпал')
        if bad:
            sys.exit('ВЫПУСК НЕ СОСТОЯЛСЯ: архив расходится с манифестом ДО запуска '
                     'проверяющего: ' + '; '.join(bad[:6]))
        r = subprocess.run([str(PY), 'selfcheck_v192.py'], cwd=t, capture_output=True, text=True)
        tail = (r.stdout or r.stderr).strip().splitlines()[-1:]
    ok = r.returncode == 0 and 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ' in (r.stdout or '')
    print(f'{Z.name}: {len(names) + 1} файлов, {Z.stat().st_size / 1048576:.2f} МБ')
    print(f'SHA-256: {h}')
    print(f'распакованный архив: {tail[0] if tail else "нет вывода"}')
    if not ok:
        print((r.stdout or '')[-3000:]); sys.exit('ВЫПУСК НЕ СОСТОЯЛСЯ: архив не проходит selfcheck')
    # ИСТОРИЯ ВЫПУСКОВ: архив прежде перезаписывался на месте, и предыдущий корень доверия
    # исчезал физически. Каждый выпуск теперь остаётся датированной копией.
    import datetime
    rel = ROOT / 'releases'
    rel.mkdir(exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
    keep = rel / f'paket-r33-{stamp}-{h[:8]}.zip'
    shutil.copy2(Z, keep)
    with open(rel / 'INDEX.txt', 'a', encoding='utf-8') as f:
        f.write(f'{stamp}  {h}  {keep.name}\n')
    (ROOT / 'docs/r33-sha256.txt').write_text(
        f'Корень доверия ADD-FUT v1.6.0 ред. 33\n\nАрхив {Z.name}\nSHA-256  {h}\n\n'
        f'Внутри — MANIFEST-192.txt на {len(names)} файлов: код, нормативный текст и ИСХОДНЫЕ РЯДЫ.\n'
        f'Проверка: распаковать в пустой каталог и выполнить  python selfcheck_v192.py\n'
        f'Хэш получен ПОСЛЕ такой проверки, а не до неё.\n', encoding='utf-8')


if __name__ == '__main__':
    main()
