"""Screener swing harian (Setup B: pullback ke MA20 di uptrend) -> email HTML.

Pemakaian: python -I screener.py <data_dir> <out_dir>
Output   : <out_dir>/email.html, <out_dir>/subject.txt, <out_dir>/signals.json

Alur harian:
  ±18:00 WIB data EOD IDX update -> screener jalan -> email malam hari.
  Malam itu pasang auto order beli di IPOT untuk order baru (berlaku 2 hari bursa).
  Setelah terisi, pasang auto order SL dan limit jual TP1. Trailing diumumkan di email.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

from swing import (CFG, IS_END, Market, add_features, base_filter, load_raw, market_regime, plan_levels,
                   round_tick, run_trades, setup_a, setup_b, tick_size, trade_stats)

BLUE, GREEN, RED, GREY = "#1f3a5f", "#137333", "#c5221f", "#666"


def fmt(x, d=0):
    return f"{x:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def rp(x):
    return f"Rp{x/1e6:,.2f} jt".replace(".", ",") if x >= 1e6 else f"Rp{fmt(x/1e3)} rb"


HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def tgl(d, hari=False):
    d = pd.Timestamp(d)
    s = f"{d.day} {BULAN[d.month - 1]} {d.year}"
    return f"{HARI[d.weekday()]}, {s}" if hari else s


def next_bdays(d, n):
    return list(pd.bdate_range(d + pd.Timedelta(days=1), periods=n))


def sizing(trig, sl, c=CFG):
    shares = c["capital"] * c["risk_pct"] / (trig - sl)
    shares = min(shares, c["capital"] * c["max_pos_frac"] / trig)
    lots = int(shares // 100)
    return lots, lots * 100 * trig, lots * 100 * (trig - sl)


def order_row(r, c=CFG):
    trig, sl, tp1, rpct = plan_levels(r["high"], r["ll5"] / r["adj"], r["atr14"] / r["adj"], c)
    lots, dana, risk = sizing(trig, sl, c)
    return dict(code=r["code"], close=float(r["close"]), ret=float(r["ret"]), rs=float(r["rs_pct"]),
                trig=trig, limit=float(trig + 2 * tick_size(trig)),
                cancel=float(round_tick(trig * (1 + c["max_gap"]), up=False)),
                sl=sl, tp1=tp1, rpct=rpct, tppct=tp1 / trig - 1, lots=lots, dana=dana, risk=risk,
                skip=rpct > c["max_stop"])


def paper_book(m, tr, c=CFG):
    """Terapkan aturan portofolio ke trade simulasi: maks 5 posisi, satu posisi per saham."""
    if len(tr) == 0:
        return tr
    tr = tr.sort_values(["fill_di", "score"], ascending=[True, False])
    exit_di = m.di[tr["exit_row"].values]
    keep, active = [], []                       # active: (code, exit_di)
    for k, (_, t) in enumerate(tr.iterrows()):
        d = t["fill_di"]
        active = [a for a in active if a[1] >= d]   # exit di hari d masih dihitung (konservatif)
        if len(active) >= c["max_pos"] or any(a[0] == t["code"] for a in active):
            continue
        active.append((t["code"], np.inf if t["open_end"] else exit_di[k]))
        keep.append(t.name)
    return tr.loc[keep]


def build(data_dir, out_dir):
    df = add_features(load_raw(data_dir, cache=f"{out_dir}/raw.pkl"))
    reg = market_regime(df)
    df["regime_on"] = df["Date"].map(reg["regime_on"]).fillna(False).astype(bool)
    dates = np.sort(df["Date"].unique())
    last, prev = pd.Timestamp(dates[-1]), pd.Timestamp(dates[-2])
    regime_on = bool(reg.loc[last, "regime_on"])
    mkt_dist = float(reg.loc[last, "mkt"] / reg.loc[last, "mkt_ma"] - 1)

    ready = df["ma200"].notna()
    sig = setup_b(df) & ready & df["regime_on"]
    sig_a = setup_a(df) & ready & df["regime_on"]

    # order baru (sinyal hari terakhir)
    new = [order_row(r) for _, r in df[sig & (df["Date"] == last)].sort_values("rs_pct", ascending=False).iterrows()]
    # order kemarin yang belum tersentuh -> masih berlaku besok (hari ke-2)
    carry = []
    for _, r in df[sig & (df["Date"] == prev)].iterrows():
        o = order_row(r)
        t = df[(df["code"] == r["code"]) & (df["Date"] == last)]
        if len(t) == 0:
            continue
        t = t.iloc[0]
        if t["open"] > o["cancel"] or t["high"] >= o["trig"]:
            continue                      # batal (gap) atau sudah terisi -> masuk posisi paper
        carry.append(o)

    # watchlist pra-setup: saham kuat yang sedang koreksi ke dekat MA20, belum ada sinyal
    x = df[(df["Date"] == last) & ready]
    x = x[base_filter(x)].copy()
    x["dist20"] = x["a_close"] / x["ma20"] - 1
    x["depth"] = 1 - x["a_close"] / x["hh15p"]
    zone = x[(x["dist20"].between(-0.02, 0.05)) & (x["depth"].between(0.03, 0.15))
             & ~x["code"].isin([o["code"] for o in new])]
    watch = zone.sort_values("rs_pct", ascending=False).head(10)
    watch = [dict(code=w["code"], close=float(w["close"]), ma20=float(w["ma20"] / w["adj"]),
                  dist20=float(w["dist20"]), depth=float(w["depth"]), rs=float(w["rs_pct"]),
                  high=float(w["high"])) for _, w in watch.iterrows()]

    # paper trade: semua sinyal B (regime ON) 120 hari terakhir
    m = Market(df)
    recent_rows = np.flatnonzero((sig & (df["Date"] >= last - pd.Timedelta(days=120))).values)
    tr = paper_book(m, run_trades(m, recent_rows))
    open_pos = tr[tr["reason"] == "open_end"] if len(tr) else tr
    closed = tr[~tr["open_end"]].sort_values("exit_date").tail(10) if len(tr) else tr
    free = CFG["max_pos"] - len(open_pos)
    held = set(open_pos["code"]) if len(open_pos) else set()
    watch = [w for w in watch if w["code"] not in held]
    for o in new + carry:                    # urut RS tertinggi dulu
        o["note"] = ""
        if o["code"] in held:
            o["note"] = "saham ini sudah dipegang"
        elif not o["skip"] and o["lots"] >= 1:
            if free <= 0:
                o["note"] = f"slot penuh ({CFG['max_pos']} posisi)"
            free -= 1

    # rekam jejak backtest (sinyal B penuh)
    allt = run_trades(m, np.flatnonzero(sig.values))
    st = {p: trade_stats(t) for p, t in (("IS", allt[allt["sig_date"] <= IS_END]),
                                         ("OOS", allt[allt["sig_date"] > IS_END]))}
    today = pd.Timestamp.now(tz="Asia/Jakarta").tz_localize(None).normalize()
    # libur bursa & sumber data dari data/status.json (repo idx-momentum), bila ada
    status_p = os.path.join(os.path.dirname(os.path.abspath(data_dir)), "status.json")
    holidays, source = [], "?"
    if os.path.exists(status_p):
        with open(status_p) as f:
            stj = json.load(f)
        holidays = stj.get("holidays", [])
        source = stj.get("days", {}).get(str(last.date()), {}).get("source", "?")
    # hari bursa sesudah data terakhir s/d hari ini yang datanya belum ada
    stale = int(np.busday_count((last + pd.Timedelta(days=1)).date(), (today + pd.Timedelta(days=1)).date(),
                                holidays=holidays))
    return dict(last=last, regime_on=regime_on, mkt_dist=mkt_dist, new=new, carry=carry, watch=watch,
                open_pos=open_pos, closed=closed, st=st, df_last=df[df["Date"] == last].set_index("code"),
                stale_days=stale, source=source, n_a=int((sig_a & (df["Date"] == last)).sum()))


# ---------------------------------------------------------------- HTML
def card(o, valid_to, label):
    risk_note = (f"<div style='color:{RED}'><b>Lewati:</b> jarak SL {o['rpct']*100:.1f}% &gt; 8%.</div>"
                 if o["skip"] else
                 f"<div style='color:{RED}'><b>Jangan pasang:</b> {o['note']}.</div>" if o.get("note") else "")
    lot_note = (f"<b>{o['lots']} lot</b> · dana {rp(o['dana'])} · risiko {rp(o['risk'])}" if o["lots"] >= 1
                else "<i>Lot &lt; 1 dengan aturan risiko 1%</i>")
    return (f"<div style='border:1px solid #ddd;border-left:4px solid {BLUE};padding:8px 10px;margin:8px 0'>"
            f"<div style='font-size:15px'><b>{o['code']}</b> <span style='color:{GREY}'>{label} · close "
            f"{fmt(o['close'])} ({o['ret']*100:+.1f}%) · RS {o['rs']*100:.0f}</span></div>"
            f"<div>Auto order beli: harga ≥ <b>{fmt(o['trig'])}</b> → limit <b>{fmt(o['limit'])}</b> · "
            f"berlaku s/d {tgl(valid_to, True)}</div>"
            f"<div>Batalkan bila open &gt; <b>{fmt(o['cancel'])}</b> (gap &gt; 3%)</div>"
            f"<div>SL <b style='color:{RED}'>{fmt(o['sl'])}</b> (−{o['rpct']*100:.1f}%) · "
            f"TP1 <b style='color:{GREEN}'>{fmt(o['tp1'])}</b> (+{o['tppct']*100:.1f}%, jual 50%)</div>"
            f"<div>{lot_note}</div>{risk_note}</div>")


def html(r) -> str:
    c = CFG
    last = r["last"]
    nb = next_bdays(last, 2)
    o = ["<div style='font-family:Arial,sans-serif;font-size:13px;color:#222;max-width:640px'>",
         f"<h2 style='margin:0;color:{BLUE}'>Swing IDX — Setup Pullback</h2>",
         f"<div style='color:{GREY}'>Data EOD terakhir: <b>{tgl(last, True)}</b> · modal "
         f"{rp(c['capital'])} · risiko {c['risk_pct']*100:.0f}%/trade · maks {c['max_pos']} posisi</div>"]
    if r["stale_days"] > 2:
        o.append(f"<p style='background:#fff4ce;padding:8px'><b>Data terlambat {r['stale_days']} hari bursa.</b> "
                 "Sumber data belum update. Jangan pasang order baru dari email ini.</p>")
    elif r["stale_days"] >= 1:
        o.append("<p style='background:#fff4ce;padding:8px'><b>Data hari ini belum tersedia</b> "
                 "(libur bursa atau sumber terlambat). Isi email memakai data terakhir di atas; "
                 "masa berlaku order dihitung dari tanggal itu.</p>")
    if r["regime_on"]:
        o.append(f"<p style='background:#e6f4ea;padding:8px'><b>Pasar: ON.</b> Proxy IHSG "
                 f"{r['mkt_dist']*100:+.1f}% di atas MA50. Order baru boleh dipasang.</p>")
    else:
        o.append(f"<p style='background:#fce8e6;padding:8px'><b>Pasar: OFF — tidak buka posisi baru.</b> "
                 f"Proxy IHSG {r['mkt_dist']*100:+.1f}% di bawah MA50. Posisi berjalan tetap dikelola.</p>")

    sec = iter(range(1, 10))
    o.append(f"<h3 style='margin-bottom:4px'>{next(sec)}. Order baru</h3>")
    if r["new"]:
        for x in r["new"]:
            o.append(card(x, nb[1], "sinyal hari ini"))
    else:
        o.append("<p><i>Tidak ada sinyal. Normal — setup bagus jarang.</i></p>")
    if r["carry"]:
        o.append(f"<h3 style='margin-bottom:4px'>{next(sec)}. Order kemarin, masih berlaku besok (hari terakhir)</h3>")
        for x in r["carry"]:
            o.append(card(x, nb[0], "sinyal kemarin"))

    o.append(f"<h3 style='margin-bottom:4px'>{next(sec)}. Posisi berjalan (paper trade)</h3>")
    if len(r["open_pos"]):
        o.append("<p style='color:#666;margin:0'>Berdasarkan simulasi semua sinyal. Bila kamu tidak beli, abaikan.</p>")
        for _, p in r["open_pos"].iterrows():
            px = r["df_last"].loc[p["code"], "close"] if p["code"] in r["df_last"].index else np.nan
            pnl = px / p["entry"] - 1
            if isinstance(p["pending"], str):
                why = {"trail": "close di bawah MA10", "time": "10 hari belum +1R", "max_hold": "60 hari"}[p["pending"]]
                act = f"<b style='color:{RED}'>JUAL {'sisa ' if p['tp_hit'] else ''}di open besok</b> ({why})"
            else:
                act = (f"Tahan. SL sisa <b>{fmt(p['last_stop'])}</b> (sudah BEP)" if p["tp_hit"]
                       else f"Tahan. SL <b>{fmt(p['last_stop'])}</b> · TP1 <b>{fmt(p['tp1'])}</b>")
            o.append(f"<div style='border-bottom:1px solid #eee;padding:4px 0'><b>{p['code']}</b> · masuk "
                     f"{pd.Timestamp(p['fill_date']):%d/%m} di {fmt(p['entry'])} · close {fmt(px)} "
                     f"(<span style='color:{GREEN if pnl >= 0 else RED}'>{pnl*100:+.1f}%</span>)"
                     f"{' · TP1 sudah kena' if p['tp_hit'] else ''}<br>{act}</div>")
    else:
        o.append("<p><i>Tidak ada posisi berjalan.</i></p>")

    o.append(f"<h3 style='margin-bottom:4px'>{next(sec)}. Watchlist: saham kuat mendekati zona pullback</h3>")
    if r["watch"]:
        o.append("<p style='color:#666;margin:0'>Belum ada sinyal. Uptrend, RS 30% teratas, sedang koreksi ke dekat MA20. "
                 "Sinyal muncul bila ada candle hijau yang close di atas high kemarin. Jangan beli sebelum itu.</p>"
                 "<table style='border-collapse:collapse;font-size:12px'><tr style='background:#1f3a5f;color:#fff'>"
                 "<th style='padding:3px 6px'>Kode</th><th style='padding:3px 6px'>Close</th>"
                 "<th style='padding:3px 6px'>MA20</th><th style='padding:3px 6px'>Koreksi</th>"
                 "<th style='padding:3px 6px'>RS</th></tr>")
        for w in r["watch"]:
            o.append(f"<tr><td style='padding:2px 6px'><b>{w['code']}</b></td><td style='padding:2px 6px'>{fmt(w['close'])}</td>"
                     f"<td style='padding:2px 6px'>{fmt(w['ma20'])} ({w['dist20']*100:+.1f}%)</td>"
                     f"<td style='padding:2px 6px'>−{w['depth']*100:.1f}%</td><td style='padding:2px 6px'>{w['rs']*100:.0f}</td></tr>")
        o.append("</table>")
    else:
        o.append("<p><i>Tidak ada saham kuat di zona pullback.</i></p>")

    o.append(f"<h3 style='margin-bottom:4px'>{next(sec)}. Cara pasang di IPOT</h3><ol style='padding-left:18px;margin:4px 0'>"
             "<li><b>Beli</b>: auto order beli dengan harga trigger ≥ <i>Trigger</i>, harga limit = <i>Limit</i>, "
             "masa berlaku 2 hari bursa. Bila menu tidak ada masa berlaku, pasang ulang besok.</li>"
             "<li><b>Cek gap</b>: open bursa 09:00 WIB (10:00 WITA). Bila open &gt; batas batal, hapus order.</li>"
             "<li><b>Setelah terisi</b>: pasang auto order jual SL (trigger ≤ SL, limit 2–3 tick di bawahnya) "
             "dan limit jual 50% lot di TP1.</li>"
             "<li><b>TP1 terisi</b>: ubah SL sisa ke harga beli (BEP). Bila IPOT tidak membatalkan SL otomatis, "
             "kurangi lot SL ke sisa posisi.</li>"
             "<li><b>Keluar sisa</b>: ikuti instruksi \"JUAL di open besok\" di email (trailing MA10 / time stop).</li></ol>")

    if len(r["closed"]):
        o.append(f"<h3 style='margin-bottom:4px'>{next(sec)}. Hasil paper trade terakhir</h3>"
                 "<table style='border-collapse:collapse;font-size:12px'>"
                 "<tr style='background:#1f3a5f;color:#fff'><th style='padding:3px 6px'>Kode</th>"
                 "<th style='padding:3px 6px'>Masuk</th><th style='padding:3px 6px'>Keluar</th>"
                 "<th style='padding:3px 6px'>Net</th><th style='padding:3px 6px'>R</th><th style='padding:3px 6px'>Exit</th></tr>")
        for _, t in r["closed"].iloc[::-1].iterrows():
            col = GREEN if t["net"] > 0 else RED
            o.append(f"<tr><td style='padding:2px 6px'>{t['code']}</td><td style='padding:2px 6px'>"
                     f"{pd.Timestamp(t['fill_date']):%d/%m}</td><td style='padding:2px 6px'>{pd.Timestamp(t['exit_date']):%d/%m}</td>"
                     f"<td style='padding:2px 6px;color:{col}'><b>{t['net']*100:+.1f}%</b></td>"
                     f"<td style='padding:2px 6px'>{t['r_mult']:+.2f}</td><td style='padding:2px 6px'>{t['reason']}</td></tr>")
        o.append("</table>")

    si, so = r["st"]["IS"], r["st"]["OOS"]
    o.append("<h3 style='margin-bottom:4px'>Rekam jejak backtest (net fee IPOT)</h3>"
             f"<p style='margin:0'>Jul 2023–Jun 2025: {si['trades']} trade · win {si['win_rate']}% · "
             f"rata-rata <b style='color:{RED}'>{si['avg_R']:+.2f}R</b><br>"
             f"Jul 2025–sekarang: {so['trades']} trade · win {so['win_rate']}% · rata-rata "
             f"<b style='color:{GREEN}'>{so['avg_R']:+.2f}R</b></p>"
             "<p style='margin:4px 0'><i>Edge belum terbukti: rugi di periode pertama, untung di periode kedua. "
             "Saham uptrend acak dengan exit sama juga menunjukkan pola serupa, jadi sebagian besar hasil "
             "berasal dari kondisi pasar. Fase sekarang: PAPER TRADE.</i></p>")
    o.append(f"<p style='color:#888;font-size:11px'>Setup A (breakout) tidak dikirim: negatif di backtest "
             f"({r['n_a']} sinyal A hari ini). Data: github.com/faza-human/idx-momentum (sumber hari terakhir: {r['source']}). "
             "Proxy IHSG = rata-rata return 100 saham terlikuid. Bukan rekomendasi investasi.</p></div>")
    return "\n".join(o)


if __name__ == "__main__":
    data_dir, out_dir = sys.argv[1], sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)
    r = build(data_dir, out_dir)
    page = html(r)
    with open(f"{out_dir}/email.html", "w") as f:
        f.write(page)
    status = "Pasar ON" if r["regime_on"] else "Pasar OFF"
    n_act = int(r["open_pos"]["pending"].notna().sum()) if len(r["open_pos"]) else 0
    parts = [f"{len(r['new'])} order baru"]
    if r["carry"]:
        parts.append(f"{len(r['carry'])} order lanjutan")
    parts.append(f"{len(r['open_pos'])} posisi" + (f" ({n_act} jual besok)" if n_act else ""))
    parts.append(f"{len(r['watch'])} watchlist")
    subject = f"Swing IDX {tgl(r['last'])} — {status} — " + ", ".join(parts)
    if r["stale_days"] >= 1:
        subject = "[data lama] " + subject
    with open(f"{out_dir}/subject.txt", "w") as f:
        f.write(subject)
    with open(f"{out_dir}/signals.json", "w") as f:
        json.dump(dict(date=str(r["last"].date()), regime_on=r["regime_on"], new=r["new"], carry=r["carry"], watch=r["watch"],
                       open_positions=r["open_pos"][["code", "entry", "last_stop", "tp1", "tp_hit", "pending"]]
                       .to_dict("records") if len(r["open_pos"]) else []), f, indent=1, default=str)
    print(subject)
