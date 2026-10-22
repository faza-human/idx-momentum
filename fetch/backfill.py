"""Backfill & perbaikan histori dari Yahoo Finance.

Pemakaian: python fetch/backfill.py --data data --start 2022-08-24 [--end 2026-10-22]

Untuk tiap hari bursa (menurut Yahoo) di rentang:
  - belum ada file            -> diisi dari Yahoo                       ("filled")
  - file ada, close tidak cocok dengan Yahoo (< 80% dari 50 saham terlikuid) -> diganti Yahoo ("replaced")
  - file ada tapi parsial (saham aktif Yahoo > 1,3x)  -> saham yang hilang ditambah dari Yahoo ("augmented")
Hari kerja tanpa data di Yahoo maupun file -> dicatat sebagai libur.
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import sys
from datetime import date, datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sources import WIB, COLS, normalize, yahoo, yahoo_index  # noqa: E402
from update import existing_dates, load_status, path_for, read_day, save_status  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("backfill")


def agreement(df, y, n=50):
    liq = df[df["Volume"] > 0].sort_values("Value", ascending=False)["Stock Code"].head(n)
    m = df[df["Stock Code"].isin(liq)].merge(y[["Stock Code", "Last Price"]], on="Stock Code", suffixes=("", "_y"))
    if len(m) < 20:
        return np.nan
    return float(((m["Last Price"] / m["Last Price_y"] - 1).abs() < 0.01).mean())


def main(root, start, end):
    st = load_status(root)
    have = set(existing_dates(root))
    codes = set()
    for f in glob.glob(os.path.join(root, "daily", "stock_data_*.csv")):
        x = pd.read_csv(f, usecols=["Stock Code", "Volume"])
        codes |= set(x.loc[x["Volume"] > 0, "Stock Code"])
    log.info("Ambil Yahoo untuk %d kode, %s s/d %s", len(codes), start, end)
    y = yahoo(sorted(codes), start, end)
    ydays = y.groupby("Date").size()
    ydays = ydays[ydays >= 200]
    rep = {"filled": [], "replaced": [], "augmented": [], "agree_low": [], "holidays": []}

    for ds, _ in ydays.items():
        d = date.fromisoformat(ds)
        yd = normalize(y[y["Date"] == ds], "yahoo")
        if d not in have:
            yd.to_csv(path_for(root, d), index=False)
            st["days"][ds] = {"source": "yahoo", "rows": len(yd), "active": int((yd["Volume"] > 0).sum()),
                              "fetched": datetime.now(WIB).isoformat(timespec="minutes"), "via": "backfill"}
            rep["filled"].append(ds)
            continue
        cur = read_day(root, d)
        ag = agreement(cur, yd)
        if np.isfinite(ag) and ag < 0.80:
            rep["agree_low"].append((ds, round(ag, 2)))
            yd.to_csv(path_for(root, d), index=False)
            st["days"][ds] = {"source": "yahoo", "rows": len(yd), "active": int((yd["Volume"] > 0).sum()),
                              "via": "backfill-replace", "old_agreement": round(ag, 2)}
            rep["replaced"].append(ds)
            continue
        a_cur, a_y = int((cur["Volume"] > 0).sum()), int((yd["Volume"] > 0).sum())
        if a_y > 1.3 * a_cur:
            add = yd[~yd["Stock Code"].isin(cur["Stock Code"])]
            for c in COLS:
                if c not in cur:
                    cur[c] = "" if c == "Source" else 0
            cur["Source"] = cur["Source"].replace("", np.nan).fillna("nofendian17")
            out = pd.concat([cur[COLS], add[COLS]]).sort_values("Stock Code")
            out.to_csv(path_for(root, d), index=False)
            st["days"][ds] = {**st["days"].get(ds, {}), "augmented_yahoo": len(add), "active": int((out["Volume"] > 0).sum())}
            rep["augmented"].append(ds)

    # hari kerja tanpa data di mana pun = libur
    have = set(existing_dates(root))
    for d in pd.bdate_range(start, min(end, date.fromisoformat(ydays.index.max()))).date:
        if d not in have and str(d) not in ydays.index:
            st["holidays"].append(str(d))
            rep["holidays"].append(str(d))

    # IHSG penuh
    os.makedirs(os.path.join(root, "index"), exist_ok=True)
    ix = yahoo_index(start, end)
    p = os.path.join(root, "index", "ihsg.csv")
    old = pd.read_csv(p) if os.path.exists(p) else ix.iloc[0:0]
    pd.concat([old, ix]).drop_duplicates("Date", keep="last").sort_values("Date").to_csv(p, index=False)

    st["runs"].append({"at": datetime.now(WIB).isoformat(timespec="minutes"), "backfill":
                       {k: len(v) for k, v in rep.items()}})
    save_status(root, st)
    with open(os.path.join(root, "backfill_report.json"), "w") as f:
        json.dump(rep, f, indent=1)
    log.info("Selesai: %s", {k: len(v) for k, v in rep.items()})
    log.info("Agreement rendah (diganti): %s", rep["agree_low"][:30])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", default=None)
    a = ap.parse_args()
    end = date.fromisoformat(a.end) if a.end else datetime.now(WIB).date()
    main(a.data, date.fromisoformat(a.start), end)
