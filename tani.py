"""Geçici tanı: endeks ve hisse tarihlerinin hizalı olup olmadığını kontrol eder."""
import sys, json
import pandas as pd, numpy as np, yfinance as yf
sys.path.insert(0, ".")
import gunluk_rapor as g

out = []
def p(*a): out.append(" ".join(str(x) for x in a))

syms = ["^GSPC", "AAPL", "XOM", "BRK-B", "MSFT", "KO"]
ham = {}
for s in syms:
    t = yf.Ticker(s)
    df = t.history(start=g._baslangic(), auto_adjust=True, actions=False)
    ham[s] = df
    p(s, "tz", df.index.tz, "n", len(df), "ilk", df.index[:2].tolist(), "son", df.index[-3:].tolist())
# Sıralı indirme ile tarih hizası
c = pd.DataFrame({s: d["Close"].set_axis(d.index.tz_localize(None).normalize()) for s, d in ham.items()})
r = c.pct_change(fill_method=None) if "fill_method" in c.pct_change.__code__.co_varnames else c / c.shift(1) - 1
r = c / c.shift(1) - 1
for s in syms[1:]:
    p(s, "kor0", round(r[s].tail(60).corr(r["^GSPC"].tail(60)), 3),
      "kor_lag+1", round(r[s].tail(60).corr(r["^GSPC"].shift(1).tail(60)), 3),
      "kor_lag-1", round(r[s].tail(60).corr(r["^GSPC"].shift(-1).tail(60)), 3))
# Script'in kendi _indir'i (havuz, 4 işçi) ile aynı semboller
v = g._indir(syms, start=g._baslangic())
cc = v["Close"]
p("_indir kolonlar", list(cc.columns), "n", len(cc))
for s in syms:
    a = c[s].dropna(); b = cc[s].dropna()
    ortak = a.index.intersection(b.index)
    p(s, "sıralı vs havuz max fark", float((a[ortak] - b[ortak]).abs().max()), "ortak", len(ortak))
# Daha büyük havuz testi: 200 hisse, karışma var mı?
liste = pd.read_csv("veri/hisseler_abd.csv")["sembol"].tolist()[:200] if __import__("os").path.exists("veri/hisseler_abd.csv") else []
if liste:
    v2 = g._indir(liste + ["AAPL", "XOM"], start=g._baslangic())
    c2 = v2["Close"]
    for s in ["AAPL", "XOM"]:
        a = c[s].dropna(); b = c2[s].dropna(); o = a.index.intersection(b.index)
        p("200lük havuzda", s, "max fark", float((a[o] - b[o]).abs().max()))
    # Hisselerin kendi aralarında aynı seri var mı (karışma işareti)?
    son = c2.iloc[-30:].T.round(4)
    dup = son[son.duplicated(keep=False) & son.notna().all(axis=1)]
    p("son 30 günü birebir aynı olan hisse sayısı:", len(dup))
open("tani_cikti.txt", "w").write("\n".join(out))
print("\n".join(out))
