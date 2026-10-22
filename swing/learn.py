"""Continuous learning untuk Setup S (pullback + Stochastic 5,3,3), dengan pagar anti-overfitting.
Juga menjalankan lab setup (lab.py): kandidat varian yang dinilai lewat uji maju.

Pemakaian: python -I learn.py <data_dir> <out_dir>
Output   : <out_dir>/learn_report.html, learn_subject.txt, learn_configs.csv,
           learn_walkforward.csv, learn_features.csv

Tiga pertanyaan:
1. Walk-forward: bila tiap kuartal kita memilih config terbaik dari data MASA LALU saja,
   apakah hasil kuartal berikutnya lebih baik dari config tetap? Bila tidak, "learning"
   hanya mengejar noise dan config tidak boleh diubah.
2. Stabilitas: apakah urutan config di periode 1 sama dengan di periode 2?
3. Fitur: ciri sinyal apa yang memprediksi R secara konsisten di KEDUA periode?

Grid dan fitur ditetapkan di muka. Jangan menambah kandidat setelah melihat hasil.
Target: rata-rata R (ekspektasi), bukan win rate. Win rate dilaporkan sebagai info.
"""
from __future__ import annotations

import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

from swing import (CFG, IS_END, Market, add_features, base_filter, load_raw, market_regime,
                   run_trades, setup_s, trade_stats)
import lab as setup_lab  # noqa: E402

GRID = dict(rs_top=[0.30, 0.20], tp_r=[1.5, 2.0, 3.0], trail_ma=["ma10", "ma20"], regime_ma=[50, 20])
DEFAULT = dict(rs_top=0.30, tp_r=2.0, trail_ma="ma10", regime_ma=50)
MIN_TRAIN = 30          # minimal trade selesai di data latih sebelum boleh memilih
FIRST_TEST = pd.Timestamp("2025-01-01")
MARGIN_R = 0.10         # usulan ganti config hanya bila unggul >= 0,10R di walk-forward


BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def tgl(d):
    return f"{d.day} {BULAN[d.month - 1]} {d.year}"


def key(p):
    return f"rs{int(p['rs_top']*100)}_tp{p['tp_r']}_{p['trail_ma']}_reg{p['regime_ma']}"


def finished(t):
    return t[~t["open_end"]] if len(t) else t


def run_grid(df, m, regimes):
    ready = df["ma200"].notna()
    out = {}
    for vals in itertools.product(*GRID.values()):
        p = dict(zip(GRID.keys(), vals))
        cfg = {**CFG, **{k: v for k, v in p.items() if k != "regime_ma"}}
        mask = setup_s(df, cfg) & ready & regimes[p["regime_ma"]]
        t = run_trades(m, np.flatnonzero(mask.values), cfg)
        out[key(p)] = (p, t)
    return out


def config_table(grid):
    rows = []
    for k, (p, t) in grid.items():
        r = dict(config=k, **p)
        for part, tt in (("is", t[t["sig_date"] <= IS_END]), ("oos", t[t["sig_date"] > IS_END])):
            s = trade_stats(tt)
            r[f"{part}_n"] = s.get("trades", 0)
            r[f"{part}_R"] = s.get("avg_R", np.nan)
            r[f"{part}_win"] = s.get("win_rate", np.nan)
        rows.append(r)
    ct = pd.DataFrame(rows).sort_values("oos_R", ascending=False)
    rho = ct[["is_R", "oos_R"]].rank().corr().iloc[0, 1]
    return ct, rho


