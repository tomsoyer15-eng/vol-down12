#!/usr/bin/env python3
"""Порт расчётной части dataviz/scripts/validate_palette.js на Python
(в системе нет JS-рантайма). Константы и матрицы взяты из оригинала без изменений."""
import math, sys

BAND = {'light': (0.43, 0.77), 'dark': (0.48, 0.67)}
CHROMA_FLOOR = 0.10
CVD_TARGET, CVD_FLOOR = 8.0, 6.0
NORMAL_FLOOR = 15.0
CONTRAST_MIN = 3.0
SURFACE = {'light': '#fcfcfb', 'dark': '#1a1a19'}
MACHADO = {
 'protan': [[0.152286,1.052583,-0.204868],[0.114503,0.786281,0.099216],[-0.003882,-0.048116,1.051998]],
 'deutan': [[0.367322,0.860646,-0.227968],[0.280085,0.672501,0.047413],[-0.011820,0.042940,0.968881]],
 'tritan': [[1.255528,-0.076749,-0.178779],[-0.078411,0.930809,0.147602],[0.004733,0.691367,0.303900]]}

def hex2srgb(h):
    h = h.strip().lstrip('#')
    return [int(h[i:i+2],16)/255 for i in (0,2,4)]
def s2lin(c): return c/12.92 if c <= 0.04045 else ((c+0.055)/1.055)**2.4
def lin(h): return [s2lin(c) for c in hex2srgb(h)]
def rel_lum(h):
    r,g,b = lin(h); return 0.2126*r+0.7152*g+0.0722*b
def contrast(a,b):
    hi,lo = sorted([rel_lum(a),rel_lum(b)], reverse=True); return (hi+0.05)/(lo+0.05)
def oklab_from_lin(rgb):
    r,g,b = rgb
    l = (0.4122214708*r+0.5363325363*g+0.0514459929*b)**(1/3)
    m = (0.2119034982*r+0.6806995451*g+0.1073969566*b)**(1/3)
    s = (0.0883024619*r+0.2817188376*g+0.6299787005*b)**(1/3)
    return [0.2104542553*l+0.7936177850*m-0.0040720468*s,
            1.9779984951*l-2.4285922050*m+0.4505937099*s,
            0.0259040371*l+0.7827717662*m-0.8086757660*s]
def oklch(h):
    L,a,b = oklab_from_lin(lin(h)); return L, math.hypot(a,b)
def simulate(h, kind):
    r,g,b = lin(h); M = MACHADO[kind]
    return [min(1,max(0, M[i][0]*r+M[i][1]*g+M[i][2]*b)) for i in range(3)]
def deltaE(h1,h2,kind=None):
    a = oklab_from_lin(simulate(h1,kind) if kind else lin(h1))
    b = oklab_from_lin(simulate(h2,kind) if kind else lin(h2))
    return 100*math.dist(a,b)

def validate(palette, mode='light', surface=None, pairs='adjacent'):
    surface = surface or SURFACE[mode]
    lo,hi = BAND[mode]; rep=[]; ok=True
    off=[(c,round(oklch(c)[0],3)) for c in palette if not (lo <= oklch(c)[0] <= hi)]
    ok &= not off; rep.append(('Lightness band', 'pass' if not off else 'FAIL',
        f'вне полосы: {off}' if off else f'все {len(palette)} внутри L {lo}–{hi}'))
    low=[(c,round(oklch(c)[1],3)) for c in palette if oklch(c)[1] < CHROMA_FLOOR]
    ok &= not low; rep.append(('Chroma floor', 'pass' if not low else 'FAIL',
        f'ниже порога: {low}' if low else f'все {len(palette)} >= {CHROMA_FLOOR}'))
    n=len(palette)
    pl=[(i,j) for i in range(n) for j in range(i+1,n)] if pairs=='all' else [(i,i+1) for i in range(n-1)]
    worst=None
    for kind in ('protan','deutan'):
        for i,j in pl:
            d=deltaE(palette[i],palette[j],kind)
            if worst is None or d<worst[0]: worst=(d,kind,palette[i],palette[j])
    tri=min((deltaE(palette[i],palette[j],'tritan') for i,j in pl), default=99)
    wd=worst[0] if worst else 99
    st='pass' if wd>=CVD_TARGET else ('floor' if wd>=CVD_FLOOR else 'FAIL')
    if st=='FAIL': ok=False
    rep.append(('CVD separation', st, f'худшая пара {worst[3]}↔{worst[2]} ΔE {wd:.1f} ({worst[1]}) · tritan {tri:.1f}'))
    nw=min(((deltaE(palette[i],palette[j]),palette[i],palette[j]) for i,j in pl), default=(99,'',''))
    ns='pass' if nw[0]>=NORMAL_FLOOR else 'FAIL'
    if ns=='FAIL': ok=False
    rep.append(('Normal-vision floor', ns, f'худшая пара {nw[2]}↔{nw[1]} ΔE {nw[0]:.1f} (нормальное зрение)'))
    cw=[(c,round(contrast(c,surface),2)) for c in palette if contrast(c,surface)<CONTRAST_MIN]
    rep.append(('Contrast vs surface', 'pass' if not cw else 'WARN',
        f'ниже {CONTRAST_MIN}:1 на {surface}: {cw}' if cw else f'все >= {CONTRAST_MIN}:1'))
    return ok, rep

if __name__=='__main__':
    pal=[c.strip() for c in sys.argv[1].split(',') if c.strip()]
    mode=sys.argv[2] if len(sys.argv)>2 else 'light'
    pairs=sys.argv[3] if len(sys.argv)>3 else 'adjacent'
    ok,rep=validate(pal,mode=mode,pairs=pairs)
    print(f'палитра {pal}  режим {mode}  пары {pairs}')
    for name,state,detail in rep: print(f'  [{state:>5}] {name:<22} {detail}')
    print('ИТОГ:', 'проходит' if ok else 'НЕ ПРОХОДИТ')
