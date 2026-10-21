#!/usr/bin/env python3
"""Yeni raporları web sitesi klasörüne kopyalar, eskileri temizler ve ana sayfayı üretir.

Kullanım: python site_olustur.py <raporlar_klasoru> <site_klasoru>

Site yapısı:
    index.html          ana sayfa (son raporlar + arşiv)
    bist.html, abd.html her zaman en son rapor (telefonda yer imi için sabit adres)
    raporlar/           tarihli raporlar ve CSV dosyaları
"""

from __future__ import annotations

import html
import re
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SAKLAMA_GUN = 45
AD = {"BIST": "Borsa İstanbul", "ABD": "ABD Borsaları"}
KALIP = re.compile(r"^(\d{4}-\d{2}-\d{2})_(BIST|ABD)\.html$")
SKOR_KALIP = re.compile(r"^(\d{4}-\d{2}-\d{2})_(BIST|ABD)_skor\.html$")
AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
         "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
GUNLER = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]

CSS = """
:root{--bg:#f6f7f9;--kart:#fff;--yazi:#1c2128;--soluk:#5b6470;--cizgi:#e3e6ea;--vurgu:#0b6bcb}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0f1216;--kart:#171b21;
--yazi:#e6e9ee;--soluk:#9aa4b1;--cizgi:#2a3039;--vurgu:#5aa9ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--yazi);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:720px;margin:0 auto;padding:28px 16px 56px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 10px;color:var(--soluk);font-weight:600}
.alt{color:var(--soluk);font-size:13px}
.kartlar{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:20px}
@media (max-width:480px){.kartlar{grid-template-columns:1fr}}
.kart{display:block;background:var(--kart);border:1px solid var(--cizgi);border-radius:12px;padding:18px}
.kart b{display:block;font-size:18px;margin-bottom:4px}
.kart .linkler{display:flex;flex-wrap:wrap;gap:8px 18px;margin-top:12px}
.kart .ok{color:var(--vurgu);font-weight:600;font-size:15px;text-decoration:none}
.kart .ok.ikincil{font-weight:500}
.bos{background:var(--kart);border:1px dashed var(--cizgi);border-radius:12px;padding:18px;color:var(--soluk)}
ul{list-style:none;padding:0;margin:0;background:var(--kart);border:1px solid var(--cizgi);border-radius:12px}
li{padding:11px 14px;border-bottom:1px solid var(--cizgi)}
li:last-child{border-bottom:0}
li .tarih{display:block;font-weight:600;margin-bottom:4px}
li .piyasa{display:flex;gap:14px;font-size:14px;margin-top:2px}
li .piyasa span{color:var(--soluk);width:36px}
li a{color:var(--vurgu);text-decoration:none}
footer{color:var(--soluk);font-size:12px;margin-top:32px}
"""

GERI_CSS = ("<style>.geri{display:inline-block;margin:0 0 12px;color:var(--vurgu);"
            "text-decoration:none;font-size:14px}</style>")


def tarih_metni(t: datetime) -> str:
    return f"{t.day} {AYLAR[t.month - 1]} {t.year}, {GUNLER[t.weekday()]}"


def geri_linki_ekle(icerik: str, hedef: str, skor: str | None = None) -> str:
    if 'class="geri"' in icerik:
        return icerik
    skor_linki = (f'<a class="geri" style="float:right" href="{skor}">Skorlar →</a>' if skor else "")
    return icerik.replace("<main>", f'<main>{GERI_CSS}{skor_linki}<a class="geri" href="{hedef}">← Tüm raporlar</a>', 1)


