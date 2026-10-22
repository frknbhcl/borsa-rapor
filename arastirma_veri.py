#!/usr/bin/env python3
"""
ABD hisseleri için ek veri toplayıcı (araştırma ve yeni modeller için).

Kaynaklar (hepsi ücretsiz, anahtar gerektirmez):
- sec:    SEC EDGAR XBRL "frames" API ile geçmiş bilanço/gelir tablosu kalemleri (bütün şirketler, dönem
          dönem) ve 8-K "Item 2.02" (bilanço açıklaması) tarihleri.
- finra:  FINRA günlük açığa satış hacmi dosyaları (Reg SHO, CNMS konsolide).
- yahoo:  Analist not değişiklikleri (upgrade/downgrade, hedef fiyat) ve kazanç sürprizleri.
- nasdaq: Sektör ve endüstri sınıflaması.

Kullanım:
    python arastirma_veri.py sec finra yahoo nasdaq     # çıktı: arastirma_veri/
"""

from __future__ import annotations

import concurrent.futures as cf
import gzip
import io
import json
import os
import pickle
import re
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

KLASOR = Path(__file__).resolve().parent
CIKTI = KLASOR / "arastirma_veri"
EVREN = KLASOR / "arastirma" / "evren_abd.txt"
BASLANGIC = date(2018, 1, 1)
# SEC, istekte bir iletişim adresi bulunan tanımlayıcı User-Agent ister. Kişisel adres kullanmıyoruz:
# GitHub Actions botunun herkese açık noreply adresi.
SEC_UA = os.environ.get("SEC_USER_AGENT",
                        "borsa-rapor research 41898282+github-actions[bot]@users.noreply.github.com")
TARAYICI_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
OZET: list[str] = []


