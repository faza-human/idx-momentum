"""Satu kali: impor histori dari github.com/nofendian17/idx_dataset ke data/daily/ dalam skema bersih.

Pemakaian: python -I fetch/bootstrap.py <folder_csv_nofendian17> data

- Kolom harga yang terotasi (Jun-Des 2024, Mei 2025) diperbaiki lewat sources.normalize().
- File hari libur / rusak (saham aktif < 100) tidak diimpor -> diisi backfill Yahoo bila hari bursa.
- File parsial (hanya saham A-E, Okt 2023-Jun 2024) tetap diimpor; backfill Yahoo menambah saham yang hilang.
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd  # noqa: E402

from sources import PRICE, normalize, ohlc_ok  # noqa: E402

src, root = sys.argv[1], sys.argv[2]
os.makedirs(os.path.join(root, "daily"), exist_ok=True)
rep = {"imported": 0, "rotated_fixed": [], "skipped": []}
for f in sorted(glob.glob(os.path.join(src, "stock_data_*.csv"))):
    d = os.path.basename(f)[11:21]
    raw = pd.read_csv(f)
    if len(raw) == 0 or raw["Date"].astype(str).iloc[0] != d:
        rep["skipped"].append((d, "tanggal tidak cocok/kosong"))
        continue
    x = raw[PRICE].to_numpy(float)
    act = raw["Volume"].to_numpy() > 0
    rotated = ohlc_ok(x[:, 4], x[:, 0], x[:, 1], x[:, 2], x[:, 3], act) > ohlc_ok(*x.T, act)
    df = normalize(raw, "nofendian17")
    if (df["Volume"] > 0).sum() < 100:
        rep["skipped"].append((d, f"aktif {(df['Volume'] > 0).sum()}"))
        continue
    if rotated:
        rep["rotated_fixed"].append(d)
    df.to_csv(os.path.join(root, "daily", os.path.basename(f)), index=False)
    rep["imported"] += 1
print(json.dumps({k: (v if not isinstance(v, list) else (len(v), v[:5])) for k, v in rep.items()}))
