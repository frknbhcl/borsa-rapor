# Borsa Raporları

Borsa İstanbul ve ABD borsalarındaki tüm hisseler için günlük, etkileşimli rapor.
GitHub Actions her hafta içi raporu otomatik üretir ve GitHub Pages'te yayınlar.

- **BIST:** hafta içi 18:40 (TR); yedek 20:40, 22:40
- **ABD:** hafta içi gece 00:40 (TR); yedek 02:40, 05:40, 07:40
- GitHub zamanlanmış çalıştırmaları geciktirebildiği için yedek saatler var; o günün kapanış raporu yayındaysa yedek çalıştırma hiçbir şey yapmaz.
- **Elle çalıştırma:** Actions → "Günlük rapor" → Run workflow

Raporlar sitenin ana sayfasında; `bist.html` ve `abd.html` her zaman en son rapor, `skor_bist.html` ve `skor_abd.html` en son skor sayfasıdır.

## Skor sayfası

Her hisse için akademik çalışmalara dayanan 18 model puanı (0-100) hesaplanır:
- **Kısa vade:** haftalık dönüş (Lehmann 1990), aylık dönüş (Jegadeesh 1990), hacim primi (Gervais, Kaniel & Mingelgrin 2001), piyango etkisi/MAX (Bali, Cakici & Whitelaw 2011), düşük çarpıklık (Amaya vd. 2015)
- **Orta vade:** 52 hafta zirvesi (George & Hwang 2004), 12-1 momentum (Jegadeesh & Titman 1993), 200 gün trendi (Moskowitz, Ooi & Pedersen 2012), kalıntı momentum (Blitz, Huij & Martens 2011), sürekli momentum (Da, Gurun & Warachka 2014)
- **Uzun vade:** düşük oynaklık (Blitz & van Vliet 2007), düşük beta (Frazzini & Pedersen 2014), düşük özgün oynaklık (Ang vd. 2006), düşük korelasyon (Asness, Frazzini, Gormsen & Pedersen 2020), illikidite primi (Amihud 2002), değer E/P ve B/M (Basu 1977; Fama & French 1992), kârlılık ROE (Hou, Xue & Zhang 2015)

Skor ve geriye dönük test sayfalarının başındaki **Öneri** kutusu, orta vade (3 ay tutma) için kombinasyon taramasında en sağlam çıkan model setini tek dokunuşla seçer. Öneriler `oneri.py` dosyasındadır: BIST'te 52 hafta zirvesi + Momentum + Düşük oynaklık; ABD'de zayıf sinyal olarak 52 hafta zirvesi + Sürekli momentum + Düşük özgün oynaklık.

**Duygu modelleri (yalnızca ABD, `duygu.py`):** açığa satış oranı (FINRA'nın günlük açığa satış hacmi ÷ toplam hacim, son 20 gün; Wang, Yan & Zheng 2020) ve kazanç sürprizi (son açıklanan hisse başı kâr − analist beklentisi, fiyata bölünmüş; Bernard & Thomas 1989). Açığa satış verisi her gün FINRA'dan, kazanç takvimi Yahoo'dan gelir. Geçmişleri `veri-arsivi` sürümünde (`finra_abd.pkl.gz`, `kazanc_abd.pkl.gz`) tutulur ve cumartesi günleri güncellenir.

**Araştırma verisi (`arastirma_veri.py`, "Araştırma verisi" iş akışı, elle çalıştırılır):** yeni model denemeleri için FINRA, Yahoo analist notları/kazanç takvimi ve Nasdaq sektör listesini toplayıp `arastirma-verisi` sürümüne yükler. SEC EDGAR, GitHub sunucularının IP adreslerini engellediği için oradan çalışmıyor.

Puan, hissenin o modeldeki değerinin aynı borsadaki likit hisseler arasındaki sırasıdır. Sayfada modeller seçilir; toplam puan seçili modellerin ortalamasıdır. Seçim adres çubuğunda (`#m=...`) saklanır. Temel veriler (E/P, B/M, ROE) Yahoo hisse tarayıcısından gelir; bir borsada yeterli veri yoksa ilgili model devre dışı görünür.

## Dosyalar

- `gunluk_rapor.py`: veriyi indirir, göstergeleri hesaplar, HTML ve CSV rapor üretir. Bilgisayarda da çalışır: `python gunluk_rapor.py bist`
- `skor_sayfasi.py`: etkileşimli skor sayfası
- `dogrula.py`: bağımsız doğrulama; her çalıştırmada örnek hisselerin göstergelerini sıfırdan yeniden hesaplar ve fiyatları Yahoo dışı kaynaklarla (ABD: Stooq/Nasdaq, BIST: İş Yatırım) karşılaştırır. Sonuç sitede "Doğrulama" sayfasında. Testler: `python -m unittest test_dogrula`
- `geriye_test.py` + `geriye_test_sablon.html`: geriye dönük test; her cumartesi ~6 yıllık geçmişle, her ay sonu modellerin o günkü puanını (ileriye bakmadan) hesaplar ve sonraki 1/3/6/12 aylık getirilerle karşılaştırır. Sayfada model seçip kombinasyonun geçmişi incelenir. Sınırlar: hayatta kalma yanlılığı (yalnızca bugün işlem gören hisseler), temel veri modelleri test edilemez.
- `site_olustur.py`: raporları siteye ekler, 45 günden eski olanları siler, ana sayfayı üretir
- `.github/workflows/rapor.yml`: zamanlama ve yayınlama

Uzun fiyat geçmişi (≈6 yıl) kalıcı olarak GitHub'da **"veri-arsivi"** sürümünde (Releases) saklanır ve her cumartesi güncellenir: `kapanis_bist.csv.gz`, `kapanis_abd.csv.gz` (düzeltilmiş kapanışlar), `hacim_*.csv.gz` ve betiklerin kullandığı `gecmis_*.pkl.gz`. Önbellek silinse bile geçmiş baştan indirilmez, arşivden alınır (`arsiv.py`).

Günlük fiyat önbelleği Actions cache'inde tutulur. İlk çalıştırma 1 yıllık geçmişi indirir, sonrakiler yalnızca son günleri indirir.

Veri kaynağı Yahoo Finance'tir; veriler hatalı olabilir. Yatırım tavsiyesi değildir.
