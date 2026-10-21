#!/usr/bin/env python3
"""
Günlük Borsa Raporu: Borsa İstanbul ve ABD borsaları için ayrı ayrı.

Kullanım (sanal ortam aktifken, bu klasörde):
    python gunluk_rapor.py            # BIST + ABD
    python gunluk_rapor.py bist       # sadece BIST
    python gunluk_rapor.py abd        # sadece ABD
    python gunluk_rapor.py --tam      # önbelleği yok say, her şeyi baştan indir
    python gunluk_rapor.py --acma     # raporu tarayıcıda açma (zamanlanmış çalıştırma için)

Veri kaynağı: Yahoo Finance. OpenBB'nin yfinance sağlayıcısı da arka planda aynı
kaynağı kullanır; burada binlerce hisseyi hızlı çekebilmek için doğrudan toplu
indirme yapılıyor. Fiyatlar bölünme/bedelsiz ve temettüye göre düzeltilmiştir.

İlk çalıştırma tüm hisselerin 1 yıllık geçmişini indirir (ABD için 10-20 dk
sürebilir). Sonraki çalıştırmalar yalnızca son günleri indirir ve hızlıdır.

Bu rapor yatırım tavsiyesi değildir; veriler hatalı olabilir.
"""

from __future__ import annotations

import argparse
import html
import io
import logging
import re
import subprocess
import sys
import time
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

try:
    import yfinance as yf
    from yfinance import EquityQuery
except ImportError:  # pragma: no cover
    sys.exit("yfinance bulunamadı. Önce şunu çalıştır: pip install openbb-yfinance")

# ---------------------------------------------------------------------------
# AYARLAR: İstediğin gibi değiştirebilirsin
# ---------------------------------------------------------------------------

KLASOR = Path(__file__).resolve().parent
VERI = KLASOR / "veri"          # önbellek (fiyat geçmişi, hisse listeleri)
RAPORLAR = KLASOR / "raporlar"  # günlük HTML ve CSV raporları

PIYASALAR = {
    "bist": {
        "ad": "Borsa İstanbul",
        "para": "₺",
        "borsalar": ["IST"],
        "endeks": ("BIST 100", "XU100.IS"),
        "ekstra": {"BIST 30": "XU030.IS", "USD/TRY": "USDTRY=X", "EUR/TRY": "EURTRY=X"},
        "saat_dilimi": "Europe/Istanbul",
        "kapanis": (18, 10),
        "sembol_kalibi": r"^[A-Z0-9]+\.IS$",
        # Listelerde (en çok yükselenler vb.) gösterilecek hisseler için alt sınırlar:
        "min_islem_hacmi": 10_000_000,   # son 20 gün ortalama günlük işlem hacmi (₺)
        "min_fiyat": 0,
        # Bu orandan büyük günlük hareket şüpheli sayılır (BIST günlük limiti ±%10):
        "supheli_hareket": 0.11,
        "supheli_mesaj": "BIST günlük limiti ±%10; bölünme/bedelsiz düzeltmesi eksik olabilir",
        "min_hisse": 300,
    },
    "abd": {
        "ad": "ABD Borsaları",
        "para": "$",
        # NMS/NGM/NCM: Nasdaq, NYQ: NYSE, ASE: NYSE American
        "borsalar": ["NMS", "NGM", "NCM", "NYQ", "ASE"],
        "endeks": ("S&P 500", "^GSPC"),
        "ekstra": {"Nasdaq": "^IXIC", "Dow Jones": "^DJI", "VIX": "^VIX"},
        "saat_dilimi": "America/New_York",
        "kapanis": (16, 0),
        "sembol_kalibi": r"^[A-Z]{1,5}(-[A-Z])?$",
        "min_islem_hacmi": 5_000_000,    # $
        "min_fiyat": 2,                  # $; kuruşluk hisseleri listelerden çıkarır
        "supheli_hareket": 0.50,
        "supheli_mesaj": "aşırı hareket; bölünme ya da haber olup olmadığını kontrol et",
        "min_hisse": 2000,
    },
}

PARCA = 40             # tek seferde indirilen hisse sayısı
PARALEL = 4            # aynı anda kaç istek (Yahoo'yu yormamak için düşük tutuldu)
LISTE_YENILEME_GUN = 7 # hisse listesi kaç günde bir yenilensin
GECMIS = "1y"          # tam indirmede geçmiş uzunluğu
LISTE_UZUNLUK = 15     # her listede gösterilecek hisse sayısı
HACIM_PATLAMA = 3.0    # bugünkü hacim / 20 günlük ortalama
ALANLAR = ["Open", "High", "Low", "Close", "Volume"]
ISTISNA_ADLAR = re.compile(r"\b(?:warrants?|rights?|units?|preferred|notes? due|debentures?)\b", re.I)



class _HataToplayici(logging.Handler):
    """yfinance hata mesajlarını ekrana basmak yerine toplar (neden indirilemediğini görmek için)."""

    def __init__(self):
        super().__init__(logging.ERROR)
        self.mesajlar: list[str] = []

    def emit(self, record):
        try:
            mesaj = record.getMessage().strip()
            if mesaj and "Failed download" not in mesaj:
                self.mesajlar.append(mesaj)
        except Exception:  # noqa: BLE001
            pass


HATALAR = _HataToplayici()
_yf_log = logging.getLogger("yfinance")
_yf_log.setLevel(logging.ERROR)
_yf_log.addHandler(HATALAR)
_yf_log.propagate = False
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)


def yaz(metin: str = "", son: str = "\n") -> None:
    print(metin, end=son, flush=True)


# ---------------------------------------------------------------------------
# 1) Hisse listesi
# ---------------------------------------------------------------------------

def yahoo_tarama(borsalar: list[str]) -> pd.DataFrame:
    """Yahoo hisse tarayıcısından bir borsadaki tüm hisseleri çeker."""
    if len(borsalar) == 1:
        sorgu = EquityQuery("eq", ["exchange", borsalar[0]])
    else:
        sorgu = EquityQuery("is-in", ["exchange", *borsalar])
    satirlar, offset, toplam = [], 0, None
    for _ in range(80):  # en fazla 80 sayfa x 250
        cevap = yf.screen(sorgu, offset=offset, size=250, count=250,
                          sortField="ticker", sortAsc=True)
        kayitlar = cevap.get("quotes", []) or []
        toplam = cevap.get("total", toplam) or 0
        if not kayitlar:
            break
        for k in kayitlar:
            satirlar.append({
                "sembol": k.get("symbol"),
                "ad": k.get("longName") or k.get("shortName") or "",
                "piyasa_degeri": k.get("marketCap"),
            })
        offset += len(kayitlar)
        if offset >= toplam:
            break
        time.sleep(0.4)
    return pd.DataFrame(satirlar)


