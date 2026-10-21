#!/usr/bin/env python3
"""Günlük hisse raporu için bağımsız doğrulama betiği.

1) Rapordaki (tum.csv) göstergeleri Yahoo verisinden tanımlarına göre yeniden hesaplar.
2) Yahoo kapanışlarını bağımsız bir kaynakla (ABD: Stooq / Nasdaq, BIST: İş Yatırım) karşılaştırır.
3) ABD için S&P 500 korelasyonunu bağımsız kaynaktan yeniden hesaplar.

Kullanım:
  python dogrula.py --piyasa abd --csv tum.csv --cikti dogrulama.html [--json d.json] [--orneklem 40] [--deneme]

Bağımlılıklar: pandas, numpy, requests, yfinance>=1.3 (yalnızca gerçek modda).
Çıkış kodu her zaman 0'dır (sorunlar çıktılarda raporlanır); yalnızca geçersiz argümanlarda 2.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import io
import json
import math
import os
import sys
import time
import traceback
import urllib.parse
import zlib

import numpy as np
import pandas as pd

ENDEKS = {"bist": "XU100.IS", "abd": "^GSPC"}
GECMIS_GUN = 400          # Yahoo geçmişi: bugün - 400 takvim günü
CAPRAZ_GUN = 30           # kaynak karşılaştırması: son ~30 işlem günü
BAGIMSIZ_TAKVIM_GUN = 150  # bağımsız kaynaktan istenen geçmiş (60 günlük korelasyon için yeterli)
KOR_BAGIMSIZ_TOL = 0.10   # rapor korelasyonu ile bağımsız kaynaktan hesaplanan arasındaki izinli fark
ZAMAN_ASIMI = 20
BEKLEME = 0.3
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# (sütun, etiket, görünüm, tolerans türü, tolerans)
METRIKLER = [
    ("kapanis", "Kapanış", "fiyat", "rel", 0.005),
    ("gunluk", "Günlük getiri", "yuzde", "abs", 0.003),
    ("hafta", "Haftalık getiri", "yuzde", "abs", 0.003),
    ("ay", "Aylık getiri", "yuzde", "abs", 0.003),
    ("rsi", "RSI (14)", "sayi1", "abs", 1.0),
    ("sma200_fark", "SMA200'e uzaklık", "yuzde", "abs", 0.003),
    ("zirveden_uzaklik", "52 hafta zirveye uzaklık", "yuzde", "abs", 0.003),
    ("korelasyon", "Endeks korelasyonu (60g)", "sayi2", "abs", 0.05),
    ("h_hacim_primi", "Hacim primi", "sayi2", "rel", 0.02),
    ("h_max_etkisi", "En yüksek günlük getiri (21g)", "yuzde", "abs", 0.003),
    ("h_momentum", "Momentum (12-1 ay)", "yuzde", "abs", 0.005),
    ("h_dusuk_oyn", "Yıllık oynaklık", "yuzde", "abs", 0.01),
    ("h_dusuk_beta", "Beta (252g)", "sayi2", "abs", 0.05),
]
METRIK_SOZ = {m[0]: m for m in METRIKLER}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def sayi(x):
    """float'a çevir; olmazsa NaN."""
    try:
        if x is None:
            return float("nan")
        if isinstance(x, str):
            x = x.strip().replace(" ", "")
            if x == "":
                return float("nan")
            if "," in x and "." in x:
                x = x.replace(".", "").replace(",", ".")
            else:
                x = x.replace(",", ".")
        return float(x)
    except Exception:
        return float("nan")


def gecerli(x) -> bool:
    return x is not None and isinstance(x, (int, float, np.floating, np.integer)) and math.isfinite(float(x))


# ----------------------------------------------------------------------------
# Gösterge hesapları (tanımlardan, bağımsız)
# ----------------------------------------------------------------------------

def getiri(c: pd.Series) -> pd.Series:
    return c / c.shift(1) - 1


def rsi_hesapla(c: pd.Series, n: int = 14) -> float:
    d = c.astype(float).diff()
    kazanc = d.clip(lower=0)
    kayip = (-d).clip(lower=0)
    ok = kazanc.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    ak = kayip.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    if len(c) < n + 1:
        return float("nan")
    g, l = ok.iloc[-1], ak.iloc[-1]
    if not (math.isfinite(g) and math.isfinite(l)):
        return float("nan")
    if l == 0:
        return 100.0 if g > 0 else float("nan")
    return float(100 - 100 / (1 + g / l))


def beta_hesapla(rs: pd.Series, ri: pd.Series, en_az: int = 120) -> float:
    m = rs.notna() & ri.notna()
    if m.sum() < en_az:
        return float("nan")
    a, b = rs[m].to_numpy(float), ri[m].to_numpy(float)
    v = np.var(b, ddof=1)
    if v == 0:
        return float("nan")
    return float(np.cov(a, b, ddof=1)[0, 1] / v)


def korelasyon_hesapla(rs: pd.Series, ri: pd.Series, en_az: int = 40) -> float:
    m = rs.notna() & ri.notna()
    if m.sum() < en_az:
        return float("nan")
    a, b = rs[m].to_numpy(float), ri[m].to_numpy(float)
    if np.std(a) == 0 or np.std(b) == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def endeks_getirisi_hizala(endeks_kapanis: pd.Series | None, tarihler: pd.Index) -> pd.Series:
    """Endeks kapanışını hissenin işlem günlerine hizalar, sonra getiri alır (doldurma yok)."""
    if endeks_kapanis is None or len(endeks_kapanis) == 0:
        return pd.Series(np.nan, index=tarihler)
    e = endeks_kapanis[~endeks_kapanis.index.duplicated(keep="last")].reindex(tarihler)
    return getiri(e)


def gostergeleri_hesapla(df: pd.DataFrame, endeks_kapanis: pd.Series | None) -> dict:
    """df: Close, High, Volume sütunlu, tarih indeksli (artan). Dönüş: {metrik: değer}."""
    c = df["Close"].astype(float)
    n = len(c)
    r = getiri(c)
    nan = float("nan")
    o = {}
    o["kapanis"] = float(c.iloc[-1]) if n >= 1 else nan
    o["gunluk"] = float(c.iloc[-1] / c.iloc[-2] - 1) if n >= 2 else nan
    o["hafta"] = float(c.iloc[-1] / c.iloc[-6] - 1) if n >= 6 else nan
    o["ay"] = float(c.iloc[-1] / c.iloc[-22] - 1) if n >= 22 else nan
    o["rsi"] = rsi_hesapla(c)
    o["sma200_fark"] = float(c.iloc[-1] / c.iloc[-200:].mean() - 1) if n >= 200 else nan
    if "High" in df and n >= 1:
        h = df["High"].astype(float).iloc[-252:].max()
        o["zirveden_uzaklik"] = float(c.iloc[-1] / h - 1) if gecerli(h) and h > 0 else nan
    else:
        o["zirveden_uzaklik"] = nan
    ri = endeks_getirisi_hizala(endeks_kapanis, c.index)
    o["korelasyon"] = korelasyon_hesapla(r.iloc[-60:], ri.iloc[-60:], 40)
    if "Volume" in df and n >= 55:
        v = df["Volume"].astype(float)
        pay, payda = v.iloc[-5:].mean(), v.iloc[-55:-5].mean()
        o["h_hacim_primi"] = float(pay / payda) if gecerli(payda) and payda > 0 else nan
    else:
        o["h_hacim_primi"] = nan
    o["h_max_etkisi"] = float(r.iloc[-21:].max()) if n >= 2 else nan
    o["h_momentum"] = float(c.iloc[-22] / c.iloc[-253] - 1) if n >= 253 else nan
    r252 = r.iloc[-252:].dropna()
    o["h_dusuk_oyn"] = float(r252.std(ddof=1) * math.sqrt(252)) if len(r252) >= 120 else nan
    o["h_dusuk_beta"] = beta_hesapla(r.iloc[-252:], ri.iloc[-252:], 120)
    return o


def metrik_karsilastir(metrik: str, rapor: float, yeni: float) -> dict:
    _, etiket, gorunum, tur, tol = METRIK_SOZ[metrik]
    k = {"metrik": metrik, "etiket": etiket, "rapor": rapor, "yeni": yeni,
         "tolerans_turu": tur, "tolerans": tol, "fark": None, "durum": "", "not": ""}
    if not gecerli(rapor):
        k.update(durum="atlandı", **{"not": "raporda değer yok"})
        return k
    if not gecerli(yeni):
        k.update(durum="atlandı", **{"not": "yeniden hesaplanamadı (yetersiz veri)"})
        return k
    if tur == "rel":
        fark = abs(yeni / rapor - 1) if rapor != 0 else abs(yeni - rapor)
    else:
        fark = abs(yeni - rapor)
    k["fark"] = float(fark)
    k["durum"] = "geçti" if fark <= tol + 1e-12 else "hata"
    return k