def yaz(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    OZET.append(f"{datetime.utcnow():%H:%M:%S} {s}")


def kaydet(ad: str, nesne) -> None:
    CIKTI.mkdir(exist_ok=True)
    with gzip.open(CIKTI / f"{ad}.pkl.gz", "wb") as f:
        pickle.dump(nesne, f, protocol=pickle.HIGHEST_PROTOCOL)
    yaz(f"  kaydedildi: {ad}.pkl.gz ({(CIKTI / f'{ad}.pkl.gz').stat().st_size / 1e6:.1f} MB)")


class Hiz:
    """Saniyede en fazla n istek."""

    def __init__(self, n: float):
        self.ara, self.son, self.kilit = 1.0 / n, 0.0, threading.Lock()

    def bekle(self):
        with self.kilit:
            simdi = time.monotonic()
            if simdi < self.son + self.ara:
                time.sleep(self.son + self.ara - simdi)
            self.son = time.monotonic()


def getir(oturum: requests.Session, url: str, hiz: Hiz | None = None, deneme: int = 4, **kw):
    for i in range(deneme):
        if hiz:
            hiz.bekle()
        try:
            r = oturum.get(url, timeout=60, **kw)
            if r.status_code == 200:
                return r
            if r.status_code == 404:
                return None
            if r.status_code in (403, 429, 500, 502, 503):
                time.sleep(2 * (i + 1) ** 2)
                continue
            return None
        except requests.RequestException:
            time.sleep(2 * (i + 1))
    return None


# ----------------------------------------------------------------------------- SEC
SEC_ANLIK = [  # (taksonomi, kavram, birim)
    ("us-gaap", "Assets", "USD"),
    ("us-gaap", "StockholdersEquity", "USD"),
    ("us-gaap", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "USD"),
    ("us-gaap", "Liabilities", "USD"),
    ("us-gaap", "CashAndCashEquivalentsAtCarryingValue", "USD"),
    ("dei", "EntityCommonStockSharesOutstanding", "shares"),
    ("us-gaap", "CommonStockSharesOutstanding", "shares"),
]
SEC_SURE = [
    ("us-gaap", "NetIncomeLoss", "USD"),
    ("us-gaap", "Revenues", "USD"),
    ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax", "USD"),
    ("us-gaap", "GrossProfit", "USD"),
    ("us-gaap", "CostOfRevenue", "USD"),
    ("us-gaap", "OperatingIncomeLoss", "USD"),
    ("us-gaap", "NetCashProvidedByUsedInOperatingActivities", "USD"),
    ("us-gaap", "EarningsPerShareDiluted", "USD-per-shares"),
    ("us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding", "shares"),
]


def sec() -> None:
    yaz("SEC: şirket listesi")
    s = requests.Session()
    s.headers.update({"User-Agent": SEC_UA, "Accept-Encoding": "gzip, deflate"})
    hiz = Hiz(6)
    r = None
    for ua in [SEC_UA, "borsa-rapor frknbhcl@users.noreply.github.com",
               "borsa-rapor research noreply@github.com", "Mozilla/5.0 (compatible; borsa-rapor; +https://github.com/frknbhcl/borsa-rapor)"]:
        s.headers["User-Agent"] = ua
        try:
            for u in ("https://data.sec.gov/api/xbrl/frames/us-gaap/Assets/USD/CY2023Q4I.json",
                      "https://data.sec.gov/submissions/CIK0000320193.json"):
                r0 = s.get(u, timeout=60)
                yaz(f"  [{ua}] {u.split('/')[-1]} -> {r0.status_code} {len(r0.content)}B")
            rr = s.get("https://www.sec.gov/files/company_tickers.json", timeout=60)
            baslik = re.search(r"<title>(.*?)</title>", rr.text, re.S)
            govde = re.sub(r"<[^>]+>", " ", rr.text)
            yaz(f"  UA denemesi [{ua}] -> {rr.status_code} {baslik.group(1) if baslik else ''} | {' '.join(govde.split())[:300]}")
            if rr.status_code == 200:
                r = rr
                break
        except requests.RequestException as e:
            yaz(f"  UA denemesi [{ua}] -> {e!r}")
        time.sleep(2)
    if r is None:
        yaz("  HATA: company_tickers.json alınamadı")
        return
    sirketler = pd.DataFrame(r.json().values())  # cik_str, ticker, title
    yaz(f"  {len(sirketler)} şirket")

    donemler_anlik, donemler_sure = [], []
    for y in range(BASLANGIC.year, date.today().year + 1):
        for q in range(1, 5):
            if date(y, 3 * q, 1) > date.today():
                break
            donemler_anlik.append(f"CY{y}Q{q}I")
            donemler_sure.append(f"CY{y}Q{q}")
        if y < date.today().year:
            donemler_sure.append(f"CY{y}")
    isler = ([(t, k, b, d) for (t, k, b) in SEC_ANLIK for d in donemler_anlik]
             + [(t, k, b, d) for (t, k, b) in SEC_SURE for d in donemler_sure])
    yaz(f"SEC frames: {len(isler)} istek")
    frames, eksik = {}, 0

    def tek(is_):
        t, k, b, d = is_
        rr = getir(s, f"https://data.sec.gov/api/xbrl/frames/{t}/{k}/{b}/{d}.json", hiz)
        if rr is None:
            return is_, None
        df = pd.DataFrame(rr.json().get("data", []))
        return is_, df[[c for c in ("cik", "start", "end", "val", "accn") if c in df.columns]]

    with cf.ThreadPoolExecutor(4) as ex:
        for i, (is_, df) in enumerate(ex.map(tek, isler)):
            if df is None:
                eksik += 1
            else:
                frames[is_] = df
            if (i + 1) % 100 == 0:
                yaz(f"  {i + 1}/{len(isler)} (eksik {eksik})")
    yaz(f"  frames tamam: {len(frames)} dolu, {eksik} eksik")
    kaydet("sec_frames", {"sirketler": sirketler, "frames": frames})

    # 8-K Item 2.02 (Results of Operations) tarihleri = bilanço açıklama günleri
    evren = set(EVREN.read_text().split()) if EVREN.exists() else set()
    harita = {str(t).upper().replace(".", "-"): int(c) for t, c in zip(sirketler["ticker"], sirketler["cik_str"])}
    hedef = sorted({harita[t] for t in evren if t in harita})
    yaz(f"SEC 8-K tarihleri: {len(hedef)} şirket")
    sinir = BASLANGIC.isoformat()

    def basvurular(cik: int):
        out = []
        rr = getir(s, f"https://data.sec.gov/submissions/CIK{cik:010d}.json", hiz)
        if rr is None:
            return cik, None
        j = rr.json()
        sayfalar = [j["filings"]["recent"]]
        for ek in j["filings"].get("files", []):
            if ek.get("filingTo", "9999") >= sinir:
                r2 = getir(s, f"https://data.sec.gov/submissions/{ek['name']}", hiz)
                if r2 is not None:
                    sayfalar.append(r2.json())
        for sf in sayfalar:
            for form, tarih, kabul, items in zip(sf.get("form", []), sf.get("filingDate", []),
                                                 sf.get("acceptanceDateTime", [""] * len(sf.get("form", []))),
                                                 sf.get("items", [""] * len(sf.get("form", [])))):
                if form in ("8-K", "8-K/A") and "2.02" in (items or "") and tarih >= sinir:
                    out.append((tarih, kabul))
                elif form in ("10-Q", "10-K", "10-Q/A", "10-K/A") and tarih >= sinir:
                    out.append((tarih, "F:" + form))
        return cik, out

    sonuc, hata = {}, 0
    with cf.ThreadPoolExecutor(4) as ex:
        for i, (cik, out) in enumerate(ex.map(basvurular, hedef)):
            if out is None:
                hata += 1
            else:
                sonuc[cik] = out
            if (i + 1) % 200 == 0:
                yaz(f"  {i + 1}/{len(hedef)} (hata {hata})")
    yaz(f"  8-K tamam: {len(sonuc)} şirket, {hata} hata")
    kaydet("sec_8k", sonuc)


# ----------------------------------------------------------------------------- FINRA
def finra() -> None:
    s = requests.Session()
    s.headers.update({"User-Agent": TARAYICI_UA})
    gunler = pd.bdate_range(date(2020, 9, 1), date.today())
    yaz(f"FINRA: {len(gunler)} gün")
    kisa, toplam, bos = {}, {}, 0

    def tek(g):
        r = getir(s, f"https://cdn.finra.org/equity/regsho/daily/CNMSshvol{g:%Y%m%d}.txt", deneme=3)
        if r is None:
            return g, None
        df = pd.read_csv(io.StringIO(r.text), sep="|", usecols=["Symbol", "ShortVolume", "TotalVolume"],
                         dtype={"Symbol": str})
        df = df[df["Symbol"].notna()]
        df["Symbol"] = df["Symbol"].str.replace(".", "-", regex=False)
        df = df.groupby("Symbol")[["ShortVolume", "TotalVolume"]].sum()
        return g, df

    with cf.ThreadPoolExecutor(8) as ex:
        for i, (g, df) in enumerate(ex.map(tek, gunler)):
            if df is None:
                bos += 1
                continue
            kisa[g] = df["ShortVolume"].astype("float32")
            toplam[g] = df["TotalVolume"].astype("float32")
            if (i + 1) % 250 == 0:
                yaz(f"  {i + 1}/{len(gunler)} (boş {bos})")
    k = pd.DataFrame(kisa).T.sort_index()
    t = pd.DataFrame(toplam).T.sort_index()
    yaz(f"  FINRA tamam: {k.shape[0]} gün, {k.shape[1]} sembol, {bos} boş gün")
    kaydet("finra_aciga_satis", {"kisa": k, "toplam": t})


# ----------------------------------------------------------------------------- Yahoo
def yahoo() -> None:
    import yfinance as yf

    semboller = EVREN.read_text().split()
    yaz(f"Yahoo analist + kazanç: {len(semboller)} sembol")
    hiz = Hiz(3)

    def tek(sym):
        out = {"analist": None, "kazanc": None, "hata": []}
        t = yf.Ticker(sym)
        for deneme in range(3):
            try:
                hiz.bekle()
                q = t._quote._fetch(modules=["upgradeDowngradeHistory", "earningsHistory", "defaultKeyStatistics"])
                r = (q or {}).get("quoteSummary", {}).get("result") or []
                if r:
                    out["analist"] = r[0].get("upgradeDowngradeHistory", {}).get("history")
                    out["kazanc_son4"] = r[0].get("earningsHistory", {}).get("history")
                    out["istatistik"] = r[0].get("defaultKeyStatistics")
                break
            except Exception as e:  # noqa: BLE001
                out["hata"].append(f"q:{type(e).__name__}:{str(e)[:80]}")
                time.sleep(3 * (deneme + 1))
        for deneme in range(2):
            try:
                hiz.bekle()
                df = t.get_earnings_dates(limit=36)
                if df is not None and len(df):
                    out["kazanc"] = df.reset_index()
                break
            except Exception as e:  # noqa: BLE001
                out["hata"].append(f"e:{type(e).__name__}:{str(e)[:80]}")
                time.sleep(3 * (deneme + 1))
        return sym, out

    sonuc = {}
    with cf.ThreadPoolExecutor(3) as ex:
        for i, (sym, out) in enumerate(ex.map(tek, semboller)):
            sonuc[sym] = out
            if (i + 1) % 200 == 0:
                a = sum(1 for v in sonuc.values() if v["analist"])
                k = sum(1 for v in sonuc.values() if v["kazanc"] is not None)
                yaz(f"  {i + 1}/{len(semboller)}: analist {a}, kazanç {k}")
    a = sum(1 for v in sonuc.values() if v["analist"])
    k = sum(1 for v in sonuc.values() if v["kazanc"] is not None)
    ornek_hata = [h for v in sonuc.values() for h in v["hata"]][:10]
    yaz(f"  Yahoo tamam: analist {a}/{len(semboller)}, kazanç {k}/{len(semboller)}; örnek hatalar: {ornek_hata}")
    kaydet("yahoo_analist", sonuc)


# ----------------------------------------------------------------------------- Nasdaq
def nasdaq() -> None:
    s = requests.Session()
    s.headers.update({"User-Agent": TARAYICI_UA, "Accept": "application/json, text/plain, */*",
                      "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"})
    r = getir(s, "https://api.nasdaq.com/api/screener/stocks?tableonly=true&download=true")
    if r is None:
        yaz("Nasdaq: HATA, sektör listesi alınamadı")
        return
    rows = r.json()["data"]["rows"]
    df = pd.DataFrame(rows)
    df["symbol"] = df["symbol"].str.strip().str.replace("/", "-", regex=False).str.replace("^", "-P", regex=False)
    yaz(f"Nasdaq: {len(df)} hisse, sektör dolu {(df['sector'].fillna('') != '').sum()}")
    kaydet("nasdaq_sektor", df[["symbol", "name", "sector", "industry", "marketCap", "country", "ipoyear"]])


def main() -> None:
    adimlar = sys.argv[1:] or ["sec", "finra", "yahoo", "nasdaq"]
    CIKTI.mkdir(exist_ok=True)
    for a in adimlar:
        bas = time.time()
        try:
            {"sec": sec, "finra": finra, "yahoo": yahoo, "nasdaq": nasdaq}[a]()
        except Exception as e:  # noqa: BLE001
            import traceback
            yaz(f"{a}: BEKLENMEYEN HATA {e!r}\n{traceback.format_exc()[-1500:]}")
        yaz(f"{a}: {time.time() - bas:.0f} sn")
    (CIKTI / f"ozet_{'_'.join(adimlar)}.txt").write_text("\n".join(OZET), encoding="utf-8")


if __name__ == "__main__":
    main()