def walk_forward(grid, last_date, window_q=None):
    """Pilih config per kuartal dari trade yang SUDAH SELESAI sebelum kuartal mulai."""
    quarters = pd.period_range(FIRST_TEST, last_date, freq="Q")
    rows, picks = [], []
    for q in quarters:
        s, e = q.start_time, q.end_time
        lo = (s - pd.DateOffset(months=3 * window_q)) if window_q else pd.Timestamp.min
        best, best_r = key(DEFAULT), -np.inf
        for k, (p, t) in grid.items():
            f = finished(t)
            tr = f[(f["exit_date"] < s) & (f["sig_date"] >= lo)]
            if len(tr) >= MIN_TRAIN and tr["r_mult"].mean() > best_r:
                best, best_r = k, tr["r_mult"].mean()
        picks.append(dict(quarter=str(q), pick=best, train_R=round(best_r, 3) if np.isfinite(best_r) else None))
        t = grid[best][1]
        rows.append(t[(t["sig_date"] >= s) & (t["sig_date"] <= e)].assign(quarter=str(q)))
    return pd.concat(rows), pd.DataFrame(picks)


def feature_table(df, t, label):
    """R per tersil fitur di IS dan OOS. Konsisten = arah sama dan selisih >= 0,2R di kedua periode."""
    t = finished(t).copy()
    d = df.loc[t["sig_row"].values]
    feats = {
        "RS 126 hari (persentil)": d["rs_pct"].values,
        "Kedalaman koreksi": (1 - d["ll3"] / d["hh15p"]).values,
        "ATR % harga": (d["atr14"] / d["a_close"]).values,
        "Volume hari sinyal / rata-rata": d["volratio"].values,
        "Jarak close ke MA50": (d["a_close"] / d["ma50"] - 1).values,
        "Kemiringan MA50 (10 hari)": (d["ma50"] / d["ma50_prev10"] - 1).values,
        "Jarak SL (%)": t["rpct"].values,
        "Kenaikan hari sinyal": d["ret"].values,
        "Likuiditas (log nilai 20h)": np.log10(d["value20"].values),
    }
    is_mask = (t["sig_date"] <= IS_END).values
    rows = []
    for name, x in feats.items():
        cuts = np.nanquantile(x, [1 / 3, 2 / 3])
        b = np.digitize(x, cuts)
        r = {"set": label, "fitur": name, "batas_tersil": f"{cuts[0]:.3g} / {cuts[1]:.3g}"}
        spread = {}
        for part, pm in (("IS", is_mask), ("OOS", ~is_mask)):
            for k, nm in enumerate(("rendah", "tengah", "tinggi")):
                sel = pm & (b == k)
                r[f"{part}_{nm}_R"] = round(float(t["r_mult"].values[sel].mean()), 2) if sel.sum() >= 5 else np.nan
                r[f"{part}_{nm}_n"] = int(sel.sum())
            spread[part] = r[f"{part}_tinggi_R"] - r[f"{part}_rendah_R"]
        r["konsisten"] = bool(np.isfinite(spread["IS"]) and np.isfinite(spread["OOS"])
                              and np.sign(spread["IS"]) == np.sign(spread["OOS"])
                              and min(abs(spread["IS"]), abs(spread["OOS"])) >= 0.2)
        r["arah"] = "tinggi lebih baik" if spread["OOS"] > 0 else "rendah lebih baik"
        rows.append(r)
    return pd.DataFrame(rows)


def summary(t, label):
    s = trade_stats(t)
    return dict(label=label, trades=s.get("trades", 0), win=s.get("win_rate"), R=s.get("avg_R"),
                t=s.get("t_stat"), pf=s.get("pf"))