# ----------------------------------------------------------------------------
# Ağ katmanı (gerçek + deneme)
# ----------------------------------------------------------------------------

class Ag:
    """Gerçek HTTP. get() asla istisna fırlatmaz: (durum_kodu, metin, hata)."""

    def __init__(self):
        import requests
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9,tr;q=0.8"})

    def get(self, url, headers=None):
        try:
            r = self.s.get(url, headers=headers or {}, timeout=ZAMAN_ASIMI)
            return r.status_code, r.text, None
        except Exception as e:  # noqa: BLE001
            return None, None, f"{type(e).__name__}: {str(e)[:150]}"


class YahooKaynak:
    def __init__(self):
        import yfinance as yf  # noqa: F401
        self.yf = yf

    def indir(self, sym: str, start: dt.date, auto_adjust: bool):
        son_hata = None
        for deneme in range(2):
            try:
                df = self.yf.Ticker(sym).history(start=start.isoformat(), auto_adjust=auto_adjust,
                                                 actions=False, timeout=ZAMAN_ASIMI)
                if df is not None and len(df) > 0:
                    return df, None
                son_hata = "boş veri"
            except Exception as e:  # noqa: BLE001
                son_hata = f"{type(e).__name__}: {str(e)[:150]}"
            if deneme == 0:
                time.sleep(2)
        return None, son_hata


def yahoo_duzenle(df: pd.DataFrame | None) -> pd.DataFrame | None:
    if df is None or len(df) == 0 or "Close" not in df:
        return None
    df = df.copy()
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    df.index = idx.normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df[df["Close"].notna()]
    return df if len(df) else None


# ---- bağımsız kaynak ayrıştırıcıları ----

def stooq_ayristir(metin: str | None):
    if not metin:
        return None, "boş yanıt"
    bas = metin.lstrip()[:200]
    if bas.lower().startswith("<") or "<html" in bas.lower():
        return None, "CSV yerine HTML döndü (API anahtarı/çerez gerekebilir)"
    try:
        df = pd.read_csv(io.StringIO(metin))
    except Exception as e:  # noqa: BLE001
        return None, f"CSV okunamadı: {bas[:60]!r} ({type(e).__name__})"
    kol = {c.lower().strip(): c for c in df.columns}
    if "date" not in kol or "close" not in kol:
        return None, f"beklenmeyen içerik: {bas[:60]!r}"
    s = pd.Series(pd.to_numeric(df[kol["close"]], errors="coerce").to_numpy(),
                  index=pd.to_datetime(df[kol["date"]], errors="coerce"))
    s = s[s.index.notna() & s.notna()].sort_index()
    s = s[~s.index.duplicated(keep="last")]
    if len(s) == 0:
        return None, "veri yok"
    return s, None


def nasdaq_ayristir(metin: str | None):
    if not metin:
        return None, "boş yanıt"
    try:
        j = json.loads(metin)
    except Exception:  # noqa: BLE001
        return None, f"JSON değil: {metin[:60]!r}"
    try:
        satirlar = ((j.get("data") or {}).get("tradesTable") or {}).get("rows") or []
    except Exception:  # noqa: BLE001
        satirlar = []
    if not satirlar:
        durum = j.get("status") if isinstance(j, dict) else None
        return None, f"satır yok ({str(durum)[:100]})"
    tarih, kap = [], []
    for r in satirlar:
        try:
            t = dt.datetime.strptime(str(r.get("date")).strip(), "%m/%d/%Y")
            v = float(str(r.get("close")).replace("$", "").replace(",", "").strip())
            tarih.append(t)
            kap.append(v)
        except Exception:  # noqa: BLE001
            continue
    if not tarih:
        return None, f"tarih/kapanış ayrıştırılamadı; alanlar: {', '.join(map(str, satirlar[0].keys()))}"
    s = pd.Series(kap, index=pd.DatetimeIndex(tarih)).sort_index()
    return s[~s.index.duplicated(keep="last")], None


def _tarih_coz(x):
    if x is None:
        return None
    if isinstance(x, (int, float)) and math.isfinite(x):
        try:
            return pd.Timestamp(int(x), unit="ms").normalize()
        except Exception:  # noqa: BLE001
            return None
    s = str(x).strip()
    if s.startswith("/Date("):
        try:
            return pd.Timestamp(int(s[6:].split(")")[0].split("+")[0].split("-")[0]), unit="ms").normalize()
        except Exception:  # noqa: BLE001
            return None
    for f in ("%d-%m-%Y", "%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return pd.Timestamp(dt.datetime.strptime(s[:10], f))
        except Exception:  # noqa: BLE001
            pass
    try:
        return pd.Timestamp(s).tz_localize(None).normalize() if pd.Timestamp(s).tz else pd.Timestamp(s).normalize()
    except Exception:  # noqa: BLE001
        return None


def isyatirim_ayristir(metin: str | None):
    """Dönüş: (seri, hata, bilgi). Alan adları garanti olmadığından esnek arar."""
    if not metin:
        return None, "boş yanıt", {}
    try:
        j = json.loads(metin)
    except Exception:  # noqa: BLE001
        return None, f"JSON değil: {metin.lstrip()[:60]!r}", {}
    liste = j.get("value") if isinstance(j, dict) else None
    if not isinstance(liste, list):
        anahtar = list(j.keys()) if isinstance(j, dict) else type(j).__name__
        return None, f"'value' listesi yok; üst anahtarlar: {anahtar}", {}
    if not liste:
        return None, "veri yok (boş liste)", {}
    alanlar = [str(k) for k in liste[0].keys()]
    kap_aday = [k for k in alanlar if "KAPANIS" in k.upper()]
    if not kap_aday:
        return None, "kapanış alanı bulunamadı; alanlar: " + ", ".join(alanlar), {"alanlar": alanlar}
    kap = next((k for k in kap_aday if k.upper().startswith("HGDG_KAPANIS")), kap_aday[0])
    onek = kap.upper().split("KAPANIS")[0]
    tar_aday = [k for k in alanlar if "TARIH" in k.upper()]
    if not tar_aday:
        return None, "tarih alanı bulunamadı; alanlar: " + ", ".join(alanlar), {"alanlar": alanlar}
    tar = next((k for k in tar_aday if k.upper() == onek + "TARIH"), tar_aday[0])
    tarih, deger = [], []
    for r in liste:
        t = _tarih_coz(r.get(tar))
        v = sayi(r.get(kap))
        if t is not None and math.isfinite(v):
            tarih.append(t)
            deger.append(v)
    bilgi = {"kapanis_alani": kap, "tarih_alani": tar}
    if not tarih:
        return None, f"satırlar ayrıştırılamadı ({tar}/{kap}); alanlar: " + ", ".join(alanlar), {"alanlar": alanlar}
    s = pd.Series(deger, index=pd.DatetimeIndex(tarih)).sort_index()
    return s[~s.index.duplicated(keep="last")], None, bilgi


class BagimsizKaynak:
    def __init__(self, ag, bugun: dt.date):
        self.ag = ag
        self.bugun = bugun
        self.sayac = {}  # kaynak -> [deneme, başarı]

    def _say(self, ad, ok):
        d = self.sayac.setdefault(ad, [0, 0])
        d[0] += 1
        d[1] += 1 if ok else 0

    def stooq(self, kod: str):
        anahtar = os.environ.get("STOOQ_APIKEY", "").strip()
        url = f"https://stooq.com/q/d/l/?s={urllib.parse.quote(kod, safe='-.')}&i=d"
        if anahtar:
            url += "&apikey=" + urllib.parse.quote(anahtar)
        kodu, metin, hata = self.ag.get(url)
        if hata:
            self._say("Stooq", False)
            return None, f"ulaşılamadı ({hata})"
        if kodu != 200:
            self._say("Stooq", False)
            return None, f"HTTP {kodu}"
        s, h = stooq_ayristir(metin)
        self._say("Stooq", s is not None)
        return s, h

    def nasdaq(self, sym: str, varlik: str = "stocks"):
        bas = (self.bugun - dt.timedelta(days=BAGIMSIZ_TAKVIM_GUN)).isoformat()
        son = self.bugun.isoformat()
        adaylar = [sym] + ([sym.replace("-", ".")] if "-" in sym else [])
        hdr = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
               "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
        son_hata = None
        for a in adaylar:
            url = (f"https://api.nasdaq.com/api/quote/{urllib.parse.quote(a)}/historical?assetclass={varlik}"
                   f"&fromdate={bas}&todate={son}&limit=120")
            kodu, metin, hata = self.ag.get(url, headers=hdr)
            if hata:
                son_hata = f"ulaşılamadı ({hata})"
                continue
            if kodu != 200:
                son_hata = f"HTTP {kodu}"
                continue
            s, h = nasdaq_ayristir(metin)
            if s is not None:
                self._say("Nasdaq", True)
                return s, None
            son_hata = h
        self._say("Nasdaq", False)
        return None, son_hata

    def isyatirim(self, kod: str):
        bas = (self.bugun - dt.timedelta(days=BAGIMSIZ_TAKVIM_GUN)).strftime("%d-%m-%Y")
        son = self.bugun.strftime("%d-%m-%Y")
        url = ("https://www.isyatirim.com.tr/_layouts/15/Isyatirim.Website/Common/Data.aspx/HisseTekil"
               f"?hisse={urllib.parse.quote(kod)}&startdate={bas}&enddate={son}")
        hdr = {"User-Agent": UA, "Accept": "application/json, text/javascript, */*; q=0.01",
               "X-Requested-With": "XMLHttpRequest",
               "Referer": "https://www.isyatirim.com.tr/tr-tr/analiz/hisse/Sayfalar/sirket-karti.aspx"}
        kodu, metin, hata = self.ag.get(url, headers=hdr)
        if hata:
            self._say("İş Yatırım", False)
            return None, f"ulaşılamadı ({hata})", {}
        if kodu != 200:
            self._say("İş Yatırım", False)
            return None, f"HTTP {kodu}", {}
        s, h, bilgi = isyatirim_ayristir(metin)
        self._say("İş Yatırım", s is not None)
        return s, h, bilgi


# ----------------------------------------------------------------------------
# Kaynaklar arası karşılaştırma
# ----------------------------------------------------------------------------

def capraz_karsilastir(yahoo: pd.Series, diger: pd.Series, gun: int = CAPRAZ_GUN) -> dict:
    y = yahoo.dropna().iloc[-gun:]
    d = diger.dropna()
    d = d[d > 0]
    m = pd.concat([y.rename("y"), d.rename("d")], axis=1, join="inner").dropna().sort_index()
    o = {"eslesen_gun": int(len(m)), "son_tarih": None, "son_fark": None, "maks_fark": None,
         "gun_1yuzde": None, "getiri_kor": None, "yahoo_son": None, "diger_son": None}
    if len(m) == 0:
        o["durum"] = "eşleşme yok"
        return o
    fark = (m["d"] / m["y"] - 1).abs()
    o.update(son_tarih=m.index[-1].date().isoformat(), son_fark=float(fark.iloc[-1]),
             maks_fark=float(fark.max()), gun_1yuzde=int((fark > 0.01).sum()),
             yahoo_son=float(m["y"].iloc[-1]), diger_son=float(m["d"].iloc[-1]))
    if len(m) >= 6:
        ry, rd = getiri(m["y"]), getiri(m["d"])
        k = korelasyon_hesapla(ry, rd, 5)
        o["getiri_kor"] = k if gecerli(k) else None
    o["durum"] = "uyumsuz" if (o["son_fark"] > 0.005 or o["gun_1yuzde"] > 2) else "uyumlu"
    return o


# ----------------------------------------------------------------------------
# Deneme (çevrimdışı) modu
# ----------------------------------------------------------------------------

ABD_SEMBOLLER = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "BRK-B", "XOM", "JPM", "V", "TSLA",
                 "UNH", "JNJ", "WMT", "PG", "MA", "HD", "CVX", "KO", "PEP", "AVGO", "COST", "MRK", "ABBV",
                 "LLY", "ORCL", "BAC", "ADBE", "CRM", "NFLX", "AMD", "INTC", "CSCO", "TMO", "ACN", "MCD",
                 "DIS", "NKE", "QCOM", "TXN", "LIN", "PM", "HON", "IBM", "GE", "CAT", "BA", "GS"]