def main() -> None:
    kaynak, site = Path(sys.argv[1]), Path(sys.argv[2])
    arsiv = site / "raporlar"
    arsiv.mkdir(parents=True, exist_ok=True)

    # 1) Yeni raporları kopyala (geri linkiyle)
    if kaynak.exists():
        for dosya in kaynak.iterdir():
            if KALIP.match(dosya.name):
                skor = dosya.name.replace(".html", "_skor.html")
                icerik = geri_linki_ekle(dosya.read_text(encoding="utf-8"), "../index.html",
                                         skor if (kaynak / skor).exists() else None)
                (arsiv / dosya.name).write_text(icerik, encoding="utf-8")
            elif (SKOR_KALIP.match(dosya.name) or dosya.name.endswith("_tum.csv")
                  or dosya.name.endswith("_dogrulama.html") or dosya.name.endswith("_dogrulama.json")):
                shutil.copy2(dosya, arsiv / dosya.name)

    # 2) Eski raporları temizle
    sinir = datetime.now() - timedelta(days=SAKLAMA_GUN)
    for dosya in arsiv.iterdir():
        m = re.match(r"^(\d{4}-\d{2}-\d{2})_", dosya.name)
        if m and datetime.strptime(m.group(1), "%Y-%m-%d") < sinir:
            dosya.unlink()

    # 3) Raporları listele
    raporlar: dict[str, list[tuple[datetime, str]]] = {"BIST": [], "ABD": []}
    for dosya in arsiv.iterdir():
        m = KALIP.match(dosya.name)
        if m:
            raporlar[m.group(2)].append((datetime.strptime(m.group(1), "%Y-%m-%d"), dosya.name))
    for liste in raporlar.values():
        liste.sort(reverse=True)

    # 4) Sabit adresler: bist.html ve abd.html her zaman en son rapor
    kartlar = []
    for kod in ("BIST", "ABD"):
        k = kod.lower()
        if raporlar[kod]:
            tarih, ad = raporlar[kod][0]
            skor_ad = ad.replace(".html", "_skor.html")
            icerik = (arsiv / ad).read_text(encoding="utf-8").replace('href="../index.html"', 'href="index.html"')
            icerik = icerik.replace(f'href="{skor_ad}"', f'href="skor_{k}.html"')
            (site / f"{k}.html").write_text(icerik, encoding="utf-8")
            skor_link = ""
            if (arsiv / skor_ad).exists():
                skor = (arsiv / skor_ad).read_text(encoding="utf-8")
                skor = skor.replace('href="../index.html"', 'href="index.html"').replace(f'href="{ad}"', f'href="{k}.html"')
                (site / f"skor_{k}.html").write_text(skor, encoding="utf-8")
                skor_link = f'<a class="ok" href="skor_{k}.html">Skorlar →</a>'
            dogrulama_ad = ad.replace(".html", "_dogrulama.html")
            if (arsiv / dogrulama_ad).exists():
                shutil.copy2(arsiv / dogrulama_ad, site / f"dogrulama_{k}.html")
                skor_link += f'<a class="ok ikincil" href="dogrulama_{k}.html">Doğrulama</a>'
            kartlar.append(
                f'<div class="kart"><b>{AD[kod]}</b><span class="alt">{tarih_metni(tarih)}</span>'
                f'<span class="linkler">{skor_link}<a class="ok ikincil" href="{k}.html">Günlük rapor</a></span></div>')
        else:
            kartlar.append(f'<div class="bos"><b>{AD[kod]}</b><br>Henüz rapor yok.</div>')

    # 5) Arşiv (tarihe göre, her satırda BIST / ABD linkleri)
    gunler: dict[datetime, dict[str, str]] = {}
    for kod, liste in raporlar.items():
        for tarih, ad in liste:
            gunler.setdefault(tarih, {})[kod] = ad
    satirlar = []
    for tarih in sorted(gunler, reverse=True):
        piyasalar = []
        for k in ("BIST", "ABD"):
            if k not in gunler[tarih]:
                continue
            ad = gunler[tarih][k]
            linkler = []
            skor = ad.replace(".html", "_skor.html")
            if (arsiv / skor).exists():
                linkler.append(f'<a href="raporlar/{html.escape(skor)}">Skorlar</a>')
            linkler.append(f'<a href="raporlar/{html.escape(ad)}">Rapor</a>')
            dog = ad.replace(".html", "_dogrulama.html")
            if (arsiv / dog).exists():
                linkler.append(f'<a href="raporlar/{html.escape(dog)}">Doğrulama</a>')
            csv = ad.replace(".html", "_tum.csv")
            if (arsiv / csv).exists():
                linkler.append(f'<a href="raporlar/{html.escape(csv)}" title="{k} tüm hisseler (Excel)">CSV</a>')
            piyasalar.append(f'<div class="piyasa"><span>{k}</span>{"".join(linkler)}</div>')
        satirlar.append(f'<li><span class="tarih">{tarih_metni(tarih)}</span>{"".join(piyasalar)}</li>')
    arsiv_html = f"<ul>{''.join(satirlar)}</ul>" if satirlar else '<div class="bos">Arşiv boş.</div>'

    simdi = datetime.now(ZoneInfo("Europe/Istanbul")).strftime("%d.%m.%Y %H:%M")
    (site / "index.html").write_text(f"""<!doctype html>
<html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>Borsa Raporları</title><style>{CSS}</style></head>
<body><main>
<h1>Borsa Raporları</h1>
<div class="alt">Son güncelleme: {simdi} (TR)</div>
<div class="kartlar">{''.join(kartlar)}</div>
<h2>Arşiv (son {SAKLAMA_GUN} gün)</h2>
{arsiv_html}
<footer>Raporlar hafta içi her gün otomatik oluşur: BIST 18:40, ABD gece 00:40 civarı.
Veri kaynağı Yahoo Finance; yatırım tavsiyesi değildir.</footer>
</main></body></html>""", encoding="utf-8")
    (site / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Site hazır: {sum(len(v) for v in raporlar.values())} rapor, {len(gunler)} gün.")


if __name__ == "__main__":
    main()
