#!/usr/bin/env python3
"""
Sanal borsa için izleme fiyatları.

portfoy dalındaki hesap.json'dan izleme listesindeki ve elde tutulan hisseleri okur; her biri için
son fiyatı, bugünün 5 dakikalık seyrini ve 1 yıllık günlük kapanışları Yahoo'dan alıp izleme.json'a
yazar. Sanal borsa sayfası (sanal.html) grafikleri ve işlem fiyatlarını buradan okur.

Kullanım:
    python izleme.py <hesap.json> <izleme.json>
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

SAAT = {"bist": ZoneInfo("Europe/Istanbul"), "abd": ZoneInfo("America/New_York")}
EK = {"bist": ".IS", "abd": ""}


def semboller(hesap: dict, kod: str) -> list[str]:
    h = hesap.get(kod) or {}
    s = set(h.get("izleme") or []) | set((h.get("poz") or {}).keys())
    return sorted(x for x in s if isinstance(x, str) and 0 < len(x) <= 12 and x.replace("-", "").replace(".", "").isalnum())


def _yuvarla(x: float) -> float:
    return float(f"{x:.6g}")


def tek(kod: str, sym: str) -> dict | None:
    t = yf.Ticker(sym + EK[kod])
    for deneme in range(3):
        try:
            g = t.history(period="1y", interval="1d", auto_adjust=False, actions=False)
            if g is None or g.empty or "Close" not in g:
                return None
            g = g["Close"].dropna()
            idx = g.index.tz_convert(SAAT[kod]) if g.index.tz is not None else g.index
            gecmis = [[d.strftime("%Y-%m-%d"), _yuvarla(v)] for d, v in zip(idx, g.to_numpy())]
            gun = []
            try:
                i = t.history(period="1d", interval="5m", auto_adjust=False, actions=False, prepost=False)
                if i is not None and not i.empty:
                    ii = i["Close"].dropna()
                    ix = ii.index.tz_convert(SAAT[kod])
                    gun = [[d.strftime("%H:%M"), _yuvarla(v)] for d, v in zip(ix, ii.to_numpy())]
                    gun_tarih = ix[-1].strftime("%Y-%m-%d")
                    # Gün içi veri bugünkü günlük kapanıştan yeniyse son fiyat odur
                    if gecmis and gecmis[-1][0] == gun_tarih:
                        gecmis[-1][1] = gun[-1][1]
                    elif gecmis and gecmis[-1][0] < gun_tarih:
                        gecmis.append([gun_tarih, gun[-1][1]])
                    zaman = ix[-1].strftime("%Y-%m-%d %H:%M")
                else:
                    zaman = gecmis[-1][0] if gecmis else None
            except Exception:  # noqa: BLE001
                zaman = gecmis[-1][0] if gecmis else None
            if not gecmis:
                return None
            return {"son": gecmis[-1][1], "onceki": gecmis[-2][1] if len(gecmis) > 1 else None,
                    "son_gun": gecmis[-1][0], "zaman": zaman, "gun": gun, "gecmis": gecmis}
        except Exception as e:  # noqa: BLE001
            print(f"  {sym}: {e!r}", flush=True)
            time.sleep(3 * (deneme + 1))
    return None


def main() -> None:
    hesap_yol, cikti = Path(sys.argv[1]), Path(sys.argv[2])
    hesap = json.loads(hesap_yol.read_text(encoding="utf-8")) if hesap_yol.exists() else {}
    eski = {}
    if cikti.exists():
        try:
            eski = json.loads(cikti.read_text(encoding="utf-8"))
        except ValueError:
            eski = {}
    out = {"guncelleme": datetime.now(ZoneInfo("Europe/Istanbul")).strftime("%Y-%m-%d %H:%M")}
    for kod in ("bist", "abd"):
        out[kod] = {}
        for sym in semboller(hesap, kod):
            v = tek(kod, sym)
            if v is None and sym in (eski.get(kod) or {}):
                v = eski[kod][sym]          # geçici hata: eski veriyi koru
            if v is not None:
                out[kod][sym] = v
        print(f"{kod}: {len(out[kod])} hisse", flush=True)
    cikti.parent.mkdir(parents=True, exist_ok=True)
    cikti.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    main()