BIST_SEMBOLLER = [s + ".IS" for s in [
    "THYAO", "GARAN", "AKBNK", "ASELS", "BIMAS", "KCHOL", "ISCTR", "YKBNK", "TUPRS", "SAHOL", "EREGL",
    "SISE", "FROTO", "TCELL", "PGSUS", "KOZAL", "ASTOR", "TOASO", "PETKM", "HEKTS", "SASA", "EKGYO",
    "ARCLK", "TTKOM", "ENKAI", "VAKBN", "HALKB", "MGROS", "ULKER", "OYAKC", "DOAS", "TAVHL", "AEFES",
    "KRDMD", "GUBRF", "ALARK", "CIMSA", "SOKM", "TKFEN", "OTKAR", "BRSAN", "CCOLA", "ODAS", "AGHOL",
    "KONTR"]]

# Enjekte edilen sorunlar (rapor + kaynak)
DENEME = {
    "abd": {
        "csv_boz": {("AAPL", "korelasyon"): ("ata", 0.03), ("XOM", "korelasyon"): ("ata", -0.48),
                    ("MSFT", "rsi"): ("ekle", 6.0), ("NVDA", "kapanis"): ("carp", 1.01),
                    ("AMZN", "h_momentum"): ("ata", float("nan"))},
        "yahoo_yok": {"META"},
        "kaynak_uyumsuz": {"JPM"},       # Stooq son 4 gün %3 yüksek
        "stooq_yok": {"GOOGL", "V"},     # Stooq 'No data' -> Nasdaq kullanılır
        "hic_yok": {"BRK-B"},            # Stooq ve Nasdaq ulaşılamaz
        "tarih_eksik": {"AMZN"},         # bağımsız kaynakta bir gün eksik
        "endeks_stooq_html": True,       # ^spx Stooq'tan HTML döner -> Nasdaq SPX
    },
    "bist": {
        "csv_boz": {("THYAO.IS", "rsi"): ("ekle", 5.0), ("GARAN.IS", "gunluk"): ("ekle", 0.01),
                    ("ISCTR.IS", "h_dusuk_beta"): ("ekle", 0.2)},
        "yahoo_yok": {"KCHOL.IS"},
        "kaynak_uyumsuz": {"AKBNK.IS"},
        "garip_alan": {"ASELS.IS"},      # İş Yatırım alan adları beklenmedik
        "hic_yok": {"BIMAS.IS"},         # İş Yatırım ulaşılamaz
        "tarih_eksik": {"TUPRS.IS"},
    },
}


def _tohum(s: str) -> int:
    return zlib.crc32(s.encode()) & 0x7FFFFFFF


