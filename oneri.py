"""Orta vade için önerilen model kombinasyonları (skor ve geriye dönük test sayfalarında "Öneri").

Kaynak: 22.10.2026 tarihli kombinasyon taraması. 5 orta vade modelinin bütün alt kümeleri, isteğe
bağlı bir risk modeliyle birlikte (155 kombinasyon) 3 ay tutmayla denendi. Seçim dönemin ilk
yarısında (2021-11 – 2024-02) yapıldı, ikinci yarı (2024-03 – 2026-07) örneklem dışı kontrol oldu.
Rakamlar maliyetsiz; en iyi %20'nin evrene göre yıllık farkıdır.
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
        "modeller": ["zirve52", "surekli_mom", "dusuk_ioyn"],
        "ufuk": "3",
        "baslik": "52 hafta zirvesi + Sürekli momentum + Düşük özgün oynaklık",
        "guven": "zayıf",
        "ozet": ("ABD'de hiçbir orta vade kombinasyonu güvenilir değil. Bu en sağlamı: IC 0,072 (t 1,8) ama "
                 "evrene göre yılda yalnızca %0 ile +%2 arası, dönemin ikinci yarısında eksiye dönüyor. Hisse seçmekten çok "
                 "riskli hisseleri elemek için filtre olarak kullan."),
    },
}
TARAMA_TARIHI = "22.10.2026"
