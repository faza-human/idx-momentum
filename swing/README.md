# Swing Agent — IDX

**Setup utama sejak 23 Okt 2026: Setup S** (gabungan dua varian Stochastic 5,3,3). Aturan dikunci per 23 Okt 2026;
sinyal sesudah tanggal itu = uji maju murni. Email harian, posisi paper, dan rekam jejak memakai Setup S.

## Setup S
Filter dasar & pasar sama dengan Setup B (lihat bawah). Sinyal bila salah satu:
- **v1**: Setup B (close > high kemarin, candle hijau, di zona pullback MA20) **dan** Stochastic %K(5,3,3) sempat < 20 dalam 3 hari.
- **v2**: zona pullback MA20 (koreksi 3–15%, low 3 hari sentuh MA20 +2%) **dan** %K memotong %D ke atas hari ini **dan** %K sempat < 20 dalam 3 hari.
Entry, SL, TP1, trailing, time stop, biaya: sama dengan Setup B.

Historis (1 posisi per saham, Jul 2023–Okt 2026): 72 trade, win 35%, +0,76R/trade, median −1,16R,
tanpa 3 trade terbaik −0,06R. Per tahun: 2023 +0,66 · 2024 −0,64 · 2025 +2,84 · 2026 −0,79 (5 trade).
Portofolio Rp30 jt: +44,8%, drawdown maks −16,4% (B asli: −11,1% / −38,5%).
Irisan v1 DAN v2 diuji dan ditolak: 19 trade, +0,05R, +0,9%.
Catatan: hasil bergantung pada sedikit kenaikan besar (MLPT Sep 2025 +26R, WIIM Sep 2023, RMKE Nov 2025).

## Lab setup (`lab.py`)
Kandidat varian didaftarkan dengan tanggal sebelum dinilai; penilaian hanya dari uji maju (sinyal setelah tanggal daftar).
Kandidat menggantikan S hanya bila uji maju ≥ 30 trade unik, R ≥ S + 0,10, dan tanpa 3 trade terbaik > 0.
Terdaftar 23 Okt 2026: C1 S + pasar sedang tren · C2 breakout high 52 minggu + volume · C3 NR7 di zona pullback.
Historis (info saja): C1 49 trade +1,41R (tanpa top3 +0,21, DD −10%) — ide C1 muncul dari analisis 2024 vs 2025,
jadi angka historisnya bias; C2 +0,11R; C3 +0,06R.

---

Screener swing harian + email Gmail. Modal Rp30 jt, risiko 1%/trade, maks 5 posisi, maks 30% modal per posisi.

## File
- `swing.py` — loader data (dengan perbaikan format kolom), indikator, Setup A & B, simulasi trade & portofolio, `CFG`.
- `backtest.py` — `python -I backtest.py <data_dir> <out_dir>`: IS vs OOS, varian ketahanan, baseline acak.
- `lab.py` — `python -I lab.py <data_dir> <out_dir>`: tabel kandidat setup (`lab.csv`, `lab.html`).
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

## Hasil backtest (data repo ini setelah diperbaiki & dilengkapi, sinyal Jul 2023–Okt 2026)
| Strategi | Periode | Trade | Win | Rata-rata R | t | PF | Return portofolio | Max DD |
|---|---|---|---|---|---|---|---|---|
| A breakout | IS | 48 | 29,2% | −0,28 | −1,1 | 0,68 | −11,8% | −14,2% |
| A breakout | OOS | 23 | 43,5% | +0,12 | 0,3 | 1,19 | +6,6% | −7,1% |
| B pullback | IS | 163 | 27,0% | −0,35 | −2,6 | 0,58 | −35,0% | −38,5% |
| B pullback | OOS | 104 | 49,0% | +0,64 | 2,6 | 2,06 | +38,2% | −8,6% |
| B pullback | ALL | 267 | 35,6% | +0,03 | 0,2 | 1,04 | −11,1% | −38,5% |
| B tanpa regime | ALL | 516 | 34,3% | −0,17 | −2,1 | 0,78 | −25,2% | −49,8% |
| B slippage 2 tick | OOS | 104 | 48,1% | +0,51 | 2,1 | 1,75 | +29,3% | −10,5% |
| Baseline acak (uptrend+RS, exit sama) | IS | 1.955 | 33,2% | −0,18 | −4,3 | – | – | – |
| Baseline acak | OOS | 1.723 | 43,0% | +0,37 | 5,7 | – | – | – |

Per tahun (B): 2023 −0,20R (51) · 2024 −0,54R (96) · 2025 +0,80R (85) · 2026 +0,07R (35).
IS = sinyal s/d 30 Jun 2025, OOS = setelahnya. Portofolio dimulai Rp30 jt per periode.
Versi sebelumnya (data lama, 2024 hanya saham A–E) menunjukkan IS −0,37R pada 116 trade; data lengkap
memperburuk 2024.

## Kesimpulan jujur
- Sepanjang 3,2 tahun Setup B ≈ impas (+0,03R/trade) dengan drawdown −38,5% di 2023–2024.
  Seluruh keuntungan datang dari 2025.
- Di periode 1, B lebih buruk dari saham uptrend acak (−0,35R vs −0,18R). Di periode 2 lebih baik
  (+0,64R vs +0,37R). Keunggulan setup atas acak tidak konsisten.
- Setup A tidak dipakai. Filter regime wajib (tanpa itu −0,17R).
- Status: PAPER TRADE. Naik ke uang asli hanya bila 30+ trade paper mendekati hasil OOS
  dan review bulanan tidak menunjukkan penurunan.

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

Hasil (data diperbaiki, s/d 22 Okt 2026):
| Cara memilih config (uji Jan 2025–Okt 2026) | Trade | Win | Rata R |
|---|---|---|---|
| Config tetap (default) | 120 | 47,5% | +0,59 |
| Adaptif, semua data lalu | 120 | 44,2% | +0,67 |
| Adaptif, 4 kuartal terakhir | 89 | 42,7% | +0,35 |
| Oracle (lihat masa depan) | 70 | 48,6% | +0,68 |

- Adaptif (semua data) unggul +0,08R, di bawah syarat +0,10R → config tetap. Pilihannya konsisten
  TP1 3R / trailing MA10 / regime MA50; dipantau tiap bulan.
- Korelasi peringkat config periode 1 vs 2 = +0,40 (data lama: +0,06).
- Regime MA50 lebih baik dari MA20.

## Watchlist pra-setup (email harian)
Saham lolos filter dasar (likuid, RS 30% teratas, Close > MA50 > MA200, MA50 naik) yang sedang koreksi
3–15% dari high 15 hari dan berada −2% s/d +5% dari MA20. Maks 10, urut RS. Ini daftar pantau, bukan sinyal beli.

## Catatan / backlog
- (28 Okt 2026) Filter syariah: batasi sinyal screener swing & watchlist ke saham Daftar Efek Syariah (DES OJK, periode berlaku 1 Jun & 1 Des) untuk eksekusi via akun syariah (SOTS). Belum diimplementasikan — catatan saja.