class DenemeEvreni:
    """Tüm kaynaklar için sentetik veri üretir."""

    def __init__(self, piyasa: str, semboller: list[str], bugun: dt.date):
        self.piyasa = piyasa
        self.cfg = DENEME[piyasa]
        son = pd.Timestamp(bugun)
        while son.weekday() >= 5:
            son -= pd.Timedelta(days=1)
        self.tarihler = pd.bdate_range(pd.Timestamp(bugun - dt.timedelta(days=GECMIS_GUN)), son)
        self.csv_son = self.tarihler[-2]   # rapor bir gün geride: kırpma yolunu sınar
        T = len(self.tarihler)
        rng = np.random.default_rng(_tohum(piyasa))
        seviye = 1000.0 if piyasa == "abd" else 10000.0
        ri = rng.normal(0.0004, 0.010, T)
        ri[0] = 0
        self.endeks = pd.Series(seviye * np.cumprod(1 + ri), index=self.tarihler)
        self.veri = {}  # sym -> (düzeltilmiş df, düzeltilmemiş kapanış)
        for i, s in enumerate(semboller):
            g = np.random.default_rng(_tohum(s))
            beta = g.uniform(0.6, 1.4)
            r = beta * ri + g.normal(0, 0.012, T)
            r[0] = 0
            c = g.uniform(20, 400) * np.cumprod(1 + r)
            hi = c * (1 + g.uniform(0, 0.02, T))
            lo = c * (1 - g.uniform(0, 0.02, T))
            vol = np.round(g.lognormal(14, 0.4, T))
            df = pd.DataFrame({"Open": c, "High": hi, "Low": lo, "Close": c, "Volume": vol}, index=self.tarihler)
            ham = pd.Series(c.copy(), index=self.tarihler)
            if i % 3 == 0:  # 120 gün önce temettü: düzeltilmemiş fiyat öncesinde daha yüksek
                ham.iloc[:-120] = ham.iloc[:-120] / 0.98
            self.veri[s] = (df, ham)

    # ---- Yahoo ----
    def yahoo(self, sym, start, auto_adjust):
        if sym == ENDEKS[self.piyasa]:
            e = self.endeks
            df = pd.DataFrame({"Open": e, "High": e * 1.005, "Low": e * 0.995, "Close": e, "Volume": 0.0})
        elif sym in self.cfg.get("yahoo_yok", set()) or sym not in self.veri:
            return None, "boş veri (deneme: Yahoo hatası)"
        else:
            adj, ham = self.veri[sym]
            df = adj.copy()
            if not auto_adjust:
                df["Adj Close"] = adj["Close"]
                df["Close"] = ham
        tz = "America/New_York" if self.piyasa == "abd" else "Europe/Istanbul"
        df = df.copy()
        df.index = df.index.tz_localize(tz)
        return df, None

    # ---- CSV ----
    def csv_uret(self, yol: str):
        satirlar = []
        n = len(self.veri)
        for i, (s, (adj, _)) in enumerate(self.veri.items()):
            guncel = i < n - 3
            son = self.csv_son if guncel else self.tarihler[-8]
            df = adj[adj.index <= son]
            m = gostergeleri_hesapla(df, self.endeks[self.endeks.index <= son])
            for (sym, k), (op, v) in self.cfg["csv_boz"].items():
                if sym == s:
                    m[k] = v if op == "ata" else (m[k] + v if op == "ekle" else m[k] * v)
            sat = {"sembol": s, "sektor": "Deneme", "guncel": guncel, "son_veri": son.date().isoformat(),
                   "ort_islem": float((df["Close"] * df["Volume"]).iloc[-20:].mean()) * (1 + (n - i) / 10)}
            sat.update(m)
            satirlar.append(sat)
        pd.DataFrame(satirlar).to_csv(yol, sep=";", decimal=",", encoding="utf-8-sig", index=False)

    # ---- HTTP ----
    def get(self, url, headers=None):
        u = urllib.parse.urlparse(url)
        q = dict(urllib.parse.parse_qsl(u.query))
        yanit = lambda t: (200, t, None)  # noqa: E731
        if "stooq.com" in u.netloc:
            kod = q.get("s", "")
            if kod == "^spx":
                if self.cfg.get("endeks_stooq_html"):
                    return yanit("<!DOCTYPE html><html><body>Get your apikey</body></html>")
                return yanit(self._csv_metni(self.endeks))
            sym = kod[:-3].upper() if kod.endswith(".us") else kod.upper()
            if sym in self.cfg.get("hic_yok", set()):
                return None, None, "ConnectTimeout: deneme"
            if sym in self.cfg.get("stooq_yok", set()) or sym not in self.veri:
                return yanit("No data")
            return yanit(self._csv_metni(self._bagimsiz_seri(sym)))
        if "nasdaq.com" in u.netloc:
            sym = u.path.split("/")[3].upper()
            if sym == "SPX":
                return yanit(self._nasdaq_metni(self.endeks, dolar=False))
            sym = sym.replace(".", "-")
            if sym in self.cfg.get("hic_yok", set()):
                return None, None, "ConnectTimeout: deneme"
            if sym not in self.veri:
                return yanit(json.dumps({"data": None, "status": {"rCode": 400}}))
            return yanit(self._nasdaq_metni(self._bagimsiz_seri(sym)))
        if "isyatirim.com.tr" in u.netloc:
            sym = q.get("hisse", "") + ".IS"
            if sym in self.cfg.get("hic_yok", set()):
                return None, None, "ReadTimeout: deneme"
            if sym not in self.veri:
                return yanit(json.dumps({"ok": True, "value": []}))
            s = self._bagimsiz_seri(sym).iloc[-90:]
            if sym in self.cfg.get("garip_alan", set()):
                v = [{"KOD": q["hisse"], "GUN": t.strftime("%d-%m-%Y"), "FIYAT": round(x, 2)} for t, x in s.items()]
            else:
                v = [{"HGDG_HS_KODU": q["hisse"], "HGDG_TARIH": t.strftime("%d-%m-%Y"),
                      "HGDG_KAPANIS": round(x, 2), "HG_KAPANIS": round(x, 2), "END_TARIH": t.strftime("%d-%m-%Y"),
                      "END_DEGER": 10000.0} for t, x in s.items()]
            return yanit(json.dumps({"ok": True, "errorCode": None, "value": v}))
        return None, None, "deneme: bilinmeyen adres"

    def _bagimsiz_seri(self, sym):
        ham = self.veri[sym][1].round(2).copy()
        if sym in self.cfg.get("kaynak_uyumsuz", set()):
            ham.iloc[-4:] = ham.iloc[-4:] * 1.03
        if sym in self.cfg.get("tarih_eksik", set()):
            ham = ham.drop(ham.index[-10])
        return ham

    @staticmethod
    def _csv_metni(s):
        satir = ["Date,Open,High,Low,Close,Volume"]
        satir += [f"{t:%Y-%m-%d},{x:.2f},{x:.2f},{x:.2f},{x:.2f},1000" for t, x in s.items()]
        return "\n".join(satir) + "\n"

    @staticmethod
    def _nasdaq_metni(s, dolar=True):
        rows = [{"date": f"{t:%m/%d/%Y}", "close": (f"${x:,.2f}" if dolar else f"{x:,.2f}"),
                 "volume": "1,000", "open": "", "high": "", "low": ""} for t, x in s.iloc[::-1].iloc[:100].items()]
        return json.dumps({"data": {"symbol": "X", "tradesTable": {"rows": rows}}, "status": {"rCode": 200}})


# ----------------------------------------------------------------------------
# CSV ve örneklem
# ----------------------------------------------------------------------------

def csv_oku(yol: str) -> pd.DataFrame:
    df = pd.read_csv(yol, sep=";", decimal=",", encoding="utf-8-sig", dtype={"sembol": str})
    df.columns = [str(c).strip() for c in df.columns]
    if "sembol" not in df.columns:
        df = df.rename(columns={df.columns[0]: "sembol"})
    df["sembol"] = df["sembol"].astype(str).str.strip()
    for m in list(METRIK_SOZ) + ["ort_islem"]:
        if m in df.columns:
            if pd.api.types.is_numeric_dtype(df[m]) and not pd.api.types.is_bool_dtype(df[m]):
                df[m] = df[m].astype(float)
            else:
                df[m] = df[m].map(sayi).astype(float)
    if "guncel" in df.columns:
        df["guncel"] = df["guncel"].map(lambda x: str(x).strip().lower() in ("true", "1", "evet", "yes", "doğru"))
    else:
        df["guncel"] = True
    if "son_veri" in df.columns:
        df["son_veri"] = pd.to_datetime(df["son_veri"], errors="coerce")
    return df


def orneklem_sec(df: pd.DataFrame, n: int, tohum: int) -> list[str]:
    g = df[df["guncel"]].drop_duplicates("sembol")
    ust_n = min(25, n)
    if "ort_islem" in g.columns:
        ust = g.sort_values("ort_islem", ascending=False, na_position="last").head(ust_n)
    else:
        ust = g.iloc[0:0]
    kalan = g[~g["sembol"].isin(ust["sembol"])]
    k = max(0, min(n - len(ust), len(kalan)))
    rast = kalan.sample(n=k, random_state=tohum % (2**32)) if k else kalan.iloc[0:0]
    return list(ust["sembol"]) + list(rast["sembol"])


# ----------------------------------------------------------------------------
# Ana akış
# ----------------------------------------------------------------------------

