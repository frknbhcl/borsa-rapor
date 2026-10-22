#!/usr/bin/env python3
"""
Geriye dönük test: skor modellerinin geçmişte ne kadar işe yaradığını ölçer.

Her ay sonunda, o güne kadarki veriyle (ileriye bakmadan) her modelin puanı hesaplanır,
ardından hisselerin sonraki 1, 3, 6 ve 12 aylık getirileri ölçülür. Sonuçlar etkileşimli bir
sayfaya yazılır; sayfada model seçilip kombinasyonun geçmişi incelenebilir.

Kullanım:
    python geriye_test.py bist          # ya da abd / hepsi
    python geriye_test.py bist --tam    # geçmişi baştan indir

Sınırlar (sayfada da yazılır):
- Hayatta kalma yanlılığı: yalnızca bugün işlem gören hisseler var; geçmişte borsadan
  çıkanlar yok. Bu, özellikle küçük ve zayıf hisselerde sonuçları olduğundan iyi gösterir.
- Temel veri modelleri (F/K, PD/DD, ROE) test edilemez: geçmişe ait temel veri yok.
- Getiriler brüttür; işlem maliyeti sayfada isteğe bağlı düşülür. Vergi yok.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import gunluk_rapor as g

GECMIS_GUN = 6 * 365 + 40          # ~6 yıl: ilk 13 ay modellerin ısınması, kalan ~5 yıl test
EVREN = {"bist": 300, "abd": 1000}  # her ay ortalama işlem hacmine göre en likit N hisse
MIN_FIYAT = {"bist": 0.0, "abd": 2.0}
UFUKLAR = [1, 3, 6, 12]             # ay
PENCERE = 300                       # model hesabı için geriye bakılan işlem günü
TEMEL_MODELLER = {"deger_fk", "deger_pddd", "karlilik"}
# Kısa vade sinyalleri birkaç günde tükenir; bunlar ayrıca HAFTALIK yenilenen bir testle de ölçülür.
HAFTA_MODELLER = ["hft_donus", "ay_donus", "hacim_primi", "max_etkisi", "carpiklik"]
HAFTA_PENCERE = 70
EVREN_HAFTA = {"bist": 300, "abd": 500}
KLASOR = Path(__file__).resolve().parent
CIKTI = KLASOR / "geriye_test"


def yaz(*a):
    print(*a, flush=True)


def ay_sonlari(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    s = pd.Series(index, index=index)
    return list(s.groupby([index.year, index.month]).max())


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    m = ~(np.isnan(a) | np.isnan(b))
    if m.sum() < 20:
        return float("nan")
    ra = pd.Series(a[m]).rank().to_numpy()
    rb = pd.Series(b[m]).rank().to_numpy()
    if ra.std() == 0 or rb.std() == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def hafta_sonlari(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    iso = index.isocalendar()
    s = pd.Series(index, index=index)
    return list(s.groupby([iso.year.to_numpy(), iso.week.to_numpy()]).max().sort_values())


def cerceve(veri: dict) -> dict:
    c = veri["Close"].astype(float)
    h = veri["High"].astype(float)
    v = veri["Volume"].astype(float)
    # Piyasa günleri: o dönemde işlem gören hisselerin en az yarısının verisi olan günler
    sayim = c.notna().sum(axis=1)
    gunler = sayim[sayim >= 0.5 * sayim.rolling(60, min_periods=1).max()].index
    c, h, v = c.loc[gunler], h.loc[gunler], v.loc[gunler]
    return {"c": c, "h": h, "v": v, "getiri": c / c.shift(1) - 1,
            "c_dolu": c.ffill(limit=10)}  # kısa işlem durdurmalarında son fiyat


def evren_sec(kod: str, cw: pd.DataFrame, vw: pd.DataFrame, adet: int) -> pd.Index:
    son = cw.iloc[-1]
    islem = (cw * vw).iloc[-20:].mean()
    gecerli = son.notna() & (son >= MIN_FIYAT[kod]) & (cw.iloc[-60:].notna().sum() >= 50) & (islem > 0)
    return islem[gecerli].nlargest(adet).index


def haftalik(kod: str, f: dict) -> list[dict]:
    """Kısa vade modelleri için haftalık yenilenen test: her hafta sonu puan, sonraki 1 hafta getiri."""
    c, h, v, getiri, c_dolu = f["c"], f["h"], f["v"], f["getiri"], f["c_dolu"]
    modeller = [m for m in g.MODELLER if m["id"] in HAFTA_MODELLER]
    tarihler = [t for t in hafta_sonlari(c.index) if c.index.get_loc(t) >= HAFTA_PENCERE]
    haftalar = []
    for i, t in enumerate(tarihler[:-1]):
        pos = c.index.get_loc(t)
        bas = pos - HAFTA_PENCERE + 1
        cw, vw = c.iloc[bas: pos + 1], v.iloc[bas: pos + 1]
        evren = evren_sec(kod, cw, vw, EVREN_HAFTA[kod])
        if len(evren) < 30:
            continue
        ham = g.model_degerleri(cw[evren], h.iloc[bas: pos + 1][evren], vw[evren],
                                getiri.iloc[bas: pos + 1][evren], None)
        tum = pd.Series(True, index=evren)
        puan = {md["id"]: g.puanla(ham[md["id"]], md["yon"], tum) for md in modeller}
        t2 = tarihler[i + 1]
        r = c_dolu.loc[t2, evren] / cw.iloc[-1][evren] - 1
        if kod == "bist":
            p2 = c.index.get_loc(t2)
            r = r.where(~(getiri.iloc[pos + 1: p2 + 1][evren].min() < -0.35))
        haftalar.append({"tarih": t, "evren": list(evren), "puan": puan, "ileri": r})
    yaz(f"  {len(haftalar)} hafta (kısa vade, haftalık yenileme)")
    return haftalar


def hesapla(kod: str, veri: dict, endeks: pd.Series | None) -> dict:
    f = cerceve(veri)
    c, h, v, getiri, c_dolu = f["c"], f["h"], f["v"], f["getiri"], f["c_dolu"]

    modeller = [m for m in g.MODELLER if m["id"] not in TEMEL_MODELLER]
    tarihler = [t for t in ay_sonlari(c.index) if c.index.get_loc(t) >= PENCERE]
    if tarihler and tarihler[-1] == c.index[-1]:
        pass  # son ay sonu bugünse ileri getirisi olmaz; aşağıda NaN kalır
    yaz(f"  {len(tarihler)} ay sonu, {c.shape[1]} hisse")

    aylar = []
    for i, t in enumerate(tarihler):
        pos = c.index.get_loc(t)
        if i + 1 >= len(tarihler):
            break  # en az 1 aylık ileri getiri yoksa test dışı
        cw = c.iloc[pos - PENCERE + 1: pos + 1]
        vw = v.iloc[pos - PENCERE + 1: pos + 1]
        son = cw.iloc[-1]
        evren = evren_sec(kod, cw, vw, EVREN[kod])
        if len(evren) < 30:
            continue
        cw, hw, vw = cw[evren], h.iloc[pos - PENCERE + 1: pos + 1][evren], vw[evren]
        gw = getiri.iloc[pos - PENCERE + 1: pos + 1][evren]
        ham = g.model_degerleri(cw, hw, vw, gw, endeks)
        tum = pd.Series(True, index=evren)
        puan = {}
        for md in modeller:
            puan[md["id"]] = g.puanla(ham[md["id"]], md["yon"], tum)
        if "surekli_mom" in puan:
            puan["surekli_mom"] = ((puan["momentum"] + g.puanla(ham["surekli_mom"], -1, tum)) / 2).round()

        ileri = {}
        for k in UFUKLAR:
            if i + k < len(tarihler):
                t2 = tarihler[i + k]
                r = c_dolu.loc[t2, evren] / son[evren] - 1
                if kod == "bist":
                    # Düzeltilmemiş bölünme/bedelsiz (tek günde < −%35) içeren dönemleri dışarıda bırak
                    p2 = c.index.get_loc(t2)
                    en_kotu = getiri.iloc[pos + 1: p2 + 1][evren].min()
                    r = r.where(~(en_kotu < -0.35))
                ileri[k] = r
            else:
                ileri[k] = pd.Series(np.nan, index=evren)
        aylar.append({"tarih": t, "evren": list(evren), "puan": puan, "ileri": ileri})

    return {"modeller": modeller, "aylar": aylar, "haftalar": haftalik(kod, f)}


def istatistik(sonuc: dict) -> dict:
    """Model başına bilgi katsayısı (IC) ve en iyi/en kötü %20 farkı."""
    tablo = {}
    for md in sonuc["modeller"]:
        mid = md["id"]
        satir = {}
        for k in UFUKLAR:
            # Her ay başlayan (örtüşen) dönemler: daha çok veri. Örtüşme t'yi şişirmesin diye
            # etkin gözlem sayısı n/k alınır.
            ic = [spearman(ay["puan"][mid].to_numpy(float), ay["ileri"][k].to_numpy(float)) for ay in sonuc["aylar"]]
            ic = np.array([x for x in ic if not math.isnan(x)])
            n = len(ic)
            satir[f"ic{k}"] = float(ic.mean()) if n else None
            satir[f"t{k}"] = (float(ic.mean() / ic.std(ddof=1) * math.sqrt(n / k))
                              if n > 2 and ic.std(ddof=1) > 0 else None)
            satir[f"n{k}"] = n
        fark = []
        for ay in sonuc["aylar"]:
            p, r = ay["puan"][mid], ay["ileri"][1]
            ok = p.notna() & r.notna()
            if ok.sum() < 20:
                continue
            ust, alt = p[ok] >= p[ok].quantile(0.8), p[ok] <= p[ok].quantile(0.2)
            fark.append(r[ok][ust].mean() - r[ok][alt].mean())
        fark = np.array(fark)
        satir["fark_yillik"] = float(np.mean(fark) * 12) if len(fark) else None
        satir["isabet"] = float((fark > 0).mean()) if len(fark) else None
        if mid in HAFTA_MODELLER and sonuc.get("haftalar"):
            ic = np.array([x for x in (spearman(hf["puan"][mid].to_numpy(float), hf["ileri"].to_numpy(float))
                                       for hf in sonuc["haftalar"]) if not math.isnan(x)])
            satir["ic_h"] = float(ic.mean()) if len(ic) else None
            satir["t_h"] = float(ic.mean() / ic.std(ddof=1) * math.sqrt(len(ic))) if len(ic) > 2 else None
        tablo[mid] = satir
    return tablo


def json_veri(kod: str, ayar: dict, sonuc: dict, tablo: dict) -> dict:
    semboller = sorted({s for ay in sonuc["aylar"] for s in ay["evren"]})
    sira = {s: i for i, s in enumerate(semboller)}
    aylar = []
    for ay in sonuc["aylar"]:
        n = len(ay["evren"])
        puan = []
        for md in sonuc["modeller"]:
            p = ay["puan"][md["id"]].reindex(ay["evren"]).to_numpy(float)
            puan.append([-1 if math.isnan(x) else int(x) for x in p])
        ileri = []
        for k in UFUKLAR:
            r = ay["ileri"][k].reindex(ay["evren"]).to_numpy(float)
            ileri.append([None if math.isnan(x) else int(round(x * 10000)) for x in r])  # baz puan
        aylar.append({"t": ay["tarih"].strftime("%Y-%m-%d"), "s": [sira[s] for s in ay["evren"]],
                      "p": puan, "r": ileri, "n": n})
    return {
        "kod": kod, "ad": ayar["ad"], "para": ayar["para"], "ufuklar": UFUKLAR,
        "evren": EVREN[kod], "gruplar": g.GRUPLAR,
        "modeller": [{k: md[k] for k in ("id", "grup", "ad", "kaynak", "aciklama")} for md in sonuc["modeller"]],
        "tablo": tablo,
        "semboller": [s.removesuffix(".IS") for s in semboller],
        "aylar": aylar,
    }


def hafta_json(sonuc: dict) -> dict:
    hs = sonuc["haftalar"]
    semboller = sorted({s for hf in hs for s in hf["evren"]})
    sira = {s: i for i, s in enumerate(semboller)}
    out = []
    for hf in hs:
        p = [[-1 if math.isnan(x) else int(x) for x in hf["puan"][mid].reindex(hf["evren"]).to_numpy(float)]
             for mid in HAFTA_MODELLER]
        r = [None if math.isnan(x) else int(round(x * 10000)) for x in hf["ileri"].reindex(hf["evren"]).to_numpy(float)]
        out.append({"t": hf["tarih"].strftime("%Y-%m-%d"), "s": [sira[s] for s in hf["evren"]], "p": p, "r": r,
                    "n": len(hf["evren"])})
    return {"modeller": HAFTA_MODELLER, "semboller": semboller, "haftalar": out}


def sayfa(veri: dict) -> str:
    sablon = (KLASOR / "geriye_test_sablon.html").read_text(encoding="utf-8")
    js_veri = json.dumps(veri, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    ilk, son = veri["aylar"][0]["t"], veri["aylar"][-1]["t"]
    olusturma = datetime.now(ZoneInfo("Europe/Istanbul")).strftime("%d.%m.%Y %H:%M")
    return (sablon.replace("{{AD}}", html.escape(veri["ad"]))
            .replace("{{KOD}}", veri["kod"])
            .replace("{{DONEM}}", f"{ilk[:7]} – {son[:7]}")
            .replace("{{AY_SAYISI}}", str(len(veri["aylar"])))
            .replace("{{EVREN}}", str(veri["evren"]))
            .replace("{{OLUSTURMA}}", olusturma)
            .replace("{{VERI}}", js_veri))


def calistir(kod: str, tam: bool) -> Path:
    ayar = g.PIYASALAR[kod]
    basla = time.time()
    yaz(f"\n=== Geriye dönük test: {ayar['ad']} ===")
    liste = g.hisse_listesi(kod, ayar, False)
    veri = g.fiyatlari_guncelle(kod, liste["sembol"].tolist(), tam, gun=GECMIS_GUN,
                                dosya=f"gecmis_{kod}.pkl", alanlar=["Close", "High", "Volume"])
    endeks = g.endeksleri_indir(ayar, gun=GECMIS_GUN).get(ayar["endeks"][0])
    sonuc = hesapla(kod, veri, endeks)
    if not sonuc["aylar"]:
        sys.exit("Test için yeterli geçmiş yok.")
    tablo = istatistik(sonuc)
    v = json_veri(kod, ayar, sonuc, tablo)
    CIKTI.mkdir(parents=True, exist_ok=True)
    yol = CIKTI / f"geriye_test_{kod}.html"
    yol.write_text(sayfa(v), encoding="utf-8")
    (CIKTI / f"geriye_test_{kod}_hafta.json").write_text(
        json.dumps(hafta_json(sonuc), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (CIKTI / f"geriye_test_{kod}.json").write_text(
        json.dumps({"tablo": tablo, "aylar": len(v["aylar"]), "donem": [v["aylar"][0]["t"], v["aylar"][-1]["t"]]},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    yaz(f"  {len(v['aylar'])} ay test edildi · {time.time() - basla:.0f} sn · {yol}")
    for mid, s in sorted(tablo.items(), key=lambda x: -(x[1]["ic1"] or -9)):
        yaz(f"    {mid:14s} IC1 {s['ic1'] if s['ic1'] is None else round(s['ic1'], 3):>7}  "
            f"t {s['t1'] if s['t1'] is None else round(s['t1'], 1):>5}  "
            f"fark/yıl {s['fark_yillik'] if s['fark_yillik'] is None else round(s['fark_yillik'] * 100, 1):>6}%")
    return yol


def main():
    p = argparse.ArgumentParser(description="Skor modelleri için geriye dönük test")
    p.add_argument("piyasa", nargs="?", default="hepsi", choices=["hepsi", "bist", "abd"])
    p.add_argument("--tam", action="store_true")
    a = p.parse_args()
    g._dosya_sinirini_artir()
    for kod in (["bist", "abd"] if a.piyasa == "hepsi" else [a.piyasa]):
        calistir(kod, a.tam)


if __name__ == "__main__":
    main()
