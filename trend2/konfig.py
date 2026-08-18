# -*- coding: utf-8 -*-
"""Пути и постоянные независимого симулятора ADD-WIDE/ADD-FUT.

Никакого кода прежней реализации здесь не использовано: всё выведено из
словесного описания задания и раздела 21 протокола.
"""
from pathlib import Path

# --- источники данных -------------------------------------------------------
KORENJ = Path(__file__).resolve().parent
REPO = KORENJ.parent
VYGRUZKI = Path('/home/user/norgate')          # распакованные norgate_export_2/3/4
PAPKI_VYGRUZOK = ['exp2', 'exp3', 'exp4']
TABLICA_ROLLOV = Path('/root/.claude/uploads/9105309a-064d-505a-ae0f-d18949dd988f/'
                      '746c7056-roll_table_64_markets.csv')
PKL_ES_ZN = REPO / 'data' / 'norgate' / 'full_history.pkl'   # непрерывные и поконтрактные ES/ZN/GC
DTB3 = REPO / 'r33build' / 'data' / 'dtb3.csv'               # трёхмесячный вексель США, FRED
FX_EUR = REPO / 'data' / 'fx_eurusd_daily.csv'               # долларов за евро (до 1999 — синтетика)

KESH = KORENJ / 'kesh'
VYVOD = KORENJ / 'vyvod'

# --- счёт -------------------------------------------------------------------
SCHET = 10_000_000.0        # начальный капитал, USD
DOLYA_ADDFUT = 0.5          # часть 1 (ES + ZN)
DOLYA_WIDE = 0.5            # часть 2 (63 рынка)

# часть 1
PLECHO_ADDFUT = 2.00        # к капиталу части
DOLI_NOG = {'ES': 0.5, 'ZN': 0.5}

# часть 2
CEL_KOLEBANIJ = 0.13        # годовые колебания части 2
OKNO_VOL = 250              # дней в оценке колебаний
MESYACEV_SMA = 12           # длина средней сигнала

# --- валютные фьючерсы как источник курса ----------------------------------
# курс = цена * множитель / истинный размер контракта в валюте
FX_FJUCHERSY = {
    'AUD': ('6A', 100_000),
    'CAD': ('6C', 100_000),
    'GBP': ('6B', 62_500),
    'JPY': ('6J', 12_500_000),   # Norgate котирует 6J в 100 раз крупнее — здесь это снимается
    'CHF': ('6S', 125_000),
    'EUR': ('6E', 125_000),
    'MXN': ('6M', 500_000),
    'NZD': ('6N', 100_000),
}