def dogrula(args) -> dict:
    bugun = dt.date.today()
    start = bugun - dt.timedelta(days=GECMIS_GUN)
    endeks_sym = ENDEKS[args.piyasa]

    evren = None
    if args.deneme:
        if os.path.exists(args.csv):
            semboller = list(csv_oku(args.csv)["sembol"])
            evren = DenemeEvreni(args.piyasa, semboller, bugun)
        else:
            semboller = ABD_SEMBOLLER if args.piyasa == "abd" else BIST_SEMBOLLER
            evren = DenemeEvreni(args.piyasa, semboller, bugun)
            os.makedirs(os.path.dirname(os.path.abspath(args.csv)), exist_ok=True)
            evren.csv_uret(args.csv)
            log(f"[deneme] sentetik CSV yazıldı: {args.csv}")
        yahoo_indir = evren.yahoo
        ag = evren
        bekle = 0.0
    else:
        ag = Ag()
        bekle = BEKLEME
        try:
            yk = YahooKaynak()
            yahoo_indir = yk.indir
        except Exception as e:  # noqa: BLE001
            hata = f"yfinance yüklenemedi: {e}"
            yahoo_indir = lambda *a, **k: (None, hata)  # noqa: E731

    sonuc = {"piyasa": args.piyasa, "endeks": endeks_sym, "deneme": bool(args.deneme),
             "olusturma": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "csv": os.path.abspath(args.csv),
             "hata": None, "hisseler": [], "endeks_bagimsiz": None}

    df = csv_oku(args.csv)
    son_veri_maks = df["son_veri"].max() if "son_veri" in df.columns else pd.NaT
    if pd.isna(son_veri_maks):
        tohum = int(bugun.strftime("%Y%m%d"))
        sonuc["rapor_tarihi"] = None
    else:
        tohum = int(son_veri_maks.strftime("%Y%m%d"))
        sonuc["rapor_tarihi"] = son_veri_maks.date().isoformat()
    sonuc["tohum"] = tohum
    sonuc["csv_satir"] = int(len(df))
    sonuc["csv_guncel"] = int(df["guncel"].sum())
    sonuc["eksik_sutunlar"] = [m for m in METRIK_SOZ if m not in df.columns]
    orneklem = orneklem_sec(df, args.orneklem, tohum)
    sonuc["orneklem"] = orneklem
    log(f"Örneklem: {len(orneklem)} hisse (tohum {tohum})")

    sayac = {"Yahoo": [0, 0]}

    def yahoo(sym, adj):
        d, h = yahoo_indir(sym, start, adj)
        d = yahoo_duzenle(d)
        sayac["Yahoo"][0] += 1
        sayac["Yahoo"][1] += 1 if d is not None else 0
        if bekle:
            time.sleep(bekle)
        return d, (h or "boş veri") if d is None else None

    endeks_df, endeks_hata = yahoo(endeks_sym, True)
    endeks_kap = endeks_df["Close"] if endeks_df is not None else None
    sonuc["endeks_yahoo_hata"] = endeks_hata

    bk = BagimsizKaynak(ag, bugun)
    endeks_bag = None
    if args.piyasa == "abd":
        s, h = bk.stooq("^spx")
        kaynak = "Stooq"
        if s is None:
            s2, h2 = bk.nasdaq("SPX", "index")
            if s2 is not None:
                s, kaynak = s2, "Nasdaq"
            else:
                h = f"Stooq: {h}; Nasdaq: {h2}"
        endeks_bag = s
        sonuc["endeks_bagimsiz"] = {"kaynak": kaynak if s is not None else None,
                                    "durum": "alındı" if s is not None else "ulaşılamadı",
                                    "hata": None if s is not None else h,
                                    "satir": int(len(s)) if s is not None else 0}
        if bekle:
            time.sleep(bekle)

    satir_sozu = df.drop_duplicates("sembol").set_index("sembol")
    for i, sym in enumerate(orneklem, 1):
        log(f"[{i}/{len(orneklem)}] {sym}")
        r = satir_sozu.loc[sym]
        son_veri = r["son_veri"] if "son_veri" in satir_sozu.columns and pd.notna(r.get("son_veri")) else son_veri_maks
        h_kayit = {"sembol": sym, "son_veri_rapor": son_veri.date().isoformat() if pd.notna(son_veri) else None,
                   "ort_islem": float(r["ort_islem"]) if "ort_islem" in satir_sozu.columns and gecerli(r["ort_islem"]) else None}
        # ---- Bölüm 1 ----
        adj, hata = yahoo(sym, True)
        if adj is None:
            h_kayit.update(yahoo_hata=hata, metrikler=[])
        else:
            h_kayit["yahoo_son_tarih"] = adj.index[-1].date().isoformat()
            kirp = pd.notna(son_veri) and adj.index[-1] > son_veri
            if kirp:
                adj = adj[adj.index <= son_veri]
            h_kayit["kirpildi"] = bool(kirp)
            h_kayit["satir"] = int(len(adj))
            h_kayit["yahoo_hata"] = None
            if len(adj) == 0:
                h_kayit.update(yahoo_hata="rapor tarihinden önce veri yok", metrikler=[])
            else:
                if pd.notna(son_veri) and adj.index[-1] < son_veri:
                    h_kayit["uyari"] = (f"Yahoo'daki son gün ({adj.index[-1].date()}) rapordaki son_veri'den "
                                        f"({son_veri.date()}) eski")
                ek = endeks_kap[endeks_kap.index <= adj.index[-1]] if endeks_kap is not None else None
                yeni = gostergeleri_hesapla(adj, ek)
                h_kayit["metrikler"] = [metrik_karsilastir(m, float(r[m]) if m in satir_sozu.columns else float("nan"),
                                                           yeni[m]) for m in METRIK_SOZ]
                h_kayit["korelasyon_yahoo"] = yeni["korelasyon"] if gecerli(yeni["korelasyon"]) else None
        h_kayit["korelasyon_rapor"] = float(r["korelasyon"]) if "korelasyon" in satir_sozu.columns and \
            gecerli(r["korelasyon"]) else None

        # ---- Bölüm 2 ----
        ham, hata2 = yahoo(sym, False)
        cap = {"kaynak": None, "durum": None, "not": None}
        diger = None
        if args.piyasa == "abd":
            kod = sym.lower() + ".us"
            diger, h_s = bk.stooq(kod)
            cap["kaynak"] = "Stooq"
            if diger is None:
                if bekle:
                    time.sleep(bekle)
                diger, h_n = bk.nasdaq(sym.upper())
                cap["kaynak"] = "Nasdaq" if diger is not None else None
                if diger is None:
                    cap["not"] = f"Stooq: {h_s}; Nasdaq: {h_n}"
        else:
            kod = sym[:-3] if sym.upper().endswith(".IS") else sym
            diger, h_i, bilgi = bk.isyatirim(kod)
            cap["kaynak"] = "İş Yatırım" if diger is not None else None
            cap["alan"] = bilgi
            if diger is None:
                cap["not"] = f"İş Yatırım: {h_i}"
        if bekle:
            time.sleep(bekle)
        if diger is None:
            ayr = bool((cap.get("alan") or {}).get("alanlar")) or "JSON değil" in (cap["not"] or "")
            cap["durum"] = "ayrıştırılamadı" if ayr else "ulaşılamadı"
        elif ham is None:
            cap["durum"] = "Yahoo yok"
            cap["not"] = f"Yahoo (düzeltilmemiş): {hata2}"
        else:
            cap.update(capraz_karsilastir(ham["Close"], diger))
        h_kayit["capraz"] = cap

        # ---- Bölüm 3 (ABD) ----
        if args.piyasa == "abd":
            kb = None
            if diger is not None and endeks_bag is not None:
                d = diger[diger.index <= son_veri] if pd.notna(son_veri) else diger
                if len(d) >= 41:
                    rs = getiri(d)
                    ri = endeks_getirisi_hizala(endeks_bag, d.index)
                    k = korelasyon_hesapla(rs.iloc[-60:], ri.iloc[-60:], 40)
                    kb = k if gecerli(k) else None
            h_kayit["korelasyon_bagimsiz"] = kb
            kr = h_kayit["korelasyon_rapor"]
            if kb is not None and kr is not None:
                h_kayit["korelasyon_bag_fark"] = kb - kr
                h_kayit["korelasyon_bag_durum"] = "uyumlu" if abs(kb - kr) <= KOR_BAGIMSIZ_TOL else "farklı"
            else:
                h_kayit["korelasyon_bag_fark"] = None
                h_kayit["korelasyon_bag_durum"] = "hesaplanamadı"
        sonuc["hisseler"].append(h_kayit)

    for k, v in bk.sayac.items():
        sayac[k] = v
    sonuc["kaynaklar"] = {k: {"deneme": v[0], "basari": v[1]} for k, v in sayac.items()}
    sonuc["ozet"] = ozet_cikar(sonuc)
    return sonuc


def ozet_cikar(s: dict) -> dict:
    tum = [m for h in s["hisseler"] for m in h.get("metrikler", [])]
    kontrol = [m for m in tum if m["durum"] in ("geçti", "hata")]
    hata = [m for m in kontrol if m["durum"] == "hata"]
    indirilemeyen = [h["sembol"] for h in s["hisseler"] if h.get("yahoo_hata")]
    cap = [h["capraz"] for h in s["hisseler"]]
    cap_kontrol = [c for c in cap if c["durum"] in ("uyumlu", "uyumsuz")]
    cap_uyumsuz = [c for c in cap_kontrol if c["durum"] == "uyumsuz"]
    o = {
        "hesap_kontrol": len(kontrol), "hesap_gecti": len(kontrol) - len(hata), "hesap_hata": len(hata),
        "hesap_atlandi": len(tum) - len(kontrol), "indirilemeyen": indirilemeyen,
        "capraz_hisse": len(cap), "capraz_kontrol": len(cap_kontrol), "capraz_uyumlu": len(cap_kontrol) - len(cap_uyumsuz),
        "capraz_uyumsuz": len(cap_uyumsuz),
        "capraz_ulasilamadi": sum(1 for c in cap if c["durum"] not in ("uyumlu", "uyumsuz")),
        "metrik_hata_dagilimi": {},
    }
    for m in hata:
        o["metrik_hata_dagilimi"][m["metrik"]] = o["metrik_hata_dagilimi"].get(m["metrik"], 0) + 1
    sorun = len(hata) + len(cap_uyumsuz)
    if s["piyasa"] == "abd":
        o["korelasyon"] = korelasyon_yorumu(s)
        sorun += o["korelasyon"]["farkli"]
    o["sorun"] = sorun
    if len(kontrol) == 0 and len(cap_kontrol) == 0:
        o["karar"] = "Uyarı: doğrulama yapılamadı"
        o["karar_tur"] = "uyari"
    elif sorun == 0:
        o["karar"] = "Geçti"
        o["karar_tur"] = "gecti"
    else:
        o["karar"] = f"Uyarı: {sorun} sorun"
        o["karar_tur"] = "uyari"
    return o


