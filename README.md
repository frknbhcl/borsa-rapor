# Sanal borsa verisi

Bu dal yalnızca sanal borsa sayfasının (sanal.html) verisini tutar:

- `hesap.json`: sanal portföy, işlemler ve izleme listesi. Sayfa, kullanıcının token'ıyla GitHub API üzerinden yazar.
- `izleme.json`: izleme listesindeki ve portföydeki hisselerin fiyatları (son fiyat, gün içi seyir, 1 yıllık kapanışlar). "Sanal borsa fiyatları" iş akışı seans saatlerinde 30 dakikada bir günceller.

Sanal paradır; gerçek işlem yapılmaz.
