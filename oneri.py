"""Orta vade için önerilen model kombinasyonları (skor ve geriye dönük test sayfalarında "Öneri").

Kaynak: 22.10.2026 tarihli kombinasyon taraması. 5 orta vade modelinin bütün alt kümeleri, isteğe
bağlı bir risk modeliyle birlikte (155 kombinasyon) 3 ay tutmayla denendi. Seçim dönemin ilk
yarısında (2021-11 – 2024-02) yapıldı, ikinci yarı (2024-03 – 2026-07) örneklem dışı kontrol oldu.
Rakamlar maliyetsiz; en iyi %20'nin evrene göre yıllık farkıdır.

ABD önerisi aynı gün yeni duygu modelleriyle (duygu.py: açığa satış oranı, kazanç sürprizi) yeniden
tarandı: 13 modelden 4'e kadar bütün kombinasyonlar (1.092), aynı ilk yarı / ikinci yarı ayrımıyla.
"""

ONERI = {
    "bist": {
        "modeller": ["zirve52", "momentum", "dusuk_oyn"],
        "ufuk": "3",
        "baslik": "52 hafta zirvesi + Momentum + Düşük oynaklık",
        "guven": "güçlü",
        "ozet": ("3 ay tutmada IC 0,151 (t 6,0). Evrene göre yılda +%17 ile +%25 arası, portföyün hangi ay "
                 "kurulduğuna göre değişiyor. Dönemin iki yarısında da tutarlı. Eylül 2026 fon krizinde evren −%30 iken en iyi %20 evreni +%10,6 geçti; "
                 "düşük oynaklık şişirilmiş hisseleri eledi. Yalnız momentum bu ayda −%15,7 geride kaldı."),
    },
    "abd": {
        "modeller": ["surekli_mom", "dusuk_ioyn", "aciga_satis", "kazanc_surprizi"],
        "ufuk": "3",
        "baslik": "Sürekli momentum + Düşük özgün oynaklık + Açığa satış + Kazanç sürprizi",
        "guven": "orta",
        "ozet": ("Fiyat modelleri ABD'de tek başına zayıf kaldı; iki duygu modeli (FINRA açığa satış oranı ve "
                 "analist beklentisine göre kazanç sürprizi) eklenince 3 ay tutmada IC 0,086 (t 3,0). En iyi %20 "
                 "evreni yılda +%6 ile +%7 arası geçti (önceki öneride %0 ile +%2); dönemin iki yarısında da "
                 "tutarlı. Duygu modelleri fiyat modelleriyle neredeyse ilişkisiz, bu yüzden birlikte işe yarıyor."),
    },
}
TARAMA_TARIHI = "22.10.2026"
GUVEN_ETIKET = {"güçlü": "sağlam sinyal", "orta": "orta güçte sinyal", "zayıf": "zayıf sinyal"}


def oneri_json(kod: str) -> str:
    """Sayfalara gömülen JSON (oneriVeri)."""
    import json
    o = ONERI.get(kod)
    if not o:
        return "null"
    sinif = {"güçlü": "guclu", "orta": "orta", "zayıf": "zayif"}[o["guven"]]
    return json.dumps({**o, "tarih": TARAMA_TARIHI, "etiket": GUVEN_ETIKET[o["guven"]], "sinif": sinif},
                      ensure_ascii=False).replace("</", "<\\/")