def korelasyon_yorumu(s: dict) -> dict:
    hs = s["hisseler"]
    cift = [(h["korelasyon_rapor"], h["korelasyon_bagimsiz"], h.get("korelasyon_yahoo")) for h in hs
            if h.get("korelasyon_rapor") is not None and h.get("korelasyon_bagimsiz") is not None]
    o = {"n": len(cift), "farkli": 0, "medyan_rapor": None, "medyan_bagimsiz": None, "medyan_fark": None,
         "medyan_yahoo": None, "yorum": "", "neden": "", "tur": "notr"}
    if not cift:
        o["yorum"] = ("Bağımsız kaynaktan hisse ya da endeks fiyatı alınamadığı için "
                      "korelasyonlar bağımsız olarak kontrol edilemedi.")
        return o
    rap = np.array([c[0] for c in cift])
    bag = np.array([c[1] for c in cift])
    fark = bag - rap
    o["farkli"] = int((np.abs(fark) > KOR_BAGIMSIZ_TOL).sum())
    o["medyan_rapor"] = float(np.median(rap))
    o["medyan_bagimsiz"] = float(np.median(bag))
    o["medyan_fark"] = float(np.median(fark))
    yh = [(c[2], c[1], c[0]) for c in cift if c[2] is not None]
    if yh:
        o["medyan_yahoo"] = float(np.median([y[0] for y in yh]))
    n, f = len(cift), o["farkli"]
    if f == 0:
        o["tur"] = "iyi"
        o["yorum"] = (f"Şüphe doğrulanmadı: {n} hissenin hepsinde rapordaki korelasyon bağımsız kaynaktan "
                      f"hesaplananla uyumlu (fark ≤ {tr_sayi(KOR_BAGIMSIZ_TOL)}).")
    elif f / n >= 0.25 or abs(o["medyan_fark"]) > KOR_BAGIMSIZ_TOL:
        o["tur"] = "kotu"
        o["yorum"] = (f"Şüphe doğrulandı: {n} hisseden {f} tanesinde rapordaki korelasyon bağımsız kaynaktan "
                      f"belirgin farklı. Medyan: rapor {tr_sayi(o['medyan_rapor'])}, "
                      f"bağımsız {tr_sayi(o['medyan_bagimsiz'])}.")
    else:
        o["tur"] = "kotu"
        o["yorum"] = (f"Kısmen: {n} hisseden {f} tanesinde rapordaki korelasyon bağımsız kaynaktan belirgin farklı; "
                      f"çoğunluk uyumlu (medyan fark {tr_sayi(o['medyan_fark'])}).")
    yh_f = [y for y in yh if abs(y[1] - y[2]) > KOR_BAGIMSIZ_TOL]  # yalnızca farklı çıkan hisseler
    if f and yh_f:
        y_bag = np.median([abs(a - b) for a, b, _ in yh_f])
        y_rap = np.median([abs(a - c) for a, _, c in yh_f])
        if y_bag <= 0.05 and y_rap > 0.05:
            o["neden"] = ("Yahoo verisiyle bu betikte yeniden hesaplanan korelasyonlar bağımsız kaynakla uyumlu; "
                          "sorun büyük olasılıkla Yahoo verisinde değil, raporun hesaplama adımında "
                          "(ör. hisse ile endeks tarihlerinin hizalanması).")
        elif y_rap <= 0.05 and y_bag > 0.05:
            o["neden"] = ("Yahoo verisiyle yeniden hesaplanan korelasyonlar raporla aynı, bağımsız kaynaktan farklı; "
                          "sorun büyük olasılıkla Yahoo verisinin kendisinde.")
        else:
            o["neden"] = "Yahoo verisiyle yeniden hesaplanan değerler ne raporla ne bağımsız kaynakla tam uyumlu."
    return o


# ----------------------------------------------------------------------------
# Biçimlendirme ve HTML
# ----------------------------------------------------------------------------

AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim",
         "Kasım", "Aralık"]


def tr_sayi(x, ondalik=2) -> str:
    if not gecerli(x):
        return "–"
    s = f"{float(x):,.{ondalik}f}"
    if s.startswith("-") and float(s.replace(",", "")) == 0:
        s = s[1:]
    s = s.replace(",", "\u0001").replace(".", ",").replace("\u0001", ".")
    return s.replace("-", "−")


def tr_yuzde(x, ondalik=2) -> str:
    if not gecerli(x):
        return "–"
    v = float(x) * 100
    return ("−%" if v < 0 else "%") + tr_sayi(abs(v), ondalik)


def tr_tarih(s) -> str:
    if not s:
        return "–"
    t = pd.Timestamp(s)
    return f"{t.day} {AYLAR[t.month - 1]} {t.year}"


def deger_bicim(x, gorunum) -> str:
    if gorunum == "yuzde":
        return tr_yuzde(x)
    if gorunum == "sayi1":
        return tr_sayi(x, 1)
    if gorunum == "fiyat":
        return tr_sayi(x, 2 if (gecerli(x) and abs(x) >= 1) else 4)
    return tr_sayi(x, 2)


def fark_bicim(m) -> str:
    if m["fark"] is None:
        return "–"
    if m["tolerans_turu"] == "rel":
        return tr_yuzde(m["fark"]) + " gö."
    _, _, g, _, _ = METRIK_SOZ[m["metrik"]]
    if g == "yuzde":
        return tr_sayi(m["fark"] * 100, 2) + " puan"
    return tr_sayi(m["fark"], 2 if g != "sayi1" else 1)


def tol_bicim(m) -> str:
    _, _, g, tur, tol = METRIK_SOZ[m["metrik"]]
    if tur == "rel":
        return "≤ " + tr_yuzde(tol, 1) + " gö."
    if g == "yuzde":
        return "≤ " + tr_sayi(tol * 100, 1) + " puan"
    return "≤ " + tr_sayi(tol, 2)


def e(x) -> str:
    return html.escape(str(x), quote=True)


def kisa(sym: str) -> str:
    return sym[:-3] if sym.upper().endswith(".IS") else sym


DURUM_SINIF = {"geçti": "ok", "uyumlu": "ok", "hata": "bad", "uyumsuz": "bad", "farklı": "bad",
               "atlandı": "skip", "hesaplanamadı": "skip", "eşleşme yok": "warn", "ulaşılamadı": "warn",
               "ayrıştırılamadı": "warn", "Yahoo yok": "warn"}


def rozet(d) -> str:
    return f'<span class="chip {DURUM_SINIF.get(d, "skip")}">{e(d)}</span>'


CSS = """
:root{--bg:#f6f5f2;--card:#fff;--text:#1d1d1f;--muted:#62625f;--line:#e4e2dc;--accent:#2a5bd7;
--ok:#16723a;--ok-bg:#e3f3e8;--bad:#b3261e;--bad-bg:#fbe9e7;--warn:#8a5a00;--warn-bg:#fdf1d3;
--skip:#5f5f5c;--skip-bg:#ecebe7;--stripe:#faf9f6}
@media (prefers-color-scheme:dark){:root{--bg:#121213;--card:#1c1c1e;--text:#ececea;--muted:#a3a3a0;
--line:#303033;--accent:#8fb0ff;--ok:#6fd492;--ok-bg:#173823;--bad:#ff8a80;--bad-bg:#3d1c1a;
--warn:#f0c35a;--warn-bg:#3a2e12;--skip:#b0b0ad;--skip-bg:#2b2b2e;--stripe:#202022}}
*{box-sizing:border-box}
html{color-scheme:light dark;-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
main{max-width:760px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:1.45rem;line-height:1.25;margin:0 0 4px}
h2{font-size:1.12rem;margin:0 0 4px}
h3{font-size:.95rem;margin:18px 0 6px}
p{margin:0 0 10px}
.meta{color:var(--muted);font-size:.88rem;margin-bottom:14px}
.aciklama{color:var(--muted);font-size:.9rem;margin-bottom:12px}
.deneme{background:var(--warn-bg);color:var(--warn);border-radius:8px;padding:8px 12px;font-size:.88rem;margin-bottom:14px}
.karar{display:inline-block;font-weight:700;font-size:1.05rem;padding:6px 14px;border-radius:999px;margin:4px 0 16px}
.karar.gecti{background:var(--ok-bg);color:var(--ok)}
.karar.uyari{background:var(--bad-bg);color:var(--bad)}
.kartlar{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin-bottom:22px}
.kart{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.kart .bas{color:var(--muted);font-size:.8rem}
.kart .deger{font-size:1.35rem;font-weight:700;font-variant-numeric:tabular-nums}
.kart .alt{font-size:.82rem;color:var(--muted)}
section{margin-top:28px}
.not{border-left:4px solid var(--line);background:var(--card);border-radius:8px;padding:10px 12px;font-size:.92rem;margin:10px 0}
.not.kotu{border-left-color:var(--bad)}.not.iyi{border-left-color:var(--ok)}.not.notr{border-left-color:var(--warn)}
.not strong{display:block;margin-bottom:2px}
.tablo{overflow-x:auto;-webkit-overflow-scrolling:touch;background:var(--card);border:1px solid var(--line);border-radius:10px;margin:6px 0 10px}
table{border-collapse:separate;border-spacing:0;width:100%;font-size:.84rem;font-variant-numeric:tabular-nums}
th,td{padding:7px 9px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--line)}
th{font-weight:600;color:var(--muted);font-size:.78rem;background:var(--card)}
tr:last-child td{border-bottom:0}
th:first-child,td:first-child{text-align:left;position:sticky;left:0;background:var(--card);z-index:1;font-weight:600}
td.sol,th.sol{text-align:left}
td.sar{white-space:normal;min-width:10em;max-width:13em}
td.sar .aciklama{display:block;margin:2px 0 0;font-size:.75rem;line-height:1.3}
tbody tr:nth-child(even) td{background:var(--stripe)}
tr.kotu td:first-child{box-shadow:inset 3px 0 0 var(--bad)}
.chip{display:inline-block;font-size:.74rem;font-weight:600;padding:1px 8px;border-radius:999px}
.chip.ok{background:var(--ok-bg);color:var(--ok)}.chip.bad{background:var(--bad-bg);color:var(--bad)}
.chip.warn{background:var(--warn-bg);color:var(--warn)}.chip.skip{background:var(--skip-bg);color:var(--skip)}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:0 12px;margin:8px 0}
details[open]{padding-bottom:10px}
summary{cursor:pointer;padding:10px 0;font-weight:600;font-size:.92rem}
details .tablo{border-radius:8px}
ul.liste{margin:4px 0 10px;padding-left:18px;font-size:.86rem;color:var(--muted)}
ul.liste li{overflow-wrap:anywhere}
.bos{color:var(--ok);font-weight:600}
footer{margin-top:32px;color:var(--muted);font-size:.8rem;overflow-wrap:anywhere}
"""


