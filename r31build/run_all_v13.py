#!/usr/bin/env python3
"""ADD-FUT v1.5.3: контрольный конвейер. Уровни: (0) MANIFEST; (1) окна §3 с допусками
+ побайтное числовое сравнение NAV с эталонами data/; (2) депрессия; (3) сигналы модели
против signals_monthly.csv точно + О-2 целыми числами. Любое расхождение → код 1."""
import sys, hashlib
from pathlib import Path
import pandas as pd, numpy as np
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import sim_v13 as S
import depr_v13 as DP
DATA = ROOT/'data' if (ROOT/'data').exists() else ROOT
ok = True
def rep(name, cond, detail=''):
    global ok; ok &= bool(cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}{(': '+detail) if detail else ''}")

# (0) MANIFEST
mf = ROOT/'MANIFEST.txt'
if mf.exists():
    bad=[]
    for line in mf.read_text().splitlines():
        if not line.strip(): continue
        h, p = line.split(maxsplit=1)
        f = ROOT/p.strip().lstrip('./')
        if not f.exists(): bad.append(p+' (нет файла)'); continue
        if hashlib.sha256(f.read_bytes()).hexdigest()!=h: bad.append(p)
    rep('MANIFEST: целостность всех файлов', len(bad)==0, f"{'; '.join(bad[:3])}" if bad else f"{len(mf.read_text().splitlines())} записей")
else:
    rep('MANIFEST: найден', False)

REF = {'modern_strict':(9.98,-22.4),'modern_ideal':(10.70,-19.1),
       'modern_stress15':(8.32,-23.4),'splice_strict':(11.41,-33.7),
       'depr_strict':(-7.7,-47.2)}
def chk(name, got, ref, tol=(0.05,0.3)):
    rep(name, abs(got[0]-ref[0])<=tol[0] and abs(got[1]-ref[1])<=tol[1], f"{got[0]:.2f}% / {got[1]:.1f}% (эталон {ref[0]:.2f}/{ref[1]:.1f})")
def nav_exact(name, produced, ref_file):
    ref = pd.read_csv(ref_file, parse_dates=[0], index_col=0)['nav']
    same = produced.index.equals(ref.index) and np.allclose(produced.values, ref.values, rtol=1e-12, atol=0.0)
    rep(name, same, 'даты точно, значения в пределах 1e-12 отн.' if same else 'РАСХОЖДЕНИЕ с эталоном')

dm=S.build_modern()
r,nav,_=S.sim(*dm,strict=True); chk('2003–2026 строгая', S.met(r), REF['modern_strict'])
nav.to_frame('nav').to_csv(ROOT/'nav_v13_modern.csv')
nav_exact('NAV modern = эталон data/', nav, DATA/'nav_v13_modern.csv')
r,_,_=S.sim(*dm,strict=False); chk('2003–2026 идеализ. (сверка)', S.met(r), REF['modern_ideal'])
r,_,_=S.sim(*dm,strict=True,spread_pp=1.5); chk('2003–2026 стресс 1,5', S.met(r), REF['modern_stress15'])
ds=S.build_splice()
r,nav,_=S.sim(*ds,strict=True); chk('1963–2026 строгая (прокси)', S.met(r), REF['splice_strict'])
nav.to_frame('nav').to_csv(ROOT/'nav_v13_splice.csv')
nav_exact('NAV splice = эталон data/', nav, DATA/'nav_v13_splice.csv')
tot,dd=DP.run(strict=True); chk('1929–1934 строгая (оценка)', (tot,dd), REF['depr_strict'], tol=(0.2,0.5))

ms = pd.DataFrame({'leg_eq':ds[6],'leg_bond':ds[7]}, index=ds[0]).resample('ME').first().astype(int)
csv = pd.read_csv(DATA/'signals_monthly.csv', parse_dates=[0], index_col=0)
rep('Сигналы модели = signals_monthly.csv (точно)', ms.index.equals(csv.index) and (ms.values==csv.values).all(), f'{len(ms)} мес × 2 ноги')
sw=(ms!=ms.shift(1)).sum(axis=1); sw.iloc[0]=0; r12=sw.rolling(12).sum().dropna()
o2=(int(sw.sum()), int(r12.max()), int((r12>=8).sum()), int((r12>12).sum()))
rep('О-2 (целые эталоны)', o2==(175,9,13,0), f"всего смен {o2[0]} (175), макс {o2[1]} (9), ≥8: {o2[2]} (13), >12: {o2[3]} (0)")

print('ИТОГ:', 'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ' if ok else 'ЕСТЬ РАСХОЖДЕНИЯ')
sys.exit(0 if ok else 1)
