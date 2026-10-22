# Swing Agent — IDX

Screener swing harian + email Gmail. Modal Rp30 jt, risiko 1%/trade, maks 5 posisi, maks 30% modal per posisi.

## File
- `swing.py` — loader data (dengan perbaikan format kolom), indikator, Setup A & B, simulasi trade & portofolio, `CFG`.
- `backtest.py` — `python -I backtest.py <data_dir> <out_dir>`: IS vs OOS, varian ketahanan, baseline acak.
- `screener.py` — `python -I screener.py <data_dir> <out_dir>`: `email.html`, `subject.txt`, `signals.json`.

## Data
Data: `data/daily/` di repo ini (lihat README utama). Histori awal diimpor dari nofendian17/idx_dataset.
Update Senin–Jumat 17:20/18:20/22:50 WIB lewat GitHub Actions.

Masalah data yang ditemukan dan ditangani di `load_raw`:
1. **Kolom harga terotasi** di 101 file (27 Jun–10 Des 2024, 23–26 Mei 2025): label "Previous"=Close,
   "Last"=Open, "Open"=High, "High"=Low, "Low"=Previous. Dideteksi per file dari validitas OHLC.
   Backtest BSJP lama memakai loader tanpa perbaikan ini → hasilnya perlu dijalankan ulang.
2. **File parsial** Okt 2023–Jun 2024: hanya saham berawalan A–E. Saham lain tidak dipakai sampai
   punya 200 hari data utuh (`cov_ok`). Universe 2024–Q1 2025 hanya ±55–65 saham.
3. **Hari bursa hilang**: lompatan harga massal tidak dianggap aksi korporasi.
4. **File hari libur** (mis. 16 Sep 2024) dibuang. Close diisi sebelum Open/High/Low.

## Aturan
Filter dasar: nilai transaksi 20 hari ≥ Rp5 M, harga ≥ 100, return 126 hari di 30% teratas,
Close > MA50 > MA200, MA50 naik, bukan ARA. Regime: proxy IHSG (100 saham terlikuid) > MA50.

- **Setup A (breakout)**: range 20 hari ≤ 20%, close tembus high 20 hari, volume ≥ 1,5×, CLV ≥ 0,5.
- **Setup B (pullback)**: low 3 hari sentuh MA20 (+2%), koreksi 3–15% dari high 15 hari,
  volume 3 hari sebelumnya < rata-rata, close > high kemarin, candle hijau, close ≥ MA20.

Eksekusi: buy stop 1 tick di atas high hari sinyal, berlaku H+1 & H+2, batal bila open > trigger +3%.
SL = max(low 5 hari − 1 tick, trigger − 1,5×ATR14), minimal 2%, lewati bila > 8%.
TP1 = 2R jual 50%, SL sisa ke BEP. Sisa keluar open besok bila close < MA10.
Time stop 10 hari bila belum TP1 dan < +1R. Fee IPOT 0,19%/0,29%, slippage 1 tick.
Parameter ditetapkan di muka, tidak dioptimasi.

## Hasil backtest (data diperbaiki, sinyal Jul 2023–Okt 2026)
| Strategi | Periode | Trade | Win | Rata-rata R | t | PF | Return portofolio | Max DD |
|---|---|---|---|---|---|---|---|---|
| A breakout | IS | 28 | 28,6% | −0,36 | −1,3 | 0,58 | −4,5% | −8,0% |
| A breakout | OOS | 20 | 40,0% | +0,03 | 0,1 | 1,05 | +0,2% | −8,5% |
| B pullback | IS | 116 | 30,2% | −0,37 | −2,9 | 0,52 | −6,9% | −14,7% |
| B pullback | OOS | 85 | 50,6% | +0,72 | 2,8 | 2,24 | +38,6% | −9,6% |
| B tanpa regime | ALL | 331 | 35,0% | −0,14 | −1,4 | 0,82 | −12,7% | −33,2% |
| B slippage 2 tick | OOS | 85 | 49,4% | +0,60 | 2,3 | 1,91 | +30,5% | −10,5% |
| Baseline acak (uptrend+RS, exit sama) | IS | 1.099 | 31,0% | −0,36 | −8,1 | 0,56 | – | – |
| Baseline acak | OOS | 1.579 | 42,6% | +0,38 | 5,5 | 1,55 | – | – |