def tablo(basliklar, satirlar, sol=(), sar=()) -> str:
    def sinif(i):
        return " ".join(([("sol")] if i in sol else []) + (["sar"] if i in sar else []))
    th = "".join(f'<th class="{sinif(i)}">{e(b)}</th>' for i, b in enumerate(basliklar))
    govde = []
    for sat in satirlar:
        tr_sinif, hucreler = (sat[0], sat[1]) if isinstance(sat, tuple) else ("", sat)
        td = "".join(f'<td class="{sinif(i)}">{h}</td>' for i, h in enumerate(hucreler))
        govde.append(f'<tr class="{tr_sinif}">{td}</tr>')
    return f'<div class="tablo"><table><thead><tr>{th}</tr></thead><tbody>{"".join(govde)}</tbody></table></div>'


def html_uret(s: dict) -> str:
    piyasa_ad = "Borsa İstanbul" if s["piyasa"] == "bist" else "ABD"
    o = s.get("ozet") or {}
    p = []
    p.append(f'<h1>Rapor doğrulaması · {e(piyasa_ad)}</h1>')
    p.append(f'<div class="meta">Rapor tarihi {e(tr_tarih(s.get("rapor_tarihi")))} · '
             f'{len(s.get("orneklem", []))} hisselik örneklem · oluşturma {e(s["olusturma"])}</div>')
    if s.get("deneme"):
        p.append('<div class="deneme">Deneme modu: tüm fiyatlar sentetik, bazı hatalar kasıtlı olarak eklendi.</div>')
    if s.get("hata"):
        p.append('<span class="karar uyari">Uyarı: doğrulama tamamlanamadı</span>')
        p.append(f'<div class="not kotu"><strong>Hata</strong>{e(s["hata"])}</div>')
        return sayfa(p, piyasa_ad)

    p.append(f'<div><span class="karar {e(o["karar_tur"])}">{e(o["karar"])}</span></div>')
    kay = s.get("kaynaklar", {})
    kay_metin = ", ".join(f'{k} {v["basari"]}/{v["deneme"]}' for k, v in kay.items()) or "–"
    kartlar = [
        ("Göstergeler", f'{o["hesap_gecti"]}/{o["hesap_kontrol"]}', "kontrol tolerans içinde"
         + (f' · {len(o["indirilemeyen"])} hisse indirilemedi' if o["indirilemeyen"] else "")),
        ("Fiyat karşılaştırması", f'{o["capraz_uyumlu"]}/{o["capraz_hisse"]}', "hisse bağımsız kaynakla uyumlu"
         + (f' · {o["capraz_ulasilamadi"]} kontrol edilemedi' if o["capraz_ulasilamadi"] else "")),
        ("Ulaşılan kaynaklar", "", "başarılı/denenen istek: " + kay_metin),
    ]
    p.append('<div class="kartlar">' + "".join(
        f'<div class="kart"><div class="bas">{e(a)}</div>'
        + (f'<div class="deger">{e(b)}</div>' if b else "") + f'<div class="alt">{e(c)}</div></div>'
        for a, b, c in kartlar) + "</div>")

    if s["piyasa"] == "abd" and o.get("korelasyon"):
        k = o["korelasyon"]
        p.append(f'<div class="not {e(k["tur"])}"><strong>S&amp;P 500 korelasyonu şüphesi</strong>{e(k["yorum"])}'
                 + (f' {e(k["neden"])}' if k.get("neden") else "") + ' <a href="#korelasyon">Ayrıntı</a></div>')

    # ---- Bölüm 1 ----
    p.append('<section id="gostergeler"><h2>Göstergeler yeniden hesaplandı</h2>')
    p.append('<p class="aciklama">Örneklemdeki hisselerin fiyatları Yahoo\'dan yeniden indirildi ve rapordaki '
             'göstergeler tanımlarından baştan hesaplandı; tolerans dışı farklar aşağıda.</p>')
    hatalar = [(h, m) for h in s["hisseler"] for m in h.get("metrikler", []) if m["durum"] == "hata"]
    if hatalar:
        p.append(tablo(["Hisse", "Gösterge", "Rapor", "Yeniden", "Fark", "Tolerans"],
                       [("kotu", [e(kisa(h["sembol"])), e(m["etiket"]), deger_bicim(m["rapor"], METRIK_SOZ[m["metrik"]][2]),
                                  deger_bicim(m["yeni"], METRIK_SOZ[m["metrik"]][2]), e(fark_bicim(m)), e(tol_bicim(m))])
                        for h, m in hatalar], sol=(1,), sar=(1,)))
    elif o["hesap_kontrol"] == 0:
        p.append('<div class="not notr"><strong>Kontrol yapılamadı</strong>Hiçbir gösterge yeniden '
                 'hesaplanamadı (Yahoo verisi alınamadı).</div>')
    else:
        p.append('<p class="bos">Hesaplanabilen tüm göstergeler tolerans içinde.</p>')
    if s.get("endeks_yahoo_hata"):
        p.append(f'<p class="aciklama">Endeks ({e(s["endeks"])}) Yahoo\'dan alınamadı: {e(s["endeks_yahoo_hata"])}; '
                 'korelasyon ve beta atlandı.</p>')
    ind = [h for h in s["hisseler"] if h.get("yahoo_hata")]
    if ind:
        p.append("<h3>İndirilemeyen hisseler</h3><ul class=\"liste\">" + "".join(
            f'<li>{e(kisa(h["sembol"]))}: {e(h["yahoo_hata"])}</li>' for h in ind) + "</ul>")
    uyarilar = [h for h in s["hisseler"] if h.get("uyari")]
    if uyarilar:
        p.append("<h3>Tarih uyarıları</h3><ul class=\"liste\">" + "".join(
            f'<li>{e(kisa(h["sembol"]))}: {e(h["uyari"])}</li>' for h in uyarilar) + "</ul>")
    kirpilan = sum(1 for h in s["hisseler"] if h.get("kirpildi"))
    if kirpilan:
        p.append(f'<p class="aciklama">{kirpilan} hissede Yahoo rapordan daha yeni gün içerdiği için veri rapor '
                 f'tarihine kırpıldı.</p>')
    if s.get("eksik_sutunlar"):
        p.append(f'<p class="aciklama">Raporda olmayan sütunlar (kontrol edilmedi): {e(", ".join(s["eksik_sutunlar"]))}</p>')
    tum = []
    for h in s["hisseler"]:
        for m in h.get("metrikler", []):
            g = METRIK_SOZ[m["metrik"]][2]
            tum.append(("kotu" if m["durum"] == "hata" else "",
                        [e(kisa(h["sembol"])), e(m["etiket"]), deger_bicim(m["rapor"], g), deger_bicim(m["yeni"], g),
                         e(fark_bicim(m)), rozet(m["durum"]) + (f'<span class="aciklama">{e(m["not"])}</span>' if m["not"] else "")]))
    p.append(f'<details><summary>Tüm kontroller ({len(tum)} satır)</summary>'
             + tablo(["Hisse", "Gösterge", "Rapor", "Yeniden", "Fark", "Durum"], tum, sol=(1, 5), sar=(1, 5)) + "</details>")
    p.append("</section>")

    # ---- Bölüm 2 ----
    kaynak_ad = "Stooq ya da Nasdaq" if s["piyasa"] == "abd" else "İş Yatırım"
    p.append('<section id="kaynak"><h2>Fiyatlar başka kaynakla karşılaştırıldı</h2>')
    p.append(f'<p class="aciklama">Yahoo\'nun düzeltilmemiş kapanışları son {CAPRAZ_GUN} işlem gününde {e(kaynak_ad)} '
             f'ile aynı tarihler üzerinden karşılaştırıldı. Son kapanış farkı %0,5\'i ya da %1\'in üzerindeki '
             f'gün sayısı 2\'yi aşarsa “uyumsuz”.</p>')
    sira = {"uyumsuz": 0, "ayrıştırılamadı": 1, "ulaşılamadı": 2, "eşleşme yok": 3, "Yahoo yok": 4, "uyumlu": 5}
    hs = sorted(s["hisseler"], key=lambda h: (sira.get(h["capraz"]["durum"], 9), h["sembol"]))
    sat = []
    for h in hs:
        c = h["capraz"]
        sat.append(("kotu" if c["durum"] == "uyumsuz" else "",
                    [e(kisa(h["sembol"])), rozet(c["durum"]), tr_yuzde(c.get("son_fark")), tr_yuzde(c.get("maks_fark")),
                     e(c["gun_1yuzde"]) if c.get("gun_1yuzde") is not None else "–",
                     e(c["eslesen_gun"]) if c.get("eslesen_gun") is not None else "–",
                     tr_sayi(c.get("getiri_kor"), 3), e(c.get("kaynak") or "–")]))
    p.append(tablo(["Hisse", "Durum", "Son fark", "Maks. fark", "%1+ gün", "Eşleşen gün", "Getiri kor.", "Kaynak"],
                   sat, sol=(1, 7)))
    notlar = [h for h in hs if h["capraz"].get("not")]
    if notlar:
        p.append("<h3>Kaynak notları</h3><ul class=\"liste\">" + "".join(
            f'<li>{e(kisa(h["sembol"]))}: {e(h["capraz"]["not"])}</li>' for h in notlar) + "</ul>")
    alanlar = {tuple(sorted(h["capraz"]["alan"].items())) for h in hs if h["capraz"].get("alan") and
               "kapanis_alani" in h["capraz"]["alan"]}
    if alanlar:
        p.append('<p class="aciklama">Kullanılan İş Yatırım alanları: ' + e("; ".join(
            ", ".join(f"{k}={v}" for k, v in a) for a in alanlar)) + "</p>")
    p.append("</section>")

    # ---- Bölüm 3 ----
    if s["piyasa"] == "abd":
        k = o.get("korelasyon", {})
        eb = s.get("endeks_bagimsiz") or {}
        p.append('<section id="korelasyon"><h2>S&amp;P 500 ile korelasyon (bağımsız kaynak)</h2>')
        p.append('<p class="aciklama">Her hissenin son 60 günlük getirilerinin S&amp;P 500 ile korelasyonu, '
                 'Yahoo dışı bir kaynaktaki hisse ve endeks fiyatlarıyla yeniden hesaplandı. Büyük ABD hisseleri '
                 'için tipik değer 0,3–0,8 aralığıdır.</p>')
        p.append(f'<div class="not {e(k.get("tur", "notr"))}"><strong>Sonuç</strong>{e(k.get("yorum", ""))}'
                 + (f' {e(k["neden"])}' if k.get("neden") else "") + "</div>")
        endeks_metin = (f'Endeks kaynağı: {eb.get("kaynak")} ({eb.get("satir")} gün)' if eb.get("kaynak")
                        else f'Endeks bağımsız kaynaktan alınamadı: {eb.get("hata")}')
        p.append(f'<p class="aciklama">{e(endeks_metin)}. Medyan: rapor {e(tr_sayi(k.get("medyan_rapor")))}, '
                 f'Yahoo ile yeniden {e(tr_sayi(k.get("medyan_yahoo")))}, bağımsız {e(tr_sayi(k.get("medyan_bagimsiz")))}. '
                 f'“Farklı”: |bağımsız − rapor| &gt; {e(tr_sayi(KOR_BAGIMSIZ_TOL))}.</p>')
        sira2 = {"farklı": 0, "uyumlu": 1, "hesaplanamadı": 2}
        hs2 = sorted(s["hisseler"], key=lambda h: (sira2.get(h.get("korelasyon_bag_durum"), 9),
                                                   -(h.get("ort_islem") or 0)))
        sat = [("kotu" if h.get("korelasyon_bag_durum") == "farklı" else "",
                [e(kisa(h["sembol"])), tr_sayi(h.get("korelasyon_rapor")), tr_sayi(h.get("korelasyon_bagimsiz")),
                 tr_sayi(h.get("korelasyon_bag_fark")), tr_sayi(h.get("korelasyon_yahoo")),
                 rozet(h.get("korelasyon_bag_durum", "hesaplanamadı"))]) for h in hs2]
        p.append(tablo(["Hisse", "Rapor", "Bağımsız", "Fark", "Yahoo (yeniden)", "Durum"], sat, sol=(5,)))
        p.append("</section>")

    p.append(f'<footer>Örneklem: işlem hacmine göre ilk 25 + rastgele {max(0, len(s.get("orneklem", [])) - 25)} '
             f'(tohum {e(s.get("tohum"))}). Kaynak dosya: {e(s.get("csv"))}</footer>')
    return sayfa(p, piyasa_ad)


