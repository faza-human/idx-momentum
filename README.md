# idx-momentum

Data EOD harian Bursa Efek Indonesia + screener swing trading (Setup Pullback) dan review bulanan.

## Struktur
```
data/daily/stock_data_YYYY-MM-DD.csv   satu file per hari bursa (harga mentah, belum di-adjust)
data/index/ihsg.csv                    IHSG (^JKSE) harian
data/status.json                       sumber & hasil validasi per hari, daftar libur, log run
fetch/sources.py                       sumber: IDX resmi -> imq21 -> Yahoo Finance
fetch/update.py                        update harian (dipanggil GitHub Actions)
fetch/backfill.py                      isi/perbaiki histori dari Yahoo
fetch/bootstrap.py                     impor awal dari nofendian17/idx_dataset (sekali)
swing/                                 swing.py, backtest.py, screener.py, learn.py
.github/workflows/update.yml           17:20, 18:20, 22:50 WIB, Senin-Jumat
.github/workflows/backfill.yml         manual
```

## Kolom CSV
`Date, Stock Code, Board, Previous Price, Last Price, Open Price, High Price, Low Price, Volume, Value,
Frequency, Foreign Buy, Foreign Sell, Listed Shares, Source`

`Last Price` = close. `Previous Price` = acuan resmi hari ini (sudah disesuaikan bila ada split).
Frekuensi, asing, dan jumlah saham hanya terisi bila sumbernya IDX resmi. Untuk sumber Yahoo,
`Value` = perkiraan (volume × rata-rata OHLC).

## Validasi sebelum disimpan
Data sebuah hari hanya ditulis bila lolos semua:
1. Saham aktif ≥ max(300, 60% median 20 hari terakhir) — menolak file parsial.
2. OHLC valid ≥ 99% (High ≥ Open/Close ≥ Low) — menolak kolom terotasi.
3. `Previous Price` = close kemarin pada ≥ 85% saham — menolak hari yang salah tanggal dan
   mendeteksi hari bursa yang terlewat.
4. Close cocok (±1%) dengan Yahoo pada ≥ 80% saham sampel — pemeriksaan silang antar sumber.

Hari kerja yang Yahoo-nya kosong padahal hari sesudahnya ada data = libur (dicatat di `status.json`).
Run 22:50 WIB gagal (GitHub mengirim email) bila hari bursa terakhir masih kosong.

## Masalah sumber lama (nofendian17/idx_dataset) yang ditangani
- 101 file dengan kolom harga terotasi (27 Jun–10 Des 2024, 23–26 Mei 2025) → diperbaiki saat impor.
- File parsial berisi saham A–E saja (Okt 2023–Jun 2024) → dilengkapi backfill Yahoo.
- Hari bursa hilang (mis. 5–13 Okt 2026, 19 Okt 2026) → diisi backfill / update.
- File hari libur berisi data nol (mis. 16 Sep 2024) → dibuang.

## Menjalankan lokal
```
pip install -r requirements.txt
python fetch/update.py --data data
python -I swing/screener.py data/daily out      # email.html, subject.txt, signals.json
python -I swing/learn.py data/daily out         # learn_report.html (review bulanan)
python -I swing/backtest.py data/daily out
```
Penjelasan strategi & hasil backtest: lihat `swing/README.md`.

Bukan rekomendasi investasi.