IS = sinyal s/d 30 Jun 2025, OOS = setelahnya. Portofolio dimulai Rp30 jt per periode.

## Kesimpulan jujur
- Setup A gagal: negatif di IS, nol di OOS. Tidak dipakai.
- Setup B: rugi di IS, untung di OOS. Tanda tergantung kondisi pasar, bukan edge yang stabil.
- Baseline acak dengan exit yang sama punya pola IS/OOS serupa. Selisih B vs baseline:
  ≈0 di IS, +0,34R di OOS. Sebagian besar hasil = momentum pasar 2025–2026.
- Filter regime penting: tanpa itu B negatif.
- Trailing MA10 lebih baik dari MA20. Hasil OOS bertahan dengan slippage 2 tick.
- IS 2024 tidak bisa diandalkan (universe kecil akibat data parsial).
- Status: PAPER TRADE. Naik ke uang asli hanya bila 30+ trade paper mendekati hasil OOS.

## Keterbatasan
- Proxy IHSG, bukan IHSG resmi. Status FCA/papan pemantauan dan aksi korporasi tidak difilter.
- Bar harian: bila SL dan TP tersentuh di hari yang sama, diasumsikan SL dulu (konservatif).
- Order IPOT (auto order, masa berlaku, OCO) belum diverifikasi; cek menu di aplikasi.

## Continuous learning (`learn.py`, review bulanan)
`python -I learn.py <data_dir> <out_dir>` → `learn_report.html` (dikirim via email tiap bulan).

Pagar anti-overfitting:
- Grid kecil ditetapkan di muka (24 config: RS 30/20%, TP1 1,5/2/3R, trailing MA10/MA20, regime MA50/MA20).
- Walk-forward per kuartal mulai Jan 2025: config dipilih hanya dari trade yang sudah selesai sebelum kuartal itu.
- Config diganti hanya bila pemilihan adaptif unggul ≥ +0,10R per trade dibanding config tetap,
  dan hanya setelah Umaa menyetujui. Tidak ada perubahan otomatis.
- Target = rata-rata R (ekspektasi). Win rate hanya info: TP1 1,5R menaikkan win rate (54% vs 47%)
  tapi rata-rata R-nya lebih rendah dari TP1 3R.
- Fitur sinyal (RS, kedalaman koreksi, ATR%, volume, jarak MA50, kemiringan MA50, jarak SL, kenaikan hari sinyal,
  likuiditas) diuji per tersil di kedua periode dan dua set data (Setup B dan uptrend acak).

Hasil pertama (data s/d 20 Okt 2026):
| Cara memilih config (uji Jan 2025–Okt 2026) | Trade | Win | Rata R |
|---|---|---|---|
| Config tetap (default) | 99 | 48,5% | +0,65 |
| Adaptif, semua data lalu | 46 | 45,7% | +0,36 |
| Adaptif, 4 kuartal terakhir | 46 | 45,7% | +0,43 |
| Oracle (lihat masa depan) | 52 | 51,9% | +0,79 |

- Korelasi peringkat config periode 1 vs 2 = +0,06 → tuning parameter = mengejar noise. Config tetap.
- Semua 24 config negatif di periode 1 dan positif di periode 2 → kondisi pasar dominan, bukan parameter.
- Regime MA50 lebih baik dari MA20 di periode 2 (12 config teratas semuanya MA50).
- Satu-satunya ciri konsisten: kemiringan MA50 (hanya di set uptrend acak). Masuk daftar uji, belum dipakai.

## Watchlist pra-setup (email harian)
Saham lolos filter dasar (likuid, RS 30% teratas, Close > MA50 > MA200, MA50 naik) yang sedang koreksi
3–15% dari high 15 hari dan berada −2% s/d +5% dari MA20. Maks 10, urut RS. Ini daftar pantau, bukan sinyal beli.
