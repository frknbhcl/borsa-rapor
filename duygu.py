"""
ABD için duygu modelleri: açığa satış oranı ve kazanç sürprizi.

- Açığa satış oranı (Wang, Yan & Zheng 2020, JFE; Diether, Lee & Werner 2009, RFS): FINRA'nın her gün
  yayımladığı açığa satış hacmi ÷ toplam hacim, son 20 işlem günü. Yoğun açığa satılan hisseler
  sonraki aylarda geride kalma eğiliminde. Kaynak: https://cdn.finra.org/equity/regsho/daily/
- Kazanç sürprizi (SUE; Bernard & Thomas 1989, Livnat & Mendenhall 2006): son açıklanan hisse başı kâr
  ile analist beklentisi arasındaki fark ÷ hisse fiyatı (son 100 gün içinde açıklanmışsa). Beklentiyi
  aşanlar sonraki aylarda da iyi gitme eğiliminde. Kaynak: Yahoo Finance kazanç takvimi.

Veri dosyaları (veri/ klasörü; kalıcı kopyaları "veri-arsivi" sürümünde):
    finra_abd.pkl   {"kisa": DataFrame, "toplam": DataFrame}  (satır = gün, sütun = sembol)
    kazanc_abd.pkl  {"olaylar": {sembol: DataFrame[gun, tahmin, gercek]}, "guncelleme": {sembol: Timestamp}}
"""

from __future__ import annotations

import concurrent.futures as cf
import io
import pickle
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

FINRA_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{:%Y%m%d}.txt"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
ACIGA_GUN = 20
SUE_GUN = 100
MODEL_IDLER = ("aciga_satis", "kazanc_surprizi")


def yaz(*a):
    print(*a, flush=True)


# ----------------------------------------------------------------------------- FINRA
def _finra_gun(oturum: requests.Session, gun: pd.Timestamp):
    for deneme in range(3):
        try:
            r = oturum.get(FINRA_URL.format(gun), timeout=60)
            if r.status_code == 404 or (r.status_code == 403 and "AccessDenied" in r.text):
                return gun, None          # tatil ya da henüz yayımlanmadı
            if r.status_code == 200:
                df = pd.read_csv(io.StringIO(r.text), sep="|", usecols=["Symbol", "ShortVolume", "TotalVolume"],
                                 dtype={"Symbol": str})
                df = df[df["Symbol"].notna() & df["TotalVolume"].notna()]
                df["Symbol"] = df["Symbol"].str.replace(".", "-", regex=False)
                return gun, df.groupby("Symbol")[["ShortVolume", "TotalVolume"]].sum()
        except (requests.RequestException, ValueError):
            pass
        time.sleep(2 * (deneme + 1))
    return gun, None


def finra_indir(gunler, semboller=None) -> dict:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    kisa, toplam = {}, {}
    with cf.ThreadPoolExecutor(8) as ex:
        for gun, df in ex.map(lambda g: _finra_gun(s, g), list(gunler)):
            if df is None:
                continue
            if semboller is not None:
                df = df[df.index.isin(semboller)]
            kisa[gun] = df["ShortVolume"].astype("float32")
            toplam[gun] = df["TotalVolume"].astype("float32")
    if not kisa:
        return {"kisa": pd.DataFrame(), "toplam": pd.DataFrame()}
    return {"kisa": pd.DataFrame(kisa).T.sort_index(), "toplam": pd.DataFrame(toplam).T.sort_index()}


def finra_guncelle(dosya: Path, semboller=None, baslangic: date = date(2020, 9, 1)) -> dict:
    """Dosyadaki geçmişe eksik günleri ekler (yoksa baştan indirir)."""
    eski = None
    if dosya.exists():
        try:
            eski = pickle.loads(dosya.read_bytes())
        except Exception:  # noqa: BLE001
            eski = None
    bas = baslangic if eski is None or eski["kisa"].empty else (eski["kisa"].index.max() + timedelta(days=1)).date()
    gunler = pd.bdate_range(bas, date.today())
    yeni = finra_indir(gunler, semboller)
    yaz(f"  FINRA: {len(yeni['kisa'])} yeni gün")
    if eski is not None and not eski["kisa"].empty:
        F = {k: pd.concat([eski[k], yeni[k]]).sort_index() for k in ("kisa", "toplam")}
        F = {k: v[~v.index.duplicated(keep="last")] for k, v in F.items()}
    else:
        F = yeni
    dosya.parent.mkdir(parents=True, exist_ok=True)
    dosya.write_bytes(pickle.dumps(F, protocol=pickle.HIGHEST_PROTOCOL))
    return F


