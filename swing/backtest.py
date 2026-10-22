"""Backtest swing Setup A (breakout) vs B (pullback).

Pemakaian: python -I backtest.py <data_dir> <out_dir>
In-sample  : sinyal s/d 30 Jun 2025
Out-sample : sinyal mulai 1 Jul 2025
Parameter tidak dioptimasi; varian hanya untuk uji ketahanan.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

from swing import (CFG, IS_END, Market, add_features, base_filter, curve_stats, load_raw,
                   market_regime, portfolio, run_trades, setup_a, setup_b, trade_stats)


def main(data_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    df = add_features(load_raw(data_dir, cache=f"{out_dir}/raw.pkl"))
    reg = market_regime(df)
    df["regime_on"] = df["Date"].map(reg["regime_on"]).fillna(False).astype(bool)
    df.to_pickle(f"{out_dir}/features.pkl")
    m = Market(df)
    dates = np.sort(df["Date"].unique())
    n_days = len(dates)

    ready = df["ma200"].notna()
    ma = setup_a(df) & ready
    mb = setup_b(df) & ready
    rng = np.random.default_rng(7)
    base = base_filter(df) & ready & df["regime_on"]
    base_rows = rng.choice(np.flatnonzero(base.values), size=min(15000, int(base.sum())), replace=False)

    variants = {
        "A breakout": (ma & df["regime_on"], CFG),
        "B pullback": (mb & df["regime_on"], CFG),
        "A+B": ((ma | mb) & df["regime_on"], CFG),
        "A tanpa regime": (ma, CFG),
        "B tanpa regime": (mb, CFG),
        "A trail MA20": (ma & df["regime_on"], {**CFG, "trail_ma": "ma20"}),
        "B trail MA20": (mb & df["regime_on"], {**CFG, "trail_ma": "ma20"}),
        "A slip 2 tick": (ma & df["regime_on"], {**CFG, "slip_ticks": 2}),
        "B slip 2 tick": (mb & df["regime_on"], {**CFG, "slip_ticks": 2}),
    }
    rows_out, all_trades = [], {}
    oos_di = int(np.searchsorted(dates, np.datetime64(IS_END + pd.Timedelta(days=1))))

    def record(name, t, cfg, do_port=True):
        all_trades[name] = t
        for part, tt in (("ALL", t), ("IS", t[t["sig_date"] <= IS_END]), ("OOS", t[t["sig_date"] > IS_END])):
            s = dict(strategy=name, period=part, **trade_stats(tt))
            if do_port and len(tt):
                start = int(tt["fill_di"].min()) if part != "OOS" else oos_di
                cv, _ = portfolio(m, tt, n_days if part != "IS" else oos_di, start_di=start, c=cfg)
                s.update(curve_stats(cv, dates, cfg["capital"]))
            rows_out.append(s)

    for name, (mask, cfg) in variants.items():
        t = run_trades(m, np.flatnonzero(mask.values), cfg)
        record(name, t, cfg)
        print(f"{name}: {len(t)} trade", flush=True)
    t0 = run_trades(m, base_rows, CFG)
    record("Baseline: uptrend+RS acak, exit sama", t0, CFG, do_port=False)

    res = pd.DataFrame(rows_out)
    res.to_csv(f"{out_dir}/swing_summary.csv", index=False)
    tr = pd.concat([t.assign(strategy=k) for k, t in all_trades.items()])
    tr.drop(columns=["fills"]).to_csv(f"{out_dir}/swing_trades.csv", index=False)

    # rincian per tahun & alasan exit untuk A dan B
    extra = []
    for k in ("A breakout", "B pullback"):
        t = all_trades[k]
        for y, g in t.groupby(pd.to_datetime(t["sig_date"]).dt.year):
            extra.append(dict(strategy=k, year=y, **trade_stats(g)))
    yearly = pd.DataFrame(extra)
    yearly.to_csv(f"{out_dir}/swing_yearly.csv", index=False)
    reasons = {k: all_trades[k]["reason"].value_counts(normalize=True).round(3).to_dict()
               for k in ("A breakout", "B pullback")}
    pd.set_option("display.width", 250, "display.max_columns", 30)
    print(res.to_string(index=False))
    print(yearly.to_string(index=False))
    print(reasons)
    print("range:", dates[0], dates[-1], "| OOS start di", oos_di)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