def nasdaq_listesi() -> pd.DataFrame:
    """Yedek: Nasdaq'ın resmi sembol dizini (NASDAQ + NYSE + diğerleri)."""
    import requests

    kaynaklar = {
        "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt": "Symbol",
        "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt": "ACT Symbol",
    }
    parcalar = []
    for url, kolon in kaynaklar.items():
        metin = requests.get(url, timeout=30).text
        df = pd.read_csv(io.StringIO(metin), sep="|", dtype=str)
        df = df[df[kolon].notna() & ~df[kolon].str.startswith("File Creation")]
        df = df[(df["ETF"] == "N") & (df["Test Issue"] == "N")]
        parcalar.append(pd.DataFrame({
            "sembol": df[kolon].str.replace(".", "-", regex=False),
            "ad": df["Security Name"].fillna(""),
            "piyasa_degeri": np.nan,
        }))
    return pd.concat(parcalar, ignore_index=True)


def hisse_listesi(kod: str, ayar: dict, tam: bool) -> pd.DataFrame:
    yol = VERI / f"hisseler_{kod}.csv"
    if yol.exists() and not tam:
        yas = datetime.now() - datetime.fromtimestamp(yol.stat().st_mtime)
        if yas < timedelta(days=LISTE_YENILEME_GUN):
            return pd.read_csv(yol, dtype={"sembol": str, "ad": str})

    yaz(f"  Hisse listesi yenileniyor ({ayar['ad']})...")
    liste = None
    try:
        liste = yahoo_tarama(ayar["borsalar"])
        if len(liste) < ayar["min_hisse"]:
            yaz(f"  Uyarı: Yahoo tarayıcısı yalnızca {len(liste)} hisse döndürdü.")
            liste = None
    except Exception as hata:  # noqa: BLE001
        yaz(f"  Uyarı: Yahoo tarayıcısına ulaşılamadı ({hata}).")

    if liste is None and kod == "abd":
        try:
            yaz("  Nasdaq sembol dizinine geçiliyor...")
            liste = nasdaq_listesi()
        except Exception as hata:  # noqa: BLE001
            yaz(f"  Uyarı: Nasdaq dizinine de ulaşılamadı ({hata}).")

    if liste is None:
        if yol.exists():
            yaz("  Önceki hisse listesi kullanılıyor.")
            return pd.read_csv(yol, dtype={"sembol": str, "ad": str})
        sys.exit(f"Hisse listesi alınamadı ve önbellekte yok: {yol}\n"
                 "İnternet bağlantını kontrol edip tekrar dene.")

    liste = liste.dropna(subset=["sembol"])
    liste["sembol"] = liste["sembol"].str.strip().str.upper()
    liste = liste[liste["sembol"].str.match(ayar["sembol_kalibi"])]
    liste = liste[~liste["ad"].fillna("").str.contains(ISTISNA_ADLAR)]
    liste = liste.drop_duplicates("sembol").sort_values("sembol").reset_index(drop=True)
    VERI.mkdir(parents=True, exist_ok=True)
    liste.to_csv(yol, index=False)
    return liste


# ---------------------------------------------------------------------------
# 2) Fiyat indirme (önbellekli)
# ---------------------------------------------------------------------------

def _dosya_sinirini_artir() -> None:
    """macOS bir programın aynı anda yalnızca 256 dosya açmasına izin verir; binlerce
    hisse indirirken bu yetmeyebilir. Sınırı (yalnızca bu program için) yükseltir."""
    try:
        import resource
        yumusak, sert = resource.getrlimit(resource.RLIMIT_NOFILE)
        hedef = 4096 if sert == resource.RLIM_INFINITY else min(4096, sert)
        if yumusak < hedef:
            resource.setrlimit(resource.RLIMIT_NOFILE, (hedef, sert))
    except Exception:  # noqa: BLE001
        pass


_HAVUZ = None


def havuz():
    """Sabit sayıda kalıcı işçi. yfinance'in kendi toplu indirmesi her hisse için yeni bir
    iş parçacığı açıyor ve her biri yerel önbellek veritabanına kapanmayan yeni bir
    bağlantı bırakıyor; birkaç yüz hisseden sonra "Too many open files" hatası çıkıyor.
    Kalıcı işçilerde her işçi tek bağlantı kullanır, sızıntı olmaz."""
    global _HAVUZ
    if _HAVUZ is None:
        from concurrent.futures import ThreadPoolExecutor
        _HAVUZ = ThreadPoolExecutor(max_workers=PARALEL, thread_name_prefix="indir")
    return _HAVUZ


def _tek_indir(sembol: str, kw: dict):
    try:
        t = yf.Ticker(sembol)
        df = t.history(auto_adjust=True, actions=False, raise_errors=False, **kw)
        ph = getattr(t, "_price_history", None)
        hata = getattr(ph, "_last_error", None) if ph is not None else None
        return sembol, df, hata
    except Exception as hata:  # noqa: BLE001
        return sembol, None, repr(hata)


def _indir(semboller: list[str], **kw) -> dict[str, pd.DataFrame]:
    """Sembolleri kalıcı işçi havuzuyla tek tek indirir; alan başına geniş tablo döndürür."""
    alanlar: dict[str, dict[str, pd.Series]] = {a: {} for a in ALANLAR}
    for sembol, df, hata in havuz().map(lambda x: _tek_indir(x, kw), semboller):
        if df is None or df.empty or "Close" not in df.columns or df["Close"].notna().sum() == 0:
            HATALAR.mesajlar.append(f"{sembol}: {hata or 'veri yok'}")
            continue
        idx = df.index
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_localize(None)
        df = df.set_axis(pd.to_datetime(idx).normalize(), axis=0)
        df = df[~df.index.duplicated(keep="last")]
        for a in ALANLAR:
            if a in df.columns:
                alanlar[a][sembol] = df[a]
    return {a: pd.DataFrame(d) for a, d in alanlar.items() if d}