def main(data_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    df = add_features(load_raw(data_dir, cache=f"{out_dir}/raw.pkl"))
    regimes = {}
    for ma in GRID["regime_ma"]:
        reg = market_regime(df, ma=ma)
        regimes[ma] = df["Date"].map(reg["regime_on"]).fillna(False).astype(bool)
    m = Market(df)
    last = df["Date"].max()

    grid = run_grid(df, m, regimes)
    ct, rho = config_table(grid)
    ct.to_csv(f"{out_dir}/learn_configs.csv", index=False)

    wf_exp, picks_exp = walk_forward(grid, last)
    wf_roll, picks_roll = walk_forward(grid, last, window_q=4)
    t_def = grid[key(DEFAULT)][1]
    t_def_test = t_def[t_def["sig_date"] >= FIRST_TEST]
    oracle = max(grid, key=lambda k: finished(grid[k][1][grid[k][1]["sig_date"] >= FIRST_TEST])["r_mult"].mean())
    t_or = grid[oracle][1]
    wf = pd.DataFrame([
        summary(t_def_test, f"Config tetap ({key(DEFAULT)})"),
        summary(wf_exp, "Adaptif: pilih terbaik dari semua data lalu"),
        summary(wf_roll, "Adaptif: pilih terbaik dari 4 kuartal terakhir"),
        summary(t_or[t_or["sig_date"] >= FIRST_TEST], f"Oracle / lihat masa depan ({oracle})"),
    ])
    wf.to_csv(f"{out_dir}/learn_walkforward.csv", index=False)
    picks = picks_exp.merge(picks_roll, on="quarter", suffixes=("_semua", "_4q"))

    # fitur: trade Setup S default + trade acak di universe uptrend (sampel lebih besar)
    ready = df["ma200"].notna()
    base = base_filter(df) & ready & regimes[50]
    rows = np.random.default_rng(7).choice(np.flatnonzero(base.values), size=min(15000, int(base.sum())), replace=False)
    t_base = run_trades(m, rows, CFG)
    ft = pd.concat([feature_table(df, t_def, "Setup S"), feature_table(df, t_base, "Uptrend acak")])
    ft.to_csv(f"{out_dir}/learn_features.csv", index=False)

    # keputusan
    d_R = wf.loc[1, "R"] - wf.loc[0, "R"]
    adopt = d_R is not None and d_R >= MARGIN_R
    cons = ft[ft["konsisten"]]
    cons_both = cons.groupby("fitur").filter(lambda g: g["set"].nunique() == 2 and g["arah"].nunique() == 1)

    # lab setup (regime MA50 seperti produksi)
    reg50 = market_regime(df, ma=50)
    df["regime_on"] = regimes[50]
    lab = setup_lab.evaluate(df, reg50)
    lab.to_csv(f"{out_dir}/lab.csv", index=False)

    html, subject = report(last, ct, rho, wf, picks, ft, cons, cons_both, adopt, d_R, grid, lab)
    with open(f"{out_dir}/learn_report.html", "w") as f:
        f.write(html)
    with open(f"{out_dir}/learn_subject.txt", "w") as f:
        f.write(subject)
    pd.set_option("display.width", 250, "display.max_columns", 30)
    print(subject)
    print(ct.to_string(index=False))
    print("Spearman IS vs OOS:", round(rho, 2))
    print(wf.to_string(index=False))
    print(picks.to_string(index=False))
    print(ft[["set", "fitur", "IS_rendah_R", "IS_tengah_R", "IS_tinggi_R", "OOS_rendah_R", "OOS_tengah_R",
              "OOS_tinggi_R", "konsisten", "arah"]].to_string(index=False))


def report(last, ct, rho, wf, picks, ft, cons, cons_both, adopt, d_R, grid, lab=None):
    th = "style='background:#1f3a5f;color:#fff;padding:3px 6px;text-align:left'"
    td = "style='padding:2px 6px;border-bottom:1px solid #eee'"
    o = ["<div style='font-family:Arial,sans-serif;font-size:13px;color:#222;max-width:680px'>",
         f"<h2 style='margin:0;color:#1f3a5f'>Swing IDX — Review Bulanan</h2>"
         f"<div style='color:#666'>Data s/d {tgl(last)}. Target: rata-rata R per trade, bukan win rate.</div>"]
    verdict = (f"<b>Usulan: ganti config.</b> Pemilihan adaptif unggul {d_R:+.2f}R per trade di walk-forward. "
               "Config TIDAK diubah otomatis; balas email ini / minta di chat untuk menerapkan."
               if adopt else
               f"<b>Keputusan: config tetap.</b> Pemilihan adaptif tidak unggul cukup "
               f"(selisih {d_R:+.2f}R, syarat ≥ +{MARGIN_R:.2f}R). Mengganti parameter sekarang = mengejar noise.")
    o.append(f"<p style='background:{'#e6f4ea' if adopt else '#f1f3f4'};padding:8px'>{verdict}</p>")

    o.append("<h3>1. Walk-forward (kuartal uji mulai Jan 2025)</h3><table style='border-collapse:collapse'>"
             f"<tr><th {th}>Cara memilih config</th><th {th}>Trade</th><th {th}>Win</th><th {th}>Rata R</th><th {th}>t</th></tr>")
    for _, r in wf.iterrows():
        o.append(f"<tr><td {td}>{r['label']}</td><td {td}>{r['trades']}</td><td {td}>{r['win']}%</td>"
                 f"<td {td}><b>{r['R']:+.2f}</b></td><td {td}>{r['t']}</td></tr>")
    o.append("</table><p style='color:#666'>Oracle = batas atas yang mustahil dicapai (memilih dengan melihat masa depan). "
             "Jarak besar antara adaptif dan oracle = tanda hasil terbaik itu kebetulan.</p>")

    o.append(f"<h3>2. Stabilitas {len(ct)} config</h3><p>Korelasi peringkat periode 1 vs 2: <b>{rho:+.2f}</b> "
             "(mendekati 0 atau negatif = config terbaik masa lalu tidak memprediksi masa depan).</p>"
             f"<table style='border-collapse:collapse;font-size:12px'><tr><th {th}>Config</th><th {th}>P1 n</th>"
             f"<th {th}>P1 R</th><th {th}>P1 win</th><th {th}>P2 n</th><th {th}>P2 R</th><th {th}>P2 win</th></tr>")
    for _, r in ct.head(8).iterrows():
        o.append(f"<tr><td {td}>{r['config']}{' (default)' if r['config'] == key(DEFAULT) else ''}</td>"
                 f"<td {td}>{r['is_n']}</td><td {td}>{r['is_R']:+.2f}</td><td {td}>{r['is_win']}%</td>"
                 f"<td {td}>{r['oos_n']}</td><td {td}><b>{r['oos_R']:+.2f}</b></td><td {td}>{r['oos_win']}%</td></tr>")
    o.append("</table><p style='color:#666;font-size:11px'>8 teratas menurut periode 2. P1 = sinyal s/d Jun 2025.</p>")

    o.append("<h3>3. Ciri sinyal yang konsisten</h3>")
    if len(cons_both):
        o.append("<p>Konsisten di kedua periode <b>dan</b> di kedua set data (kandidat filter untuk diuji):</p><ul>")
        for f_, g in cons_both.groupby("fitur"):
            o.append(f"<li>{f_}: {g['arah'].iloc[0]}</li>")
        o.append("</ul>")
    else:
        o.append("<p>Belum ada ciri yang konsisten di kedua periode dan kedua set data.</p>")
    if len(cons):
        o.append("<p style='color:#666'>Konsisten di satu set saja: "
                 + "; ".join(f"{r['fitur']} ({r['set']}, {r['arah']})" for _, r in cons.iterrows()) + ".</p>")
    o.append("<p style='color:#888;font-size:11px'>Ciri baru tidak langsung dipakai. Ia masuk daftar uji dan baru "
             "diterapkan bila tetap konsisten di data bulan-bulan berikutnya.</p>")
    layak = []
    if lab is not None:
        o.append(setup_lab.lab_html(lab, th, td.replace("'padding", "'vertical-align:top;padding")).replace(
            "<h3>Lab setup", "<h3>4. Lab setup"))
        layak = lab.loc[lab["status"].str.startswith("LAYAK"), "id"].tolist()
    o.append("</div>")
    subject = (f"Swing IDX — Review bulanan {BULAN[last.month - 1]} {last.year} — "
               + ("USULAN ganti config" if adopt else "config tetap")
               + (f" — kandidat LAYAK: {', '.join(layak)}" if layak else ""))
    return "\n".join(o), subject


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
