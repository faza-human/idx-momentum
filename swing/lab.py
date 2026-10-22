"""Lab setup: kandidat varian yang DIDAFTARKAN dulu (aturan + tanggal), baru diuji.

Pemakaian: python -I lab.py <data_dir> <out_dir>   -> <out_dir>/lab.csv, lab.html
Dipanggil juga oleh learn.py (review bulanan).

Protokol (ditetapkan 23 Okt 2026, tidak diubah setelah melihat hasil):
- Semua kandidat memakai filter pasar (proxy IHSG > MA50), entry, SL, TP1, trailing, biaya yang sama
  dengan Setup S. Yang berbeda hanya syarat sinyal.
- Metrik dihitung pada trade unik (1 posisi per saham) dan portofolio Rp30 jt.
- Hasil historis hanya informasi. Penilaian = uji maju: sinyal SETELAH tanggal daftar kandidat.
- Kandidat layak menggantikan Setup S hanya bila, pada uji maju: >= 30 trade unik selesai,
  rata-rata R >= R Setup S (periode maju yang sama) + 0,10, dan rata-rata R tanpa 3 trade terbaik > 0.
- Kandidat baru ditambahkan di bawah dengan tanggal daftar = hari ditambahkan. Kandidat lama tidak diedit;
  bila aturannya berubah, daftarkan sebagai kandidat baru.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

from swing import (CFG, IS_END, Market, add_features, base_filter, curve_stats, load_raw, market_regime,
                   portfolio, pullback_zone, run_trades, setup_b, setup_s, unique_trades)


# ---------------------------------------------------------------- kandidat
def _c1_trend_market(df, reg):
    """S + pasar sedang tren: proxy IHSG naik dalam 63 hari & MA50 proxy naik 20 hari terakhir.
    Ide: momentum hanya bekerja setelah pasar naik (Cooper, Gutierrez & Hameed 2004)."""
    up63 = reg["mkt"] / reg["mkt"].shift(63) - 1 > 0
    slope = reg["mkt_ma"] > reg["mkt_ma"].shift(20)
    ok = df["Date"].map(up63 & slope).fillna(False).astype(bool)
    return setup_s(df) & ok


def _c2_52w_high(df, reg):
    """Breakout ke high 250 hari baru dengan volume >= 1,5x rata-rata, saham lolos filter dasar.
    Ide: dekat high 52 minggu memprediksi return (George & Hwang 2004)."""
    g = df.groupby("code", sort=False)
    hh250 = g["a_high"].transform(lambda s: s.shift(1).rolling(250, min_periods=240).max())
    return (base_filter(df) & (df["a_close"] > hh250) & (df["volratio"] >= 1.5)
            & (df["clv"] >= 0.5) & (df["ret"] <= 0.15))


def _c3_nr7_pullback(df, reg):
    """NR7 di zona pullback: range hari ini tersempit dalam 7 hari, uptrend, entry = tembus high hari itu.
    Ide: kontraksi volatilitas sebelum ekspansi (Crabel 1990)."""
    rng = df["a_high"] - df["a_low"]
    nr7 = rng <= rng.groupby(df["code"]).transform(lambda s: s.rolling(7).min())
    return base_filter(df) & pullback_zone(df) & nr7 & (df["a_close"] >= df["ma20"])


CANDIDATES = [
    dict(id="S", nama="Setup S — pullback + Stochastic 5,3,3 (utama)", daftar="2026-10-23",
         fn=lambda df, reg: setup_s(df)),
    dict(id="B", nama="Setup B — pullback, close > high kemarin (pembanding)", daftar="2026-10-22",
         fn=lambda df, reg: setup_b(df)),
    dict(id="C1", nama="S + pasar sedang tren (proxy naik 63 hari & MA50 naik)", daftar="2026-10-23",
         fn=_c1_trend_market),
    dict(id="C2", nama="Breakout high 52 minggu + volume 1,5x", daftar="2026-10-23", fn=_c2_52w_high),
    dict(id="C3", nama="NR7 (range tersempit 7 hari) di zona pullback", daftar="2026-10-23", fn=_c3_nr7_pullback),
]


# ---------------------------------------------------------------- evaluasi
def _r(s):
    return round(float(s.mean()), 2) if len(s) else np.nan


def evaluate(df, reg):
    m = Market(df)
    dates = np.sort(df["Date"].unique())
    ready = df["ma200"].notna() & df["regime_on"]
    rows, live = [], {}
    for c in CANDIDATES:
        mask = c["fn"](df, reg) & ready
        t = unique_trades(m, run_trades(m, np.flatnonzero(mask.values)))
        f = t[~t["open_end"]] if len(t) else t
        r = f["r_mult"].sort_values() if len(f) else pd.Series(dtype=float)
        sd = pd.to_datetime(f["sig_date"]) if len(f) else pd.Series(dtype="datetime64[ns]")
        reg_d = pd.Timestamp(c["daftar"])
        fwd = f[sd >= reg_d]["r_mult"] if len(f) else r
        row = dict(id=c["id"], nama=c["nama"], daftar=c["daftar"], sinyal=int(mask.sum()), trade=len(f),
                   win=round(float((f["net"] > 0).mean()) * 100, 1) if len(f) else np.nan,
                   R=_r(r), median=round(float(r.median()), 2) if len(r) else np.nan,
                   tanpa_top3=_r(r.iloc[:-3]) if len(r) > 5 else np.nan,
                   P1=_r(f[sd <= IS_END]["r_mult"]) if len(f) else np.nan,
                   P2=_r(f[(sd > IS_END) & (sd < reg_d)]["r_mult"]) if len(f) else np.nan)
        for y in (2023, 2024, 2025, 2026):
            row[str(y)] = _r(f[sd.dt.year == y]["r_mult"]) if len(f) else np.nan
        if len(t):
            cv, _ = portfolio(m, t, len(dates), start_di=int(t["fill_di"].min()))
            cs = curve_stats(cv, dates, CFG["capital"])
            row["ret_pct"], row["dd_pct"] = cs["total_ret_pct"], cs["max_dd_pct"]
        fs = fwd.sort_values()
        row.update(maju_trade=len(fs), maju_R=_r(fs), maju_tanpa_top3=_r(fs.iloc[:-3]) if len(fs) > 5 else np.nan,
                   maju_terbuka=int(t["open_end"].sum()) if len(t) else 0)
        live[c["id"]] = f[sd >= reg_d] if len(f) else f
        rows.append(row)
    lab = pd.DataFrame(rows)
    # status keputusan (aturan di docstring)
    s_fwd = live["S"]
    status = []
    for _, r in lab.iterrows():
        if r["id"] == "S":
            status.append("utama")
            continue
        if r["maju_trade"] < 30:
            status.append(f"uji maju {r['maju_trade']}/30 trade")
            continue
        start = pd.Timestamp(r["daftar"])
        s_same = s_fwd[pd.to_datetime(s_fwd["sig_date"]) >= start]["r_mult"].mean() if len(s_fwd) else 0.0
        ok = r["maju_R"] >= s_same + 0.10 and (r["maju_tanpa_top3"] or 0) > 0
        status.append("LAYAK dipertimbangkan" if ok else "tidak lebih baik dari S")
    lab["status"] = status
    return lab


def lab_html(lab, th, td):
    o = ["<h3>Lab setup (kandidat varian)</h3>",
         "<p style='color:#666'>Historis hanya info. Penilaian dari kolom <b>uji maju</b> (sinyal setelah tanggal daftar). "
         "Ganti setup hanya bila uji maju ≥ 30 trade, R ≥ Setup S + 0,10, dan tanpa 3 trade terbaik &gt; 0.</p>",
         f"<table style='border-collapse:collapse;font-size:12px'><tr><th {th}>Kandidat</th><th {th}>Hist. trade</th>"
         f"<th {th}>Hist. R</th><th {th}>Tanpa top3</th><th {th}>2024</th><th {th}>2025</th><th {th}>Port. / DD</th>"
         f"<th {th}>Uji maju</th><th {th}>Status</th></tr>"]
    for _, r in lab.iterrows():
        fw = f"{r['maju_trade']} trade" + (f" · {r['maju_R']:+.2f}R" if r["maju_trade"] else "")
        if r.get("maju_terbuka"):
            fw += f" (+{r['maju_terbuka']} terbuka)"
        o.append(f"<tr><td {td}><b>{r['id']}</b> {r['nama']}<br><span style='color:#888'>daftar {r['daftar']}</span></td>"
                 f"<td {td}>{r['trade']}</td><td {td}>{r['R']:+.2f}</td><td {td}>{r['tanpa_top3']:+.2f}</td>"
                 f"<td {td}>{r['2024']:+.2f}</td><td {td}>{r['2025']:+.2f}</td>"
                 f"<td {td}>{r.get('ret_pct', 0):+.0f}% / {r.get('dd_pct', 0):.0f}%</td>"
                 f"<td {td}>{fw}</td><td {td}>{r['status']}</td></tr>")
    o.append("</table>")
    return "\n".join(o)


def prepare(data_dir, out_dir):
    df = add_features(load_raw(data_dir, cache=f"{out_dir}/raw.pkl"))
    reg = market_regime(df)
    df["regime_on"] = df["Date"].map(reg["regime_on"]).fillna(False).astype(bool)
    return df, reg


if __name__ == "__main__":
    data_dir, out_dir = sys.argv[1], sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)
    df, reg = prepare(data_dir, out_dir)
    lab = evaluate(df, reg)
    lab.to_csv(f"{out_dir}/lab.csv", index=False)
    th = "style='background:#1f3a5f;color:#fff;padding:3px 6px;text-align:left'"
    td = "style='padding:2px 6px;border-bottom:1px solid #eee;vertical-align:top'"
    with open(f"{out_dir}/lab.html", "w") as f:
        f.write(lab_html(lab, th, td))
    pd.set_option("display.width", 250, "display.max_columns", 30)
    print(lab.drop(columns=["nama"]).to_string(index=False))