def finra_son(gun: int = ACIGA_GUN + 10) -> dict:
    """Günlük rapor için yalnızca son günler (önbellek gerektirmez)."""
    gunler = pd.bdate_range(date.today() - timedelta(days=int(gun * 1.6) + 5), date.today())
    return finra_indir(gunler)


def aciga_oran(F: dict, t: pd.Timestamp | None = None, gun: int = ACIGA_GUN) -> pd.Series:
    k, tp = F["kisa"], F["toplam"]
    if k.empty:
        return pd.Series(dtype=float)
    if t is not None:
        k, tp = k[k.index <= t], tp[tp.index <= t]
    k, tp = k.iloc[-gun:], tp.iloc[-gun:]
    gecerli = tp.notna().sum() >= gun // 2
    return (k.sum() / tp.sum().where(tp.sum() > 0)).where(gecerli)


# ----------------------------------------------------------------------------- Kazanç
_HATA = {"ardisik": 0, "basari": 0}


def _kazanc_tek(sym: str, hiz):
    import yfinance as yf
    # Devre kesici: hiç başarı olmadan art arda 30 hata (ör. Yahoo engeli) olursa vazgeç
    if _HATA["basari"] == 0 and _HATA["ardisik"] >= 30:
        return sym, None
    for deneme in range(3):
        try:
            hiz()
            df = yf.Ticker(sym).get_earnings_dates(limit=36)
            _HATA["basari"] += 1
            _HATA["ardisik"] = 0
            if df is None or not len(df):
                return sym, pd.DataFrame(columns=["gun", "tahmin", "gercek"])
            return sym, kazanc_bicimle(df.reset_index())
        except Exception as e:  # noqa: BLE001
            if "Too Many Requests" in str(e) or "Rate" in type(e).__name__:
                time.sleep(20 * (deneme + 1))
            else:
                time.sleep(3 * (deneme + 1))
    _HATA["ardisik"] += 1
    return sym, None


def kazanc_bicimle(k: pd.DataFrame) -> pd.DataFrame:
    tcol = [c for c in k.columns if "Date" in c][0]
    t = pd.to_datetime(k[tcol], utc=True).dt.tz_convert("America/New_York")
    # Piyasa kapandıktan sonra (16:00 ve sonrası) açıklanan bilançoya tepki ertesi gün
    gun = t.dt.tz_localize(None).dt.normalize() + pd.to_timedelta((t.dt.hour >= 16).astype(int), unit="D")
    out = pd.DataFrame({"gun": gun.to_numpy(), "tahmin": pd.to_numeric(k.get("EPS Estimate"), errors="coerce").to_numpy(),
                        "gercek": pd.to_numeric(k.get("Reported EPS"), errors="coerce").to_numpy()})
    return out.sort_values("gun").drop_duplicates("gun", keep="last").reset_index(drop=True)


def kazanc_guncelle(dosya: Path, semboller: list[str], tam: bool = False, en_fazla: int = 600) -> dict:
    """tam=True: hepsini yeniden indir. Değilse yalnızca hiç verisi olmayanları ve son günlerde
    bilanço açıklamış (ya da açıklaması beklenen) hisseleri günceller."""
    K = {"olaylar": {}, "guncelleme": {}}
    if dosya.exists():
        try:
            K = pickle.loads(dosya.read_bytes())
        except Exception:  # noqa: BLE001
            pass
    bugun = pd.Timestamp(date.today())
    if tam:
        hedef = list(semboller)
    else:
        hedef = []
        for s in semboller:
            olay = K["olaylar"].get(s)
            son = K["guncelleme"].get(s)
            if olay is None:
                hedef.append(s)
                continue
            # Son 6 günde açıklama günü olup gerçekleşen değeri boş olan ya da açıklamadan sonra
            # güncellenmemiş hisseler
            yakin = olay[(olay["gun"] >= bugun - pd.Timedelta(days=6)) & (olay["gun"] <= bugun + pd.Timedelta(days=1))]
            if len(yakin) and (yakin["gercek"].isna().any() or son is None or son < yakin["gun"].max()):
                hedef.append(s)
        hedef = hedef[:en_fazla]
    yaz(f"  Kazanç: {len(hedef)} hisse güncellenecek ({'tam' if tam else 'artımlı'})")
    kilit, son_t = threading.Lock(), [0.0]

    def hiz():
        with kilit:
            bekle = son_t[0] + 0.34 - time.monotonic()
            if bekle > 0:
                time.sleep(bekle)
            son_t[0] = time.monotonic()

    basari = 0
    with cf.ThreadPoolExecutor(3) as ex:
        for sym, df in ex.map(lambda s: _kazanc_tek(s, hiz), hedef):
            if df is not None:
                K["olaylar"][sym] = df
                K["guncelleme"][sym] = bugun
                basari += 1
    yaz(f"  Kazanç: {basari}/{len(hedef)} güncellendi")
    dosya.parent.mkdir(parents=True, exist_ok=True)
    dosya.write_bytes(pickle.dumps(K, protocol=pickle.HIGHEST_PROTOCOL))
    return K


