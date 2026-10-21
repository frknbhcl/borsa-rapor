"""Birim testleri: python -m unittest test_dogrula -v"""
import json
import math
import unittest

import numpy as np
import pandas as pd

import dogrula as d

# StockCharts'ın klasik Wilder RSI örneği (ilk 20 kapanış)
WILDER = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03,
          45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64]


def rsi_elle(kapanis, n=14, sma_tohum=False):
    """Wilder yinelemesi, düz Python döngüsüyle (pandas yok)."""
    farklar = [kapanis[i] - kapanis[i - 1] for i in range(1, len(kapanis))]
    g = [max(x, 0.0) for x in farklar]
    l = [max(-x, 0.0) for x in farklar]
    if sma_tohum:      # klasik Wilder: ilk ortalama = ilk n değerin basit ortalaması
        ag, al = sum(g[:n]) / n, sum(l[:n]) / n
        bas = n
    else:              # ewm(alpha=1/n, adjust=False): ilk ortalama = ilk değer
        ag, al = g[0], l[0]
        bas = 1
    for i in range(bas, len(g)):
        ag = ag * (n - 1) / n + g[i] / n
        al = al * (n - 1) / n + l[i] / n
    return 100 - 100 / (1 + ag / al)


class RsiTest(unittest.TestCase):
    def test_klasik_wilder_ornegi(self):
        # 15 kapanış (14 fark), basit ortalama tohumlu klasik Wilder -> 70,46. StockCharts tablosu 70,53
        # gösterir (ara ortalamaları 2 basamağa yuvarladığı için). Döngünün Wilder'ı doğru uyguladığını sınar.
        self.assertAlmostEqual(rsi_elle(WILDER[:15], sma_tohum=True), 70.46, delta=0.1)

    def test_ewm_tanimi_elle_hesapla_ayni(self):
        for k in (15, 17, 20):
            self.assertAlmostEqual(d.rsi_hesapla(pd.Series(WILDER[:k])), rsi_elle(WILDER[:k]), places=9)

    def test_elle_kucuk_ornek(self):
        # 15 kapanış -> 14 fark: 13 kez +1, son fark -2. Elle: kazanç ort. 1 ile başlar ve 13 fark boyunca
        # (13/14)*1+1/14 = 1 kalır; son adımda 1*13/14 = 0,928571. Kayıp ort.: 0 -> 0*13/14+2/14 = 0,142857.
        # RSI = 100 - 100/(1+0,928571/0,142857) = 100 - 100/7,5 = 86,6667
        c = [10 + i for i in range(14)] + [21]
        self.assertAlmostEqual(d.rsi_hesapla(pd.Series(c, dtype=float)), 86.6667, places=3)

    def test_yetersiz_veri(self):
        self.assertTrue(math.isnan(d.rsi_hesapla(pd.Series(WILDER[:14]))))

    def test_uzun_seride_tohum_farki_ihmal_edilebilir(self):
        rng = np.random.default_rng(1)
        c = list(100 * np.cumprod(1 + rng.normal(0, 0.02, 270)))
        self.assertLess(abs(rsi_elle(c) - rsi_elle(c, sma_tohum=True)), 0.01)


