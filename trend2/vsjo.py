# -*- coding: utf-8 -*-
"""Полный прогон: данные -> панели -> симуляция -> самопроверки -> проверки движка."""
import subprocess, sys, pathlib
import konfig as K

for shag in ('zagruzka.py', 'podgotovka.py'):
    print(f'--- {shag}');  subprocess.run([sys.executable, shag], check=True)
subprocess.run([sys.executable, 'zapusk.py'], check=True)
r = subprocess.run([sys.executable, 'proverki.py'], check=True, capture_output=True, text=True)
print(r.stdout)
p = K.VYVOD / 'otchet.md'
p.write_text(p.read_text() + '\n\n## Проверки самого движка\n\n```\n' + r.stdout + '```\n')
print('файлы:', sorted(x.name for x in K.VYVOD.iterdir()))