def _gelenler(veri: dict) -> set[str]:
    if "Close" not in veri:
        return set()
    c = veri["Close"]
    return set(c.columns[c.notna().any()])


def _sinira_takildi(mesajlar: list[str]) -> bool:
    return any("RateLimit" in m or "Too Many Requests" in m or "429" in m for m in mesajlar)


def _neden_ozeti(mesajlar: list[str], adet: int = 3) -> str:
    """En sık görülen hata nedenlerini kısa bir metne çevirir."""
    sayac: dict[str, int] = {}
    for m in mesajlar:
        neden = re.sub(r"^\[.*?\]:\s*", "", m)          # baştaki sembol listesini at
        neden = re.sub(r"\$?[A-Z0-9.^=-]+:\s*", "", neden, count=1)
        sayac[neden[:160]] = sayac.get(neden[:160], 0) + 1
    enler = sorted(sayac.items(), key=lambda x: -x[1])[:adet]
    return "\n".join(f"    - {n}" for n, _ in enler) or "    - (Yahoo bir neden bildirmedi)"


def toplu_indir(semboller: list[str], etiket: str, **kw) -> tuple[dict, list[str]]:
    """Sembolleri küçük parçalar halinde indirir.

    Yahoo engellediğinde ya hata mesajı verir ya da sessizce boş veri döndürür.
    İkisini de yakalamak için: hata mesajında "rate limit" geçerse VEYA önceki
    parçalar iyi gelirken bir parçanın çoğu boş gelirse engellenmiş sayılır,
    giderek artan sürelerle beklenip aynı parça yeniden denenir. Üç bekleyişten
    sonra hâlâ engelliyse indirme durur; o ana kadar gelenle rapor hazırlanır,
    kalanlar bir sonraki çalıştırmada tamamlanır."""
    birikim = {a: [] for a in ALANLAR}
    eksik: list[str] = []
    nedenler: list[str] = []
    durum = {"mola": 1.5, "iyi_oran": 0.0, "durdu": False}

    def isle(parca: list[str]) -> int:
        veri, gelen = {}, set()
        for deneme in range(4):
            HATALAR.mesajlar.clear()
            veri = _indir(parca, **kw)
            gelen = _gelenler(veri)
            oran = len(gelen) / len(parca)
            ani_dusus = len(parca) >= 10 and durum["iyi_oran"] >= 0.6 and oran < 0.3
            if not (_sinira_takildi(HATALAR.mesajlar) or ani_dusus):
                durum["iyi_oran"] = max(durum["iyi_oran"], oran)
                break
            if deneme == 3:
                durum["durdu"] = True
                break
            sure = 60 * 2 ** deneme  # 60, 120, 240 sn
            durum["mola"] = min(durum["mola"] * 2, 10)
            yaz(f"\n  Yahoo istekleri yavaşlattı ya da engelledi; {sure} sn bekleniyor "
                f"(deneme {deneme + 1}/3)...")
            time.sleep(sure)
        nedenler.extend(HATALAR.mesajlar)
        for a in ALANLAR:
            if a in veri:
                birikim[a].append(veri[a][[s for s in veri[a].columns if s in gelen]])
        eksik.extend(s for s in parca if s not in gelen)
        return len(gelen)

    parcalar = [semboller[i:i + PARCA] for i in range(0, len(semboller), PARCA)]
    toplam_gelen = 0
    for n, parca in enumerate(parcalar, 1):
        yaz(f"\r  {etiket}: {n}/{len(parcalar)} parça ({len(semboller)} hisse, {toplam_gelen} geldi)", son="")
        toplam_gelen += isle(parca)
        if durum["durdu"]:
            kalan = [s for p in parcalar[n:] for s in p]
            eksik.extend(kalan)
            yaz(f"\n  Yahoo engellemeyi sürdürüyor; indirme durduruldu. "
                f"{toplam_gelen} hisse geldi, {len(eksik)} hisse bir sonraki çalıştırmaya kaldı.")
            break
        if n >= 2 and toplam_gelen == 0:
            yaz(f"\n\n  Hiçbir hissenin verisi gelmedi. Yahoo'nun verdiği hata:\n{_neden_ozeti(nedenler)}")
            sys.exit("\n  İndirme durduruldu. Bu mesajı Claude'a gönder.")
        time.sleep(durum["mola"])
    else:
        yaz(f"\r  {etiket}: {len(parcalar)}/{len(parcalar)} parça ({len(semboller)} hisse, {toplam_gelen} geldi)")

    if eksik and not durum["durdu"] and len(eksik) < 0.5 * len(semboller):
        ilk_eksik = list(eksik)
        eksik.clear()
        yaz(f"  {len(ilk_eksik)} hisse için ikinci deneme...")
        time.sleep(5)
        for i in range(0, len(ilk_eksik), 25):
            isle(ilk_eksik[i:i + 25])
            if durum["durdu"]:
                eksik.extend(s for s in ilk_eksik[i + 25:])
                break
            time.sleep(durum["mola"])
    if eksik:
        yaz(f"  {len(eksik)} hisse indirilemedi. En sık neden:\n{_neden_ozeti(nedenler, 2)}")

    sonuc = {}
    for a in ALANLAR:
        if birikim[a]:
            df = pd.concat(birikim[a], axis=1)
            sonuc[a] = df.loc[:, ~df.columns.duplicated()].sort_index()
    return sonuc, eksik