def sayfa(parcalar, piyasa_ad) -> str:
    return ("<!doctype html><html lang=\"tr\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>Rapor doğrulaması · {e(piyasa_ad)}</title><style>{CSS}</style></head>"
            f"<body><main>{''.join(parcalar)}</main></body></html>")


def json_temizle(x):
    if isinstance(x, dict):
        return {str(k): json_temizle(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [json_temizle(v) for v in x]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return float(x) if math.isfinite(float(x)) else None
    if isinstance(x, (pd.Timestamp, dt.date)):
        return x.isoformat()
    if isinstance(x, np.bool_):
        return bool(x)
    return x


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Günlük hisse raporunu bağımsız olarak doğrular.")
    ap.add_argument("--piyasa", required=True, choices=["bist", "abd"])
    ap.add_argument("--csv", required=True, help="raporun tum.csv dosyası")
    ap.add_argument("--cikti", required=True, help="HTML çıktı yolu")
    ap.add_argument("--json", default=None, help="JSON çıktı yolu (varsayılan: HTML ile aynı, .json)")
    ap.add_argument("--orneklem", type=int, default=40)
    ap.add_argument("--deneme", action="store_true", help="çevrimdışı sentetik veriyle deneme")
    args = ap.parse_args(argv)
    if args.orneklem < 1:
        ap.error("--orneklem en az 1 olmalı")
    if not args.deneme and not os.path.exists(args.csv):
        ap.error(f"CSV bulunamadı: {args.csv}")
    json_yol = args.json or (os.path.splitext(args.cikti)[0] + ".json")

    try:
        sonuc = dogrula(args)
    except Exception as ex:  # noqa: BLE001
        log(traceback.format_exc())
        sonuc = {"piyasa": args.piyasa, "deneme": bool(args.deneme), "olusturma": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                 "hata": f"{type(ex).__name__}: {ex}", "hisseler": [], "orneklem": [], "rapor_tarihi": None,
                 "ozet": {"karar": "Uyarı: doğrulama tamamlanamadı", "karar_tur": "uyari"}}
    for yol in (args.cikti, json_yol):
        try:
            os.makedirs(os.path.dirname(os.path.abspath(yol)), exist_ok=True)
        except Exception:  # noqa: BLE001
            pass
    try:
        sayfa_html = html_uret(sonuc)
    except Exception as ex:  # noqa: BLE001  - rapor üretimi bozulsa bile bir sayfa yaz
        log(traceback.format_exc())
        sayfa_html = sayfa(['<h1>Rapor doğrulaması</h1><span class="karar uyari">Uyarı: sayfa üretilemedi</span>',
                            f'<div class="not kotu"><strong>Hata</strong>{e(type(ex).__name__)}: {e(ex)}. '
                            'Ayrıntılar JSON çıktısında.</div>'], args.piyasa.upper())
    try:
        with open(args.cikti, "w", encoding="utf-8") as f:
            f.write(sayfa_html)
    except Exception:  # noqa: BLE001
        log(traceback.format_exc())
    try:
        with open(json_yol, "w", encoding="utf-8") as f:
            json.dump(json_temizle(sonuc), f, ensure_ascii=False, indent=1)
    except Exception:  # noqa: BLE001
        log(traceback.format_exc())
    oz = sonuc.get("ozet", {})
    log(f"Karar: {oz.get('karar')}  ->  {args.cikti} , {json_yol}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
