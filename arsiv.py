#!/usr/bin/env python3
"""Uzun fiyat geçmişini kalıcı arşiv için dışa aktarır.

    python arsiv.py veri arsiv

veri/gecmis_<kod>.pkl dosyalarından şunları üretir:
- arsiv/gecmis_<kod>.pkl.gz  : betiklerin geri yüklediği önbellek (sıkıştırılmış)
- arsiv/kapanis_<kod>.csv.gz : günlük düzeltilmiş kapanışlar (satır = tarih, sütun = hisse), Excel/pandas ile açılır
- arsiv/hacim_<kod>.csv.gz   : günlük işlem adedi
- arsiv/finra_abd.pkl.gz, arsiv/kazanc_abd.pkl.gz : ABD duygu modelleri için açığa satış ve kazanç geçmişi
"""
import gzip
import shutil
import sys
from pathlib import Path

import pandas as pd

kaynak, hedef = Path(sys.argv[1]), Path(sys.argv[2])
hedef.mkdir(parents=True, exist_ok=True)
for yol in sorted(kaynak.glob("gecmis_*.pkl")):
    kod = yol.stem.removeprefix("gecmis_")
    with open(yol, "rb") as f, gzip.open(hedef / f"{yol.name}.gz", "wb", compresslevel=6) as g:
        shutil.copyfileobj(f, g)
    veri = pd.read_pickle(yol)
    for alan, ad in (("Close", "kapanis"), ("Volume", "hacim")):
        if alan in veri:
            df = veri[alan]
            df.columns = [c.removesuffix(".IS") for c in df.columns]
            df.index.name = "tarih"
            df.round(4).to_csv(hedef / f"{ad}_{kod}.csv.gz", compression="gzip")
    print(f"{kod}: {veri['Close'].shape[1]} hisse, {veri['Close'].shape[0]} gün "
          f"({veri['Close'].index.min():%Y-%m-%d} – {veri['Close'].index.max():%Y-%m-%d})")

for ad in ("finra_abd.pkl", "kazanc_abd.pkl"):
    yol = kaynak / ad
    if yol.exists():
        with open(yol, "rb") as f, gzip.open(hedef / f"{ad}.gz", "wb", compresslevel=6) as g:
            shutil.copyfileobj(f, g)
        print(f"{ad}: arşivlendi")