def fiyatlari_guncelle(kod: str, semboller: list[str], tam: bool) -> dict[str, pd.DataFrame]:
    yol = VERI / f"fiyat_{kod}.pkl"
    eski = None
    if yol.exists() and not tam:
        try:
            eski = pd.read_pickle(yol)
        except Exception:  # noqa: BLE001
            eski = None
    if eski is not None and (datetime.now() - eski["Close"].index.max()).days > 25:
        eski = None  # çok eski önbellek; baştan indir

    if eski is None:
        veri, eksik = toplu_indir(semboller, "Tam geçmiş", period=GECMIS)
        if "Close" not in veri:
            sys.exit("Hiç fiyat verisi indirilemedi. İnternet bağlantını kontrol et.")
    else:
        mevcut = [s for s in semboller if s in eski["Close"].columns]
        yeniler = [s for s in semboller if s not in eski["Close"].columns]
        baslangic = (eski["Close"].index.max() - timedelta(days=10)).strftime("%Y-%m-%d")
        kisa, eksik = toplu_indir(mevcut, "Son günler", start=baslangic)

        # Bölünme/bedelsiz/temettü sonrası Yahoo tüm geçmişi yeniden düzeltir.
        # Önbellekle çakışan günler tutmuyorsa o hissenin geçmişini baştan indir.
        yeniden: list[str] = []
        if "Close" in kisa:
            ortak_tarih = kisa["Close"].index.intersection(eski["Close"].index)
            ortak_sembol = kisa["Close"].columns.intersection(eski["Close"].columns)
            a = eski["Close"].loc[ortak_tarih, ortak_sembol]
            b = kisa["Close"].loc[ortak_tarih, ortak_sembol]
            fark = ((b - a).abs() / a.abs()).max()
            yeniden = fark[fark > 0.003].index.tolist()

        tam_liste = yeniler + yeniden
        tam_veri = {}
        if tam_liste:
            yaz(f"  {len(yeniler)} yeni hisse, {len(yeniden)} düzeltilmiş hisse için tam geçmiş indiriliyor.")
            tam_veri, _ = toplu_indir(tam_liste, "Tam geçmiş", period=GECMIS)

        veri = {}
        for a in ALANLAR:
            df = eski[a].drop(columns=yeniden, errors="ignore")
            if a in kisa:
                df = kisa[a].drop(columns=yeniden, errors="ignore").combine_first(df)
            if a in tam_veri:
                df = tam_veri[a].combine_first(df)
            veri[a] = df

    sinir = pd.Timestamp(datetime.now() - timedelta(days=400)).normalize()
    for a in ALANLAR:
        df = veri[a]
        df = df.loc[df.index >= sinir, [s for s in df.columns if s in set(semboller)]]
        veri[a] = df.sort_index()
    VERI.mkdir(parents=True, exist_ok=True)
    pd.to_pickle(veri, yol)
    return veri


def endeksleri_indir(ayar: dict) -> dict[str, pd.Series]:
    adlar = {ayar["endeks"][0]: ayar["endeks"][1], **ayar["ekstra"]}
    veri = _indir(list(adlar.values()), period=GECMIS)
    sonuc = {}
    if "Close" in veri:
        for ad, sembol in adlar.items():
            if sembol in veri["Close"].columns:
                s = veri["Close"][sembol].dropna()
                if len(s) >= 2:
                    sonuc[ad] = s
    return sonuc


# ---------------------------------------------------------------------------
# 3) Hesaplamalar
# ---------------------------------------------------------------------------