def sue(K: dict, fiyat: pd.Series, t: pd.Timestamp, gun: int = SUE_GUN) -> pd.Series:
    """Son `gun` gün içinde (t dahil) açıklanan bilançonun sürprizi ÷ fiyat."""
    out = {}
    alt = t - pd.Timedelta(days=gun)
    for s, olay in K["olaylar"].items():
        if s not in fiyat.index or olay is None or not len(olay):
            continue
        g = olay["gun"].to_numpy()
        i = np.searchsorted(g, np.datetime64(t), side="right") - 1
        while i >= 0 and np.isnan(olay["gercek"].iat[i]):
            i -= 1  # henüz açıklanmamış / değeri boş olanı atla
        if i < 0 or g[i] <= np.datetime64(alt):
            continue
        tah, ger = olay["tahmin"].iat[i], olay["gercek"].iat[i]
        f = fiyat.get(s)
        if pd.notna(tah) and pd.notna(f) and f > 0:
            out[s] = (ger - tah) / f
    return pd.Series(out, dtype=float).clip(-0.5, 0.5)


def degerler(F: dict | None, K: dict | None, fiyat: pd.Series, t: pd.Timestamp) -> pd.DataFrame:
    """model_degerleri için: sembol başına aciga_satis ve kazanc_surprizi ham değerleri."""
    df = pd.DataFrame(index=fiyat.index)
    df["aciga_satis"] = aciga_oran(F, t).reindex(df.index) if F else np.nan
    df["kazanc_surprizi"] = sue(K, fiyat, t).reindex(df.index) if K else np.nan
    return df


def tohum(kaynak: Path, hedef: Path) -> None:
    """Araştırma verisinden (arastirma_veri.py çıktısı) ilk dosyaları üretir:
    yahoo_analist.pkl.gz -> kazanc_abd.pkl, finra_aciga_satis.pkl.gz -> finra_abd.pkl"""
    hedef.mkdir(parents=True, exist_ok=True)
    y = kaynak / "yahoo_analist.pkl.gz"
    if y.exists() and not (hedef / "kazanc_abd.pkl").exists():
        Y = pd.read_pickle(y)
        K = {"olaylar": {}, "guncelleme": {}}
        zaman = pd.Timestamp(datetime.fromtimestamp(y.stat().st_mtime).date())
        for s, v in Y.items():
            k = v.get("kazanc")
            if k is not None and len(k):
                K["olaylar"][s] = kazanc_bicimle(k)
                K["guncelleme"][s] = zaman
        (hedef / "kazanc_abd.pkl").write_bytes(pickle.dumps(K, protocol=pickle.HIGHEST_PROTOCOL))
        yaz(f"tohum: kazanc_abd.pkl ({len(K['olaylar'])} hisse)")
    f = kaynak / "finra_aciga_satis.pkl.gz"
    if f.exists() and not (hedef / "finra_abd.pkl").exists():
        F = pd.read_pickle(f)
        gecmis = hedef / "gecmis_abd.pkl"
        if gecmis.exists():
            sem = sorted(set(pd.read_pickle(gecmis)["Close"].columns) & set(F["kisa"].columns))
            F = {k: v[sem] for k, v in F.items()}
        for k in F:
            F[k].index = pd.to_datetime(F[k].index)
        (hedef / "finra_abd.pkl").write_bytes(pickle.dumps(F, protocol=pickle.HIGHEST_PROTOCOL))
        yaz(f"tohum: finra_abd.pkl {F['kisa'].shape}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 4 and sys.argv[1] == "tohum":
        tohum(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        sys.exit("kullanım: python duygu.py tohum <kaynak_klasör> <veri_klasörü>")
