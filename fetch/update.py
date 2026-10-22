"""Update data EOD harian IDX: isi hari bursa yang belum ada, validasi, simpan.

Pemakaian:
  python fetch/update.py --data data            # dijalankan GitHub Actions 3x tiap malam
  python fetch/update.py --data data --final    # run terakhir: exit 1 bila hari bursa terakhir masih kosong

Setiap run:
  1. Tentukan hari bursa (Senin-Jumat) dalam 14 hari bursa terakhir yang belum punya file & bukan libur.
  2. Coba sumber berurutan: IDX resmi -> imq21 -> Yahoo. Ambil yang pertama LOLOS validasi.
  3. Hari tanpa data dari semua sumber: libur bila Yahoo punya data sesudahnya tapi kosong di hari itu.
  4. Perbarui data/index/ihsg.csv (IHSG ^JKSE dari Yahoo) dan data/status.json.
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sources import WIB, COLS, SourceError, idx_official, imq21, ohlc_ok, yahoo, yahoo_day, yahoo_index  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("update")
READY_HOUR_WIB = 17          # data hari ini baru dicari setelah 17:00 WIB


# ------------------------------------------------------------------ util
def daily_dir(root):
    return os.path.join(root, "daily")


def path_for(root, d):
    return os.path.join(daily_dir(root), f"stock_data_{d.isoformat()}.csv")


def existing_dates(root):
    return sorted(date.fromisoformat(os.path.basename(f)[11:21])
                  for f in glob.glob(os.path.join(daily_dir(root), "stock_data_*.csv")))


def load_status(root):
    p = os.path.join(root, "status.json")
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {"holidays": [], "days": {}, "runs": []}


def save_status(root, st):
    st["holidays"] = sorted(set(st["holidays"]))
    st["runs"] = st["runs"][-60:]
    with open(os.path.join(root, "status.json"), "w") as f:
        json.dump(st, f, indent=1, sort_keys=True)


def bdays_back(end: date, n: int):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def read_day(root, d):
    return pd.read_csv(path_for(root, d))


# ------------------------------------------------------------------ validasi
def reference(root, dates):
    """Median jumlah saham aktif & total dari 20 file terakhir."""
    act, tot = [], []
    for d in dates[-20:]:
        x = read_day(root, d)
        act.append(int((x["Volume"] > 0).sum()))
        tot.append(len(x))
    return (float(np.median(act)) if act else 600.0), (float(np.median(tot)) if tot else 800.0)


def validate(df, src, ref_active, prev_df, prev_gap_ok, yref):
    """Return (ok, info). Semua alasan gagal dicatat di info['fail']."""
    info, fail = {"source": src, "rows": int(len(df))}, []
    if df.empty:
        return False, {**info, "fail": ["kosong"]}
    act = df["Volume"].to_numpy() > 0
    info["active"] = int(act.sum())
    if act.sum() < max(300, 0.6 * ref_active):
        fail.append(f"saham aktif {act.sum()} < {max(300, 0.6 * ref_active):.0f}")
    x = df[["Previous Price", "Last Price", "Open Price", "High Price", "Low Price"]].to_numpy(float)
    info["ohlc_ok"] = round(ohlc_ok(*x.T, act), 4)
    if info["ohlc_ok"] < 0.99:
        fail.append(f"OHLC valid {info['ohlc_ok']:.3f} < 0.99")
    if prev_df is not None and prev_gap_ok:
        m = df.merge(prev_df[["Stock Code", "Last Price"]], on="Stock Code", suffixes=("", "_y"))
        m = m[m["Volume"] > 0]
        if len(m) > 100:
            info["prev_match"] = round(float(((m["Previous Price"] / m["Last Price_y"] - 1).abs() < 0.02).mean()), 4)
            if info["prev_match"] < 0.85:
                fail.append(f"Previous vs close kemarin cocok {info['prev_match']:.2f} < 0.85")
    if yref is not None and src != "yahoo" and len(yref):
        m = df.merge(yref[["Stock Code", "Last Price"]], on="Stock Code", suffixes=("", "_y"))
        if len(m) >= 20:
            info["yahoo_match"] = round(float(((m["Last Price"] / m["Last Price_y"] - 1).abs() < 0.01).mean()), 4)
            if info["yahoo_match"] < 0.80:
                fail.append(f"close vs Yahoo cocok {info['yahoo_match']:.2f} < 0.80")
    info["fail"] = fail
    return not fail, info


# ------------------------------------------------------------------ utama
def run(root, n_days=14, final=False, today=None, sources=("idx", "imq21", "yahoo")):
    os.makedirs(daily_dir(root), exist_ok=True)
    now = datetime.now(WIB)
    today = today or now.date()
    last_cand = today if now.hour >= READY_HOUR_WIB or today < now.date() else today - timedelta(days=1)
    st = load_status(root)
    have = existing_dates(root)
    hol = {date.fromisoformat(h) for h in st["holidays"]}
    # hari libur di jendela ikut dicek ulang: bila ternyata ada data, libur dibatalkan
    targets = [d for d in bdays_back(last_cand, n_days) if d not in set(have)]
    run_log = {"at": now.isoformat(timespec="minutes"), "targets": [str(d) for d in targets], "written": [],
               "holidays": [], "missing": []}
    log.info("Target: %s", [str(d) for d in targets] or "tidak ada")

    if targets:
        ref_active, _ = reference(root, have)
        latest = read_day(root, have[-1]) if have else None
        codes = sorted(latest.loc[latest["Volume"] > 0, "Stock Code"]) if latest is not None else []
        liquid = (latest.sort_values("Value", ascending=False)["Stock Code"].head(30).tolist()
                  if latest is not None else [])
        ylong = None
        try:
            ylong = yahoo(codes or liquid, targets[0], last_cand) if codes else None
        except SourceError as e:
            log.warning("%s", e)
        fetchers = {"idx": idx_official, "imq21": imq21}

        for d in targets:
            prev_d = max([x for x in existing_dates(root) if x < d], default=None)
            prev_df = read_day(root, prev_d) if prev_d else None
            # Previous hanya dibandingkan bila tidak ada hari bursa lain yang hilang di antaranya
            between = [b for b in bdays_back(d - timedelta(days=1), 10) if prev_d and b > prev_d and b not in hol]
            yref = yahoo_day(ylong, d) if ylong is not None else None
            tried, n_empty = [], 0
            for src in sources:
                try:
                    if src == "yahoo":
                        if ylong is None:
                            raise SourceError("Yahoo: tidak tersedia")
                        df = yref
                    else:
                        df = fetchers[src](d)
                        if df.empty:
                            n_empty += 1
                except SourceError as e:
                    tried.append(f"{src}: {e}")
                    continue
                except Exception as e:  # noqa: BLE001
                    tried.append(f"{src}: {type(e).__name__}: {e}")
                    continue
                if df is None or df.empty:
                    tried.append(f"{src}: kosong")
                    continue
                ok, info = validate(df, src, ref_active, prev_df, not between, yref)
                if ok:
                    df[COLS].to_csv(path_for(root, d), index=False)
                    if str(d) in st["holidays"]:
                        st["holidays"].remove(str(d))
                        hol.discard(d)
                    st["days"][str(d)] = {**info, "fetched": now.isoformat(timespec="minutes"),
                                          "skipped_sources": tried}
                    run_log["written"].append(f"{d} {src}")
                    log.info("%s OK dari %s: %s", d, src, info)
                    break
                tried.append(f"{src}: {'; '.join(info['fail'])}")
            else:
                later = ylong is not None and (ylong["Date"] > str(d)).any()
                y_has_d = yref is not None and len(yref) > 0
                if d in hol:
                    continue                                   # libur yang sudah diketahui
                if ylong is not None and later and not y_has_d:
                    # Yahoo punya data sesudah d tapi kosong di d (untuk ~900 saham) -> libur
                    st["holidays"].append(str(d))
                    hol.add(d)
                    run_log["holidays"].append(str(d))
                    log.info("%s: libur bursa (Yahoo tidak punya transaksi)", d)
                else:
                    reachable_empty = n_empty > 0 and n_empty == len([s for s in sources if s != "yahoo"]) \
                        and not y_has_d
                    run_log["missing"].append({"date": str(d), "tried": tried,
                                               "probable_holiday": bool(reachable_empty)})
                    log.warning("%s GAGAL%s: %s", d, " (kemungkinan libur)" if reachable_empty else "", tried)

    # IHSG resmi (^JKSE) 40 hari terakhir -> gabung ke data/index/ihsg.csv
    try:
        os.makedirs(os.path.join(root, "index"), exist_ok=True)
        p = os.path.join(root, "index", "ihsg.csv")
        new = yahoo_index(last_cand - timedelta(days=60), last_cand)
        old = pd.read_csv(p) if os.path.exists(p) else new.iloc[0:0]
        pd.concat([old, new]).drop_duplicates("Date", keep="last").sort_values("Date").to_csv(p, index=False)
    except SourceError as e:
        log.warning("%s", e)

    st["runs"].append(run_log)
    save_status(root, st)
    have = existing_dates(root)
    expected = [d for d in bdays_back(last_cand, 3) if str(d) not in st["holidays"]]
    prob = {m["date"] for m in run_log["missing"] if m.get("probable_holiday")}
    expected = [d for d in expected if str(d) not in prob] or expected
    latest_ok = bool(have) and have[-1] >= expected[-1]
    log.info("File terbaru: %s | hari bursa terakhir yang diharapkan: %s", have[-1] if have else None, expected[-1])
    if final and not latest_ok:
        log.error("Data hari bursa terakhir (%s) belum ada setelah run terakhir hari ini.", expected[-1])
        return 1
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--final", action="store_true")
    ap.add_argument("--sources", default="idx,imq21,yahoo")
    a = ap.parse_args()
    sys.exit(run(a.data, a.days, a.final, sources=tuple(a.sources.split(","))))