def hesapla(veri: dict, endeks: pd.Series | None, ayar: dict, adlar: pd.Series):
    c, h, l, v = (veri[a] for a in ("Close", "High", "Low", "Volume"))

    # Hisselerin en az yarısının işlem gördüğü günleri piyasa günü say
    sayim = c.notna().sum(axis=1)
    gunler = sayim[sayim >= 0.5 * sayim.max()].index
    c, h, l, v = (x.loc[gunler] for x in (c, h, l, v))
    son_tarih = c.index[-1]
    getiri = c / c.shift(1) - 1

    m = pd.DataFrame(index=c.columns)
    m.index.name = "sembol"
    m["ad"] = adlar.reindex(m.index).fillna("")
    son = c.iloc[-1]
    m["kapanis"] = son
    m["gunluk"] = son / c.iloc[-2] - 1
    m["hafta"] = son / c.iloc[-6] - 1 if len(c) > 6 else np.nan
    m["ay"] = son / c.iloc[-22] - 1 if len(c) > 22 else np.nan
    gecen_yil = c[c.index.year < son_tarih.year]
    m["yil_basi"] = son / gecen_yil.iloc[-1] - 1 if len(gecen_yil) else np.nan
    m["hacim"] = v.iloc[-1]
    ort_hacim = v.iloc[-21:-1].mean()
    m["hacim_orani"] = v.iloc[-1] / ort_hacim.where(ort_hacim > 0)
    m["ort_islem"] = (c * v).iloc[-20:].mean()

    # RSI(14), Wilder yöntemi (TradingView ile aynı)
    fark = c.diff()
    kazanc = fark.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    kayip = (-fark.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    m["rsi"] = (100 - 100 / (1 + kazanc / kayip)).iloc[-1]

    sma50 = c.rolling(50, min_periods=50).mean()
    sma200 = c.rolling(200, min_periods=200).mean()
    m["sma50_fark"] = son / sma50.iloc[-1] - 1
    m["sma200_fark"] = son / sma200.iloc[-1] - 1
    if len(c) >= 2:
        gecerli = sma50.iloc[-2:].notna().all() & sma200.iloc[-2:].notna().all()
        ust_bugun = sma50.iloc[-1] > sma200.iloc[-1]
        ust_dun = sma50.iloc[-2] > sma200.iloc[-2]
        m["kesisim"] = np.where(gecerli & ust_bugun & ~ust_dun, "Altın kesişim",
                                np.where(gecerli & ~ust_bugun & ust_dun, "Ölüm kesişimi", ""))
    else:
        m["kesisim"] = ""

    yeterli = c.iloc[-252:].notna().sum() >= 200
    m["zirve52"] = yeterli & (h.iloc[-1] >= h.iloc[-252:-1].max())
    m["dip52"] = yeterli & (l.iloc[-1] <= l.iloc[-252:-1].min())
    m["zirveden_uzaklik"] = son / h.iloc[-252:].max() - 1

    # Endeksle 60 günlük getiri korelasyonu ve beta
    m["korelasyon"] = np.nan
    m["beta"] = np.nan
    if endeks is not None:
        e = endeks.reindex(c.index)
        eg = (e / e.shift(1) - 1).iloc[-60:]
        R = getiri.iloc[-60:]
        if eg.notna().sum() >= 40:
            n = R.notna().mul(eg.notna(), axis=0).sum()
            kor = R.corrwith(eg)
            beta = kor * R.std() / eg.std()
            m["korelasyon"] = kor.where(n >= 40)
            m["beta"] = beta.where(n >= 40)

    son_veri = pd.to_datetime(c.apply(lambda s: s.last_valid_index()))
    m["son_veri"] = son_veri
    m["guncel"] = son_veri == son_tarih
    m["gun_sayisi"] = c.notna().sum()

    # Veri kalitesi kontrolleri
    uyarilar = {s: [] for s in m.index}
    for s in m.index[~m["guncel"]]:
        tarih = son_veri[s]
        uyarilar[s].append(f"Son veri {tarih:%d.%m.%Y}" if pd.notna(tarih) else "Veri yok")
    for s in m.index[m["guncel"] & (m["hacim"] == 0)]:
        uyarilar[s].append("Bugün işlem yok")
    for s in m.index[m["gun_sayisi"] < 60]:
        uyarilar[s].append("Az veri (<60 gün), göstergeler güvenilmez")
    buyuk = getiri.iloc[-60:].abs()
    en_buyuk = buyuk.max()
    for s in en_buyuk.index[en_buyuk > ayar["supheli_hareket"]]:
        tarih = buyuk[s].idxmax()
        degisim = getiri.loc[tarih, s]
        uyarilar[s].append(f"{tarih:%d.%m} günü {yuzde(degisim)} hareket: {ayar['supheli_mesaj']}")
    m["uyari"] = pd.Series({s: " · ".join(u) for s, u in uyarilar.items()})

    m["likit"] = (m["guncel"] & (m["ort_islem"] >= ayar["min_islem_hacmi"])
                  & (m["kapanis"] >= ayar["min_fiyat"]))
    return m, son_tarih


# ---------------------------------------------------------------------------
# 4) Biçimlendirme (Türkçe sayı biçimi)
# ---------------------------------------------------------------------------

def _tr(sayi: float, basamak: int) -> str:
    return f"{sayi:,.{basamak}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def yuzde(x) -> str:
    if x is None or pd.isna(x):
        return "–"
    isaret = "+" if x > 0 else ("−" if x < 0 else "")
    return f"{isaret}%{_tr(abs(x) * 100, 2)}"


def sayi(x, basamak: int = 2) -> str:
    return "–" if x is None or pd.isna(x) else _tr(x, basamak)


def kisalt(x) -> str:
    if x is None or pd.isna(x):
        return "–"
    for sinir, ek in ((1e12, "Tn"), (1e9, "Mr"), (1e6, "Mn"), (1e3, "B")):
        if abs(x) >= sinir:
            return f"{_tr(x / sinir, 1)} {ek}"
    return _tr(x, 0)


def renk(x) -> str:
    if x is None or pd.isna(x) or x == 0:
        return ""
    return "pos" if x > 0 else "neg"


# ---------------------------------------------------------------------------
# 5) HTML rapor
# ---------------------------------------------------------------------------

CSS = """
:root{--bg:#f6f7f9;--kart:#fff;--yazi:#1c2128;--soluk:#5b6470;--cizgi:#e3e6ea;--vurgu:#0b6bcb;
--pos:#16803c;--neg:#c62828;--uyari-bg:#fff6e0;--uyari:#8a5a00;--satir:#f9fafb}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0f1216;--kart:#171b21;--yazi:#e6e9ee;
--soluk:#9aa4b1;--cizgi:#2a3039;--vurgu:#5aa9ff;--pos:#3fbf6a;--neg:#ff6b6b;--uyari-bg:#2b2412;--uyari:#f0c060;--satir:#1b2027}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--yazi);
font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:0 0 10px}
.alt{color:var(--soluk);font-size:13px}
.banner{background:var(--uyari-bg);color:var(--uyari);padding:10px 14px;border-radius:8px;margin:14px 0}
.ozet{display:grid;grid-template-columns:repeat(auto-fit,minmax(128px,1fr));gap:10px;margin:18px 0}
.kutu{background:var(--kart);border:1px solid var(--cizgi);border-radius:10px;padding:12px 14px}
.kutu b{display:block;font-size:20px;font-variant-numeric:tabular-nums;margin:2px 0}.kutu .et,.kutu .dip{display:block;color:var(--soluk);font-size:12px}
.kutu .dip.pos{color:var(--pos)}.kutu .dip.neg{color:var(--neg)}
.izgara{display:grid;grid-template-columns:repeat(auto-fit,minmax(520px,1fr));gap:14px}
@media (max-width:600px){.izgara{grid-template-columns:1fr}}
section.kart{background:var(--kart);border:1px solid var(--cizgi);border-radius:10px;padding:14px;overflow-x:auto}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:6px 8px;border-bottom:1px solid var(--cizgi);text-align:right;white-space:nowrap}
th:first-child,td:first-child,th.sol,td.sol{text-align:left}
td.ad{max-width:220px;overflow:hidden;text-overflow:ellipsis;color:var(--soluk)}
th{font-size:12px;color:var(--soluk);font-weight:600;position:sticky;top:0;background:var(--kart)}
@media (max-width:600px){th.ad,td.ad{display:none}section.kart{padding:12px 10px}th,td{padding:6px 6px}}
tbody tr:hover{background:var(--satir)}
.pos{color:var(--pos)}.neg{color:var(--neg)}
a{color:var(--vurgu);text-decoration:none}a:hover{text-decoration:underline}
.isaret{color:var(--uyari);cursor:help}
.bos{color:var(--soluk);padding:8px 0}
td.uyari{white-space:normal;text-align:left;color:var(--uyari);font-size:12px;min-width:260px}
.arac{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin-bottom:10px}
.arac input[type=search]{padding:7px 10px;border:1px solid var(--cizgi);border-radius:6px;background:var(--bg);color:var(--yazi);min-width:220px}
#tum th{cursor:pointer;user-select:none}#tum th:hover{color:var(--yazi)}
.tablo-kap{max-height:640px;overflow:auto}
details summary{cursor:pointer;color:var(--soluk);margin:6px 0}
footer{color:var(--soluk);font-size:12px;margin-top:28px}
"""

JS = """
const t=document.getElementById('tum');
if(t){const tb=t.tBodies[0],rows=[...tb.rows],ara=document.getElementById('ara'),
lk=document.getElementById('likit'),uy=document.getElementById('uyarili'),say=document.getElementById('sayac');
function suz(){const q=ara.value.trim().toLocaleUpperCase('tr');let n=0;
for(const r of rows){const ok=(!q||r.dataset.k.includes(q))&&(!lk.checked||r.dataset.l==='1')&&(!uy.checked||r.dataset.u==='1');
r.style.display=ok?'':'none';if(ok)n++;}say.textContent=n.toLocaleString('tr-TR')+' hisse';}
[ara,lk,uy].forEach(e=>e.addEventListener('input',suz));
let yon={};t.tHead.querySelectorAll('th').forEach((th,i)=>th.addEventListener('click',()=>{
const asc=yon[i]=!yon[i];const sayisal=th.dataset.tip!=='m';
rows.sort((a,b)=>{let x=a.cells[i].dataset.v,y=b.cells[i].dataset.v;
if(sayisal){x=x===''?NaN:+x;y=y===''?NaN:+y;if(isNaN(x))return 1;if(isNaN(y))return -1;return asc?x-y:y-x;}
return asc?x.localeCompare(y,'tr'):y.localeCompare(x,'tr');});
rows.forEach(r=>tb.appendChild(r));}));suz();}
"""


def sembol_link(sembol: str, kod: str) -> str:
    gorunen = sembol.removesuffix(".IS")
    if kod == "bist":
        url = f"https://www.tradingview.com/symbols/BIST-{gorunen}/"
    else:
        url = f"https://finance.yahoo.com/quote/{sembol}"
    return f'<a href="{url}" target="_blank" rel="noopener">{html.escape(gorunen)}</a>'


def kolon(baslik, alan, bicim, sinif=None):
    return (baslik, alan, bicim, sinif)


def liste_tablosu(df: pd.DataFrame, kod: str, para: str, ek: list | None = None) -> str:
    if df.empty:
        return '<div class="bos">Bugün bu ölçüte uyan hisse yok.</div>'
    kolonlar = [
        kolon("Ad", "ad", lambda x: html.escape(str(x)[:40]), "ad sol"),
        kolon(f"Kapanış ({para})", "kapanis", sayi),
        kolon("Günlük", "gunluk", yuzde, "renk"),
    ] + (ek or [
        kolon("Hacim/Ort.", "hacim_orani", lambda x: "–" if pd.isna(x) else f"{_tr(x, 1)}x"),
        kolon("RSI", "rsi", lambda x: sayi(x, 0)),
    ])
    bas = "<tr><th>Sembol</th>" + "".join(
        f'<th class="{s if (s or "").startswith("ad") else ""}">{b}</th>' for b, _, _, s in kolonlar) + "</tr>"
    satirlar = []
    for sembol, r in df.iterrows():
        isaret = (f' <span class="isaret" title="{html.escape(r["uyari"])}">⚠</span>'
                  if r["uyari"] else "")
        hucreler = [f"<td>{sembol_link(sembol, kod)}{isaret}</td>"]
        for _, alan, bicim, sinif in kolonlar:
            cls = renk(r[alan]) if sinif == "renk" else (sinif or "")
            title = f' title="{html.escape(str(r[alan]))}"' if alan == "ad" else ""
            hucreler.append(f'<td class="{cls}"{title}>{bicim(r[alan])}</td>')
        satirlar.append("<tr>" + "".join(hucreler) + "</tr>")
    return f"<table><thead>{bas}</thead><tbody>{''.join(satirlar)}</tbody></table>"


def bolum(baslik: str, aciklama: str, icerik: str) -> str:
    return (f'<section class="kart"><h2>{baslik}</h2>'
            f'<div class="alt" style="margin:-6px 0 8px">{aciklama}</div>{icerik}</section>')


def tum_tablo(m: pd.DataFrame, kod: str, para: str) -> str:
    kolonlar = [
        ("Ad", "ad", lambda x: html.escape(str(x)[:40]), "m"),
        (f"Kapanış ({para})", "kapanis", sayi, None),
        ("Günlük", "gunluk", yuzde, "renk"),
        ("1 Hafta", "hafta", yuzde, "renk"),
        ("1 Ay", "ay", yuzde, "renk"),
        ("Yıl başı", "yil_basi", yuzde, "renk"),
        ("Hacim/Ort.", "hacim_orani", lambda x: "–" if pd.isna(x) else f"{_tr(x, 1)}x", None),
        (f"Ort. işlem ({para})", "ort_islem", kisalt, None),
        ("RSI", "rsi", lambda x: sayi(x, 0), None),
        ("SMA200 farkı", "sma200_fark", yuzde, "renk"),
        ("52h zirveye", "zirveden_uzaklik", yuzde, None),
        ("Endeks kor.", "korelasyon", sayi, None),
        ("Beta", "beta", sayi, None),
        ("Uyarı", "uyari", lambda x: html.escape(x), "m"),
    ]
    bas = '<tr><th data-tip="m">Sembol</th>' + "".join(
        f'<th data-tip="{"m" if t == "m" else "s"}" class="{("ad sol" if a == "ad" else "sol") if t == "m" else ""}">{b}</th>'
        for b, a, _, t in kolonlar) + "</tr>"
    satirlar = []
    for sembol, r in m.sort_values("ort_islem", ascending=False).iterrows():
        anahtar = html.escape(f"{sembol.removesuffix('.IS')} {r['ad']}".upper(), quote=True)
        hucreler = [f'<td data-v="{html.escape(sembol)}">{sembol_link(sembol, kod)}</td>']
        for _, alan, bicim, t in kolonlar:
            deger = r[alan]
            if t == "m":
                dv = html.escape(str(deger), quote=True)
                cls = "uyari" if alan == "uyari" else "ad sol"
            else:
                dv = "" if pd.isna(deger) else f"{float(deger):.6g}"
                cls = renk(deger) if t == "renk" else ""
            hucreler.append(f'<td class="{cls}" data-v="{dv}">{bicim(deger)}</td>')
        satirlar.append(f'<tr data-k="{anahtar}" data-l="{int(bool(r["likit"]))}" '
                        f'data-u="{int(bool(r["uyari"]))}">' + "".join(hucreler) + "</tr>")
    return (
        '<div class="arac"><input type="search" id="ara" placeholder="Sembol ya da şirket ara">'
        '<label><input type="checkbox" id="likit" checked> Sadece likit hisseler</label>'
        '<label><input type="checkbox" id="uyarili"> Sadece uyarılı</label>'
        '<span class="alt" id="sayac"></span></div>'
        f'<div class="tablo-kap"><table id="tum"><thead>{bas}</thead>'
        f'<tbody>{"".join(satirlar)}</tbody></table></div>'
    )


def rapor_html(kod: str, ayar: dict, m: pd.DataFrame, son_tarih, endeksler: dict,
               banner: str, toplam_liste: int) -> str:
    para = ayar["para"]
    g = m[m["guncel"]]
    lk = m[m["likit"]]
    n = LISTE_UZUNLUK

    yukselen = (g["gunluk"] > 0).sum()
    dusen = (g["gunluk"] < 0).sum()
    sabit = len(g) - yukselen - dusen
    sma_var = g["sma200_fark"].notna()
    sma_ust = (g.loc[sma_var, "sma200_fark"] > 0).mean() if sma_var.any() else np.nan

    kutular = []
    for ad, s in endeksler.items():
        degisim = s.iloc[-1] / s.iloc[-2] - 1
        kutular.append(f'<div class="kutu"><span class="et">{html.escape(ad)}</span><b>{sayi(s.iloc[-1])}</b>'
                       f'<span class="dip {renk(degisim)}">{yuzde(degisim)}</span></div>')
    kutular += [
        f'<div class="kutu"><span class="et">Yükselen / Düşen</span><b><span class="pos">{yukselen}</span> / '
        f'<span class="neg">{dusen}</span></b><span class="dip">{sabit} değişmeyen</span></div>',
        f'<div class="kutu"><span class="et">Medyan değişim</span><b class="{renk(g["gunluk"].median())}">'
        f'{yuzde(g["gunluk"].median())}</b><span class="dip">{len(g)} hissenin ortası</span></div>',
        f'<div class="kutu"><span class="et">SMA200 üstünde</span><b>{yuzde(sma_ust).lstrip("+")}</b>'
        f'<span class="dip">piyasa genişliği</span></div>',
        f'<div class="kutu"><span class="et">52h zirve / dip</span><b><span class="pos">{int(g["zirve52"].sum())}</span> / '
        f'<span class="neg">{int(g["dip52"].sum())}</span></b><span class="dip">yeni zirve / dip</span></div>',
    ]

    ek_rsi = [kolon("RSI", "rsi", lambda x: sayi(x, 1)),
              kolon("1 Ay", "ay", yuzde, "renk")]
    ek_hacim = [kolon("Hacim/Ort.", "hacim_orani", lambda x: f"{_tr(x, 1)}x"),
                kolon(f"Ort. işlem ({para})", "ort_islem", kisalt)]
    ek_52 = [kolon("1 Ay", "ay", yuzde, "renk"), kolon("RSI", "rsi", lambda x: sayi(x, 0))]
    ek_kes = [kolon("Sinyal", "kesisim", html.escape, "sol"),
              kolon("SMA200 farkı", "sma200_fark", yuzde, "renk")]

    kesisim = lk[lk["kesisim"] != ""].sort_values("ort_islem", ascending=False).head(n)
    likit_aciklama = (f"Son 20 gün ortalama işlem hacmi ≥ {kisalt(ayar['min_islem_hacmi'])} {para}"
                      + (f", fiyat ≥ {ayar['min_fiyat']} {para}" if ayar["min_fiyat"] else ""))

    bolumler = [
        bolum("En çok yükselenler", likit_aciklama,
              liste_tablosu(lk.sort_values("gunluk", ascending=False).head(n), kod, para)),
        bolum("En çok düşenler", likit_aciklama,
              liste_tablosu(lk.sort_values("gunluk").head(n), kod, para)),
        bolum("Hacim patlaması", f"Bugünkü hacim, 20 günlük ortalamanın {_tr(HACIM_PATLAMA, 0)} katından fazla",
              liste_tablosu(lk[lk["hacim_orani"] >= HACIM_PATLAMA]
                            .sort_values("hacim_orani", ascending=False).head(n), kod, para, ek_hacim)),
        bolum("Kesişim sinyalleri", "Bugün 50 günlük ortalama 200 günlüğü kesti",
              liste_tablosu(kesisim, kod, para, ek_kes)),
        bolum("RSI aşırı alım (≥ 70)", "RSI(14), Wilder yöntemi",
              liste_tablosu(lk[lk["rsi"] >= 70].sort_values("rsi", ascending=False).head(n), kod, para, ek_rsi)),
        bolum("RSI aşırı satım (≤ 30)", "RSI(14), Wilder yöntemi",
              liste_tablosu(lk[lk["rsi"] <= 30].sort_values("rsi").head(n), kod, para, ek_rsi)),
        bolum("52 hafta zirvesi", "Bugünkü en yüksek fiyat son 1 yılın zirvesini geçti",
              liste_tablosu(lk[lk["zirve52"]].sort_values("ort_islem", ascending=False).head(n), kod, para, ek_52)),
        bolum("52 hafta dibi", "Bugünkü en düşük fiyat son 1 yılın dibinin altına indi",
              liste_tablosu(lk[lk["dip52"]].sort_values("ort_islem", ascending=False).head(n), kod, para, ek_52)),
    ]

    supheli = g[g["uyari"] != ""].sort_values("ort_islem", ascending=False)
    eski = m[~m["guncel"]].sort_values("son_veri", ascending=False)
    uyari_tablo = (
        "<table><thead><tr><th>Sembol</th><th class='ad sol'>Ad</th><th>Günlük</th><th class='sol'>Uyarı</th></tr></thead><tbody>"
        + "".join(f"<tr><td>{sembol_link(s, kod)}</td><td class='ad sol'>{html.escape(str(r['ad'])[:40])}</td>"
                  f"<td class='{renk(r['gunluk'])}'>{yuzde(r['gunluk'])}</td><td class='uyari'>{html.escape(r['uyari'])}</td></tr>"
                  for s, r in supheli.iterrows())
        + "</tbody></table>"
    ) if len(supheli) else '<div class="bos">Güncel hisselerde şüpheli veri bulunmadı.</div>'
    eski_liste = ", ".join(f"{s.removesuffix('.IS')} ({r['son_veri']:%d.%m})" if pd.notna(r["son_veri"])
                           else s.removesuffix(".IS") for s, r in eski.iterrows())
    kalite = (
        f'<p class="alt">Listede {toplam_liste} hisse var; {len(g)} tanesinin bugünkü verisi geldi. '
        "⚠ işaretli hisselerde göstergeler yanıltıcı olabilir; resmi kaynaktan kontrol et.</p>"
        f'<div class="tablo-kap" style="max-height:360px">{uyari_tablo}</div>'
        + (f"<details><summary>Bugün verisi gelmeyen {len(eski)} hisse (askıda, işlem dışı ya da veri eksik)</summary>"
           f'<p class="alt">{html.escape(eski_liste)}</p></details>' if len(eski) else "")
    )

    olusturma = datetime.now().strftime("%d.%m.%Y %H:%M")
    return f"""<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{ayar['ad']} · {son_tarih:%d.%m.%Y}</title><style>{CSS}</style></head>
<body><main>
<h1>{ayar['ad']} günlük rapor</h1>
<div class="alt">Veri tarihi: <b>{son_tarih:%d.%m.%Y}</b> · Oluşturma: {olusturma} · Kaynak: Yahoo Finance (düzeltilmiş fiyatlar)</div>
{banner}
<div class="ozet">{''.join(kutular)}</div>
<div class="izgara">{''.join(bolumler)}</div>
<div style="margin-top:14px">{bolum("Veri kalitesi", "Şüpheli hareketler, eksik ve yetersiz veriler", kalite)}</div>
<div style="margin-top:14px">{bolum("Tüm hisseler", "Başlığa tıklayarak sırala. Sembole tıklayınca grafik açılır; RSI'ı oradan da karşılaştırabilirsin.", tum_tablo(m, kod, para))}</div>
<footer>Bu rapor otomatik üretilmiştir ve yatırım tavsiyesi değildir. Yahoo Finance verileri gecikmeli ya da hatalı olabilir.
Korelasyon ve beta, {ayar['endeks'][0]} ile son 60 günlük günlük getiriler üzerinden hesaplanmıştır.</footer>
</main><script>{JS}</script></body></html>"""


# ---------------------------------------------------------------------------
# 6) Ana akış
# ---------------------------------------------------------------------------

def piyasa_raporu(kod: str, tam: bool, ac: bool) -> Path | None:
    ayar = PIYASALAR[kod]
    basla = time.time()
    yaz(f"\n=== {ayar['ad']} ===")
    liste = hisse_listesi(kod, ayar, tam)
    yaz(f"  {len(liste)} hisse")
    adlar = liste.set_index("sembol")["ad"]
    veri = fiyatlari_guncelle(kod, liste["sembol"].tolist(), tam)
    endeksler = endeksleri_indir(ayar)
    endeks = endeksler.get(ayar["endeks"][0])

    m, son_tarih = hesapla(veri, endeks, ayar, adlar)

    banner = ""
    simdi = datetime.now(ZoneInfo(ayar["saat_dilimi"]))
    if (son_tarih.date() == simdi.date() and simdi.weekday() < 5
            and (simdi.hour, simdi.minute) < ayar["kapanis"]):
        banner = ('<div class="banner">Piyasa henüz kapanmadı: bugünün verileri anlık ve kesinleşmemiş. '
                  "Kesin rapor için kapanıştan sonra tekrar çalıştır.</div>")
    guncel_sayi = int(m["guncel"].sum())
    if guncel_sayi < 0.9 * len(liste):
        banner += (f'<div class="banner"><b>Rapor eksik:</b> listedeki {len(liste)} hissenin yalnızca '
                   f"{guncel_sayi} tanesi için güncel veri var. Yahoo indirmeyi yavaşlatmış olabilir. "
                   "Script'i bir süre sonra tekrar çalıştır; eksik hisseler tamamlanır.</div>")
        yaz(f"  DİKKAT: {len(liste)} hissenin yalnızca {guncel_sayi} tanesi güncel; rapor eksik. "
            "Bir süre sonra tekrar çalıştır.")

    RAPORLAR.mkdir(parents=True, exist_ok=True)
    dosya = RAPORLAR / f"{son_tarih:%Y-%m-%d}_{kod.upper()}"
    html_yol = dosya.with_suffix(".html")
    html_yol.write_text(rapor_html(kod, ayar, m, son_tarih, endeksler, banner, len(liste)), encoding="utf-8")

    csv = m.drop(columns=["likit"]).copy()
    csv["son_veri"] = csv["son_veri"].dt.strftime("%Y-%m-%d")
    csv.sort_values("ort_islem", ascending=False).to_csv(
        dosya.with_name(dosya.name + "_tum.csv"), sep=";", decimal=",", encoding="utf-8-sig")

    g = m[m["guncel"]]
    lk = m[m["likit"]]
    yaz(f"  Veri tarihi: {son_tarih:%d.%m.%Y} · {len(g)}/{len(m)} hisse güncel · "
        f"{(g['gunluk'] > 0).sum()} yükselen, {(g['gunluk'] < 0).sum()} düşen")
    if endeks is not None:
        yaz(f"  {ayar['endeks'][0]}: {yuzde(endeks.iloc[-1] / endeks.iloc[-2] - 1)}")
    if len(lk):
        en = lk["gunluk"].idxmax()
        en_d = lk["gunluk"].idxmin()
        yaz(f"  En çok yükselen: {en.removesuffix('.IS')} {yuzde(lk.loc[en, 'gunluk'])} · "
            f"En çok düşen: {en_d.removesuffix('.IS')} {yuzde(lk.loc[en_d, 'gunluk'])}")
    yaz(f"  Uyarılı hisse: {(g['uyari'] != '').sum()} · Süre: {time.time() - basla:.0f} sn")
    yaz(f"  Rapor: {html_yol}")
    if ac and sys.platform == "darwin":
        subprocess.run(["open", str(html_yol)], check=False)
    return html_yol


def main() -> None:
    _dosya_sinirini_artir()
    p = argparse.ArgumentParser(description="BIST ve ABD borsaları için günlük rapor")
    p.add_argument("piyasa", nargs="?", default="hepsi", choices=["hepsi", "bist", "abd"])
    p.add_argument("--tam", action="store_true", help="önbelleği yok say, her şeyi baştan indir")
    p.add_argument("--acma", action="store_true", help="raporu tarayıcıda açma")
    args = p.parse_args()
    kodlar = ["bist", "abd"] if args.piyasa == "hepsi" else [args.piyasa]
    for kod in kodlar:
        try:
            piyasa_raporu(kod, args.tam, not args.acma)
        except KeyboardInterrupt:
            sys.exit("\nİptal edildi.")
        except SystemExit:
            raise
        except Exception as hata:  # noqa: BLE001
            yaz(f"\n  {PIYASALAR[kod]['ad']} raporu oluşturulamadı: {hata!r}")
            if len(kodlar) == 1:
                raise


if __name__ == "__main__":
    main()
