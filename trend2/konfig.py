# -*- coding: utf-8 -*-
"""Пути и постоянные независимого симулятора.

Все спорные места задания разрешены ответами заказчика (см. VOPROSY.md и
OTVETY.md). Где ответ разошёлся с моим умолчанием, здесь стоит вариант
заказчика; методически лучший вариант вынесен во второй прогон через NASTROJKI.
"""
from pathlib import Path

# --- источники данных -------------------------------------------------------
KORENJ = Path(__file__).resolve().parent
REPO = KORENJ.parent
VYGRUZKI = Path('/home/user/norgate')
PAPKI_VYGRUZOK = ['exp1', 'exp2', 'exp3', 'exp4']      # exp1 — выгрузка №1, если прислана
TABLICA_ROLLOV = Path('/root/.claude/uploads/9105309a-064d-505a-ae0f-d18949dd988f/'
                      '746c7056-roll_table_64_markets.csv')
PKL_ES_ZN = REPO / 'data' / 'norgate' / 'full_history.pkl'
DTB3 = REPO / 'r33build' / 'data' / 'dtb3.csv'
KESH = KORENJ / 'kesh'
VYVOD = KORENJ / 'vyvod'

# --- счёт -------------------------------------------------------------------
SCHET = 10_000_000.0
DOLYA_ADDFUT = 0.5
DOLYA_WIDE = 0.5
PLECHO_ADDFUT = 2.00
NOGI_ADDFUT = ('ES', 'ZN')
CEL_KOLEBANIJ = 0.13
OKNO_VOL = 252              # ответ 5.3
MIN_DNEJ_VOL = 100          # ответ 1.2
MESYACEV_SMA = 12

# ответ 1.1: широкая нога — таблица роллов минус ES и минус ZN, 62 рынка
SPISOK_WIDE = """6A 6B 6C 6E 6J 6M 6N 6S BRN CC CGB CL CT DX FCE FDAX FESX FGBL FGBM FGBS
GAS GC GF HE HG HO HSI KC KE KOS LCC LE LEU LFT LLG LRC NG PA PL RB SB SCN SI SNK SO3 SR3
SSG TN UB WBS YAP YXT YYT ZB ZC ZF ZL ZM ZQ ZS ZT ZW""".split()

# --- валюты (ответ 2.2: постоянный курс — последняя цена фьючерса CME) --------
FX_FJUCHERSY = {
    'AUD': ('6A', 100_000), 'CAD': ('6C', 100_000), 'GBP': ('6B', 62_500),
    'JPY': ('6J', 12_500_000),          # у Norgate множитель занижен в 100 раз
    'CHF': ('6S', 125_000), 'EUR': ('6E', 125_000),
    'MXN': ('6M', 500_000), 'NZD': ('6N', 100_000),
}
FX_POSTOYANNYE = {'HKD': 0.1282, 'KRW': 0.00072, 'SGD': 0.78}   # ответ 2.1

# --- издержки: комиссия IBKR Tiered за сторону за контракт --------------------
KOMISSIYA_BIRZHA = {
    'HKFE': 0.98, 'ASX': 1.59, 'ICE Europe': 1.75, 'Eurex': 2.49,
    'CME': 2.25, 'CBOT': 2.25, 'NYMEX': 2.25, 'ICE US': 2.25,
    'KCBT': 2.25, 'MIAX': 2.25,
    'SGX': 2.11, 'KRX': 2.50, 'EURONEXT': 1.39, 'ME': 1.70,      # ответ 8.1
}
KOMISSIYA_VALJUTNYE = 2.15
KOMISSIYA_MIKRO_CME = 0.55          # ответ 8.2 — за один микроконтракт CME/NYMEX
KOMISSIYA_PROCHEE = 2.06
VALJUTNYE_FJUCHERSY = ('6A', '6B', '6C', '6E', '6J', '6M', '6N', '6S')

# ответ 8.3: микроверсии применены к пяти рынкам широкой ноги; делитель номинала
MIKRO = {'GC': 10, 'SI': 5, 'HSI': 5, 'FDAX': 5, 'SNK': 40}
MIKRO_CME = ('GC', 'SI')            # у этих комиссия микро-тарифная, 0.55

# --- разрешённые неоднозначности --------------------------------------------
NASTROJKI = dict(
    n_v_formule='vse_dostupnye',   # ответ 5.1 (вариант 'v_pozicii' — второй прогон)
    pereschet='mesyachnyj',        # ответ 5.6
    ryad_signala='cena',           # ответ 4.1: сигнал по нескорректированной склейке
    potolok_nominala=3.00,         # ответ 5.5
    pol_procentil=10,              # ответ 5.4
    pol_min_dnej=750,              # ответ 5.4
    rho_min_par_mesyacev=48,       # ответ 5.2
    rho_min_istorii=60,            # ответ 5.2
    rho_do_nakopleniya=0.10,
    nachalo='1997-09-09',          # ответ 1.3
    konec='2026-08-07',
    baza_nachisleniya=360,         # ответ 3.1
    platim_rolly=True,             # ответ 7.5: второй ряд — с False
    delim_kapital_popolam=True,    # ответ 5.7: ежемесячная перебалансировка 50/50
)

# гарантийные депозиты теперь есть в спецификациях выгрузки №1 (ES 16 060, ZN 2 063)
MARZHA_DOP = {}
