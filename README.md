# Borsa Raporları

Borsa İstanbul ve ABD borsalarındaki tüm hisseler için günlük, etkileşimli rapor.
GitHub Actions her hafta içi raporu otomatik üretir ve GitHub Pages'te yayınlar.

- **BIST:** hafta içi 18:40 (TR)
- **ABD:** hafta içi gece 00:40 (TR)
- **Elle çalıştırma:** Actions → "Günlük rapor" → Run workflow

Raporlar sitenin ana sayfasında; `bist.html` ve `abd.html` her zaman en son rapor, `skor_bist.html` ve `skor_abd.html` en son skor sayfasıdır.

## Skor sayfası

Her hisse için akademik çalışmalara dayanan 10 model puanı (0-100) hesaplanır: kısa vade (haftalık/aylık dönüş, hacim primi, MAX etkisi), orta vade (52 hafta zirvesi, 12-1 momentum, 200 gün trendi), uzun vade (düşük oynaklık, düşük beta, düşük özgün oynaklık). Puan, hissenin o modeldeki değerinin aynı borsadaki likit hisseler arasındaki sırasıdır. Sayfada modeller seçilir; toplam puan seçili modellerin ortalamasıdır. Seçim adres çubuğunda (`#m=...`) saklanır.

## Dosyalar

- `gunluk_rapor.py`: veriyi indirir, göstergeleri hesaplar, HTML ve CSV rapor üretir. Bilgisayarda da çalışır: `python gunluk_rapor.py bist`
- `skor_sayfasi.py`: etkileşimli skor sayfası
- `site_olustur.py`: raporları siteye ekler, 45 günden eski olanları siler, ana sayfayı üretir
- `.github/workflows/rapor.yml`: zamanlama ve yayınlama

Fiyat önbelleği Actions cache'inde tutulur. İlk çalıştırma 1 yıllık geçmişi indirir, sonrakiler yalnızca son günleri indirir.

Veri kaynağı Yahoo Finance'tir; veriler hatalı olabilir. Yatırım tavsiyesi değildir.
