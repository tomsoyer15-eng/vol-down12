"""Проверка архива ГЛАЗАМИ ВНЕШНЕГО АУДИТОРА.

Корень доверия предписывает: «распаковать в пустой каталог и выполнить
python selfcheck_v192.py». Здесь делается ровно это, но с ЧУЖИМ домашним каталогом и
урезанным окружением — то есть в условиях, в которых находится любой, кроме этой машины.
До правки находки №13 такой прогон давал ПРОВАЛ, потому что проверка лезла по жёсткому
пути ~/claude-projects/vol-down12 за живым замером маржи.

Написано на Python, а не на bash, СОЗНАТЕЛЬНО: кириллические имена переменных bash считает
командами, и за двое суток этот класс поймал меня СЕМЬ раз, включая предыдущую редакцию
этого самого зонда — она измерила пустоту и напечатала правдоподобный результат.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

КОРЕНЬ = Path('/home/alex/claude-projects/vol-down12')
ПИТОН = str(КОРЕНЬ / '.venv/bin/python')

каталог = tempfile.mkdtemp(prefix='auditor-')
дом = tempfile.mkdtemp(prefix='auditor-home-')
zipfile.ZipFile(КОРЕНЬ / 'paket-ADD-FUT-v1_6_0-r33.zip').extractall(каталог)
# проверяем ИСПРАВЛЕННЫЙ проверяющий, а не тот, что лежит в старом архиве
shutil.copy2(КОРЕНЬ / 'r33build/selfcheck_v192.py', Path(каталог) / 'selfcheck_v192.py')
print(f'распаковано в {каталог}')
print(f'дом аудитора {дом}: файлов {len(os.listdir(дом))}')
print(f'рабочее дерево видно аудитору: '
      f'{os.path.isdir(os.path.join(дом, "claude-projects", "vol-down12"))}')

среда = {'HOME': дом, 'PATH': '/usr/bin:/bin:/usr/local/bin', 'LANG': 'C.UTF-8'}
r = subprocess.run([ПИТОН, 'selfcheck_v192.py'], cwd=каталог, env=среда,
                   capture_output=True, text=True, timeout=7200)
Path('/tmp/auditor.log').write_text(r.stdout + '\n--- stderr ---\n' + r.stderr,
                                    encoding='utf-8')
print(f'код возврата: {r.returncode}')
итог = [s for s in r.stdout.splitlines() if s.startswith('ИТОГ')]
print('итог:', итог[-1] if итог else '(строки ИТОГ нет)')
про_пару = [s for s in r.stdout.splitlines()
            if 'машинная пара' in s or 'НЕПРИМЕНИМО ВНЕ РАБОЧЕГО' in s]
print('про машинную пару:', про_пару or '(ничего)')
print('провалов [FAIL]:', sum(1 for s in r.stdout.splitlines() if '[FAIL]' in s))