class BetaKorelasyonTest(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(7)
        ri = rng.normal(0, 0.01, 252)
        rs = 1.3 * ri + rng.normal(0, 0.01, 252)
        rs[[3, 50, 51]] = np.nan
        ri[[10, 200]] = np.nan
        self.rs, self.ri = pd.Series(rs), pd.Series(ri)

    def test_beta_numpy_cov_var(self):
        m = self.rs.notna() & self.ri.notna()
        a, b = self.rs[m].to_numpy(), self.ri[m].to_numpy()
        beklenen = np.cov(a, b)[0, 1] / np.var(b, ddof=1)
        self.assertAlmostEqual(d.beta_hesapla(self.rs, self.ri), beklenen, places=12)

    def test_beta_az_cift(self):
        self.assertTrue(math.isnan(d.beta_hesapla(self.rs.iloc[:100], self.ri.iloc[:100])))

    def test_korelasyon_pandas_ile_ayni(self):
        self.assertAlmostEqual(d.korelasyon_hesapla(self.rs, self.ri), self.rs.corr(self.ri), places=12)


class GostergeTest(unittest.TestCase):
    def test_tanimlar(self):
        n = 260
        idx = pd.bdate_range("2025-01-01", periods=n)
        c = pd.Series(np.arange(1, n + 1, dtype=float), index=idx)
        df = pd.DataFrame({"Close": c, "High": c + 0.5, "Volume": np.r_[np.full(n - 5, 100.0), np.full(5, 300.0)]},
                          index=idx)
        o = d.gostergeleri_hesapla(df, c * 2)
        self.assertAlmostEqual(o["gunluk"], 260 / 259 - 1)
        self.assertAlmostEqual(o["hafta"], 260 / 255 - 1)
        self.assertAlmostEqual(o["ay"], 260 / 239 - 1)
        self.assertAlmostEqual(o["sma200_fark"], 260 / np.mean(np.arange(61, 261)) - 1)
        self.assertAlmostEqual(o["zirveden_uzaklik"], 260 / 260.5 - 1)
        self.assertAlmostEqual(o["h_hacim_primi"], 3.0)
        self.assertAlmostEqual(o["h_momentum"], 239 / 8 - 1)
        self.assertAlmostEqual(o["h_max_etkisi"], 240 / 239 - 1)
        self.assertAlmostEqual(o["korelasyon"], 1.0)
        self.assertAlmostEqual(o["h_dusuk_beta"], 1.0)


class AyristiriciTest(unittest.TestCase):
    def test_stooq(self):
        s, h = d.stooq_ayristir("Date,Open,High,Low,Close,Volume\n2026-10-19,1,1,1,10.5,1\n2026-10-20,1,1,1,11,1\n")
        self.assertIsNone(h)
        self.assertEqual(list(s), [10.5, 11.0])
        self.assertIsNone(d.stooq_ayristir("No data")[0])
        self.assertIn("HTML", d.stooq_ayristir("<html>apikey</html>")[1])

    def test_nasdaq(self):
        j = {"data": {"tradesTable": {"rows": [{"date": "10/20/2026", "close": "$1,234.50"},
                                               {"date": "10/19/2026", "close": "$1,200.00"}]}}}
        s, h = d.nasdaq_ayristir(json.dumps(j))
        self.assertEqual(s.index[-1], pd.Timestamp("2026-10-20"))
        self.assertEqual(s.iloc[-1], 1234.5)

    def test_isyatirim_alan_secimi_ve_hata(self):
        j = {"value": [{"HG_KAPANIS": 9, "HGDG_KAPANIS": 10.0, "END_TARIH": "01-01-2020", "HGDG_TARIH": "20-10-2026"}]}
        s, h, b = d.isyatirim_ayristir(json.dumps(j))
        self.assertEqual(b["kapanis_alani"], "HGDG_KAPANIS")
        self.assertEqual(b["tarih_alani"], "HGDG_TARIH")
        self.assertEqual(s.index[0], pd.Timestamp("2026-10-20"))
        s, h, b = d.isyatirim_ayristir(json.dumps({"value": [{"A": 1, "B": 2}]}))
        self.assertIsNone(s)
        self.assertEqual(b["alanlar"], ["A", "B"])
        self.assertEqual(d._tarih_coz("/Date(1760918400000)/"), pd.Timestamp("2025-10-20"))


class CaprazTest(unittest.TestCase):
    def test_uyumsuzluk(self):
        idx = pd.bdate_range("2026-09-01", periods=30)
        y = pd.Series(np.linspace(100, 110, 30), index=idx)
        self.assertEqual(d.capraz_karsilastir(y, y.drop(idx[5]))["durum"], "uyumlu")
        diger = y.copy()
        diger.iloc[-1] *= 1.006
        o = d.capraz_karsilastir(y, diger)
        self.assertEqual(o["durum"], "uyumsuz")
        self.assertAlmostEqual(o["son_fark"], 0.006, places=9)

    def test_bicim(self):
        self.assertEqual(d.tr_sayi(1234.5), "1.234,50")
        self.assertEqual(d.tr_yuzde(-0.0123), "−%1,23")
        self.assertEqual(d.tr_sayi(-0.001), "0,00")


if __name__ == "__main__":
    unittest.main()
