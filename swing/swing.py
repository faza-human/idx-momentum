"""Swing trading IDX: data, indikator, Setup A (breakout) & B (pullback),
simulasi per trade dan simulasi portofolio.

Data: rekap harian IDX (satu CSV per hari) dari github.com/faza-human/idx-momentum (data/daily).
Semua indikator memakai data s/d hari sinyal saja (tanpa look-ahead).
Parameter ditetapkan di muka (aturan umum trader), TIDAK dioptimasi dari data.
"""
from __future__ import annotations

import glob
import os

import numpy as np
import pandas as pd

CFG = dict(
    # universe
    min_value20=5e9,        # rata-rata nilai transaksi 20 hari >= Rp5 M
    min_price=100,
    rs_top=0.30,            # return 126 hari masuk 30% teratas universe likuid
    # Setup A: breakout dari konsolidasi
    a_base_max_range=0.20,  # range 20 hari sebelumnya <= 20% (basis cukup rapat)
    a_vol_mult=1.5,         # volume breakout >= 1.5x rata-rata 20 hari
    a_min_clv=0.5,          # close di setengah atas range harian
    a_max_ret=0.15,         # buang candle sinyal > +15% (kejar harga)
    # Setup B: pullback ke MA20 di uptrend
    b_touch_band=0.02,      # low 3 hari terakhir menyentuh MA20 (+2%)
    b_pull_min=0.03,        # koreksi dari high 15 hari: 3%..15%
    b_pull_max=0.15,
    # entry: buy stop 1 tick di atas high hari sinyal
    entry_valid_days=2,     # berlaku H+1 dan H+2
    max_gap=0.03,           # batal bila open > trigger + 3%
    # stop loss
    atr_mult=1.5,
    swing_lookback=5,
    min_stop=0.02,          # jarak SL minimal 2%
    max_stop=0.08,          # lewati trade bila jarak SL > 8%
    # exit
    tp_r=2.0,               # TP1 di 2R
    tp_frac=0.5,            # jual 50% di TP1, SL sisa pindah ke harga entry
    trail_ma="ma10",        # sisa: keluar open besok bila close < MA10
    time_stop=10,           # 10 hari bursa belum TP1 dan < +1R -> keluar
    max_hold=60,
    # biaya IPOT reguler
    fee_buy=0.0019,
    fee_sell=0.0029,
    slip_ticks=1,           # slippage per eksekusi stop / open
    # portofolio
    capital=30e6,
    risk_pct=0.01,
    max_pos=5,
    max_pos_frac=0.30,
)

CFG.update(stoch=(5, 3, 3), stoch_oversold=20)

IS_END = pd.Timestamp("2025-06-30")
LOCK_DATE = pd.Timestamp("2026-10-23")   # aturan Setup S dikunci; sinyal sesudah ini = uji maju murni


def tick_size(p):
    p = np.asarray(p, dtype=float)
    return np.select([p < 200, p < 500, p < 2000, p < 5000], [1, 2, 5, 10], 25)


def ara_limit(p):
    p = np.asarray(p, dtype=float)
    return np.select([p <= 200, p <= 5000], [0.35, 0.25], 0.20)


def round_tick(p, up=True):
    p = np.asarray(p, dtype=float)
    t = tick_size(p)
    return (np.ceil(p / t - 1e-9) * t) if up else (np.floor(p / t + 1e-9) * t)


# ---------------------------------------------------------------- data
PRICE_COLS = ["prev", "close", "open", "high", "low"]


def _read_day(path: str) -> pd.DataFrame:
    """Baca satu file harian. Sebagian file (Jun-Des 2024, Mei 2025) kolom harganya
    terotasi: 'Previous'=Close, 'Last'=Open, 'Open'=High, 'High'=Low, 'Low'=Previous.
    Format dideteksi dari validitas OHLC (High >= Open/Close >= Low)."""
    d = pd.read_csv(path)
    d = d.rename(columns={"Stock Code": "code", "Volume": "volume", "Value": "value"})
    x = d[["Previous Price", "Last Price", "Open Price", "High Price", "Low Price"]].to_numpy(float)
    act = d["volume"].to_numpy() > 0

    def ok(p, c, o, h, l):
        v = (h >= np.maximum(o, c)) & (l <= np.minimum(o, c)) & (l > 0)
        return v[act].mean() if act.any() else 1.0

    labeled = ok(*x.T)
    rotated = ok(x[:, 4], x[:, 0], x[:, 1], x[:, 2], x[:, 3])
    if rotated > labeled:
        x = x[:, [4, 0, 1, 2, 3]]
    for k, col in enumerate(PRICE_COLS):
        d[col] = x[:, k]
    d["fmt_rotated"] = rotated > labeled
    return d[["Date", "code", *PRICE_COLS, "volume", "value", "fmt_rotated"]]


def load_raw(data_dir: str, cache: str | None = None) -> pd.DataFrame:
    files = sorted(glob.glob(os.path.join(data_dir, "stock_data_*.csv")))
    if cache and os.path.exists(cache):
        df = pd.read_pickle(cache)
        if df["Date"].max() >= pd.Timestamp(os.path.basename(files[-1])[11:21]):
            return df
    df = pd.concat((_read_day(f) for f in files), ignore_index=True)
    df["Date"] = pd.to_datetime(df["Date"])
    # hari libur: file ada tapi hampir tidak ada transaksi -> buang.
    # Catatan: file Okt 2023-Jun 2024 hanya berisi saham A-E (parsial) -> tetap dipakai,
    # saham lain disaring lewat syarat kelengkapan data (cov_ok).
    active = df[df["volume"] > 0].groupby("Date").size()
    good = active[active >= 0.05 * active.median()].index
    df = df[df["Date"].isin(good)]
    df = df[df["prev"] > 0].copy()
    df.loc[df["close"] <= 0, "close"] = df["prev"]        # isi close dulu, baru O/H/L
    for c in ("open", "high", "low"):
        df[c] = df[c].where(df[c] > 0, df["close"])
    df = df.drop_duplicates(["code", "Date"]).sort_values(["code", "Date"]).reset_index(drop=True)
    if cache:
        df.to_pickle(cache)
    return df


def _roll(g, col, fn, n, shift=0, minp=None):
    minp = minp or n
    return g[col].transform(lambda s: getattr(s.shift(shift).rolling(n, min_periods=minp), fn)())


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    dates = np.sort(df["Date"].unique())
    df["di"] = df["Date"].map(pd.Series(np.arange(len(dates)), index=dates)).values

    g = df.groupby("code", sort=False)
    prev_close = g["close"].shift(1)
    consecutive = (df["di"] - g["di"].shift(1)) == 1
    # faktor corporate action (split/right issue): prev resmi vs close kemarin
    f = np.where(consecutive & (prev_close > 0), df["prev"] / prev_close, 1.0)
    f = np.where((f > 0.98) & (f < 1.02), 1.0, f)
    # hari setelah file bursa yang hilang: banyak saham "diskontinu" serentak -> bukan aksi korporasi
    disc = pd.Series(f != 1.0).groupby(df["Date"].values).transform("mean").values
    f = np.where(disc > 0.05, 1.0, f)
    df["ca_f"] = f
    rev = df.iloc[::-1]
    cum = rev.groupby("code", sort=False)["ca_f"].cumprod()
    df["adj"] = (cum / rev["ca_f"]).iloc[::-1].values
    for c in ("open", "high", "low", "close"):
        df["a_" + c] = df[c] * df["adj"]

    g = df.groupby("code", sort=False)
    df["ret"] = df["close"] / df["prev"] - 1
    for n in (10, 20, 50, 200):
        df[f"ma{n}"] = _roll(g, "a_close", "mean", n)
    df["ma50_prev10"] = g["ma50"].shift(10)
    df["vol20"] = _roll(g, "volume", "mean", 20, shift=1, minp=15)
    df["value20"] = _roll(g, "value", "mean", 20, shift=1, minp=15)
    df["vol3p"] = _roll(g, "volume", "mean", 3, shift=1)          # 3 hari sebelum sinyal
    df["hh20"] = _roll(g, "a_high", "max", 20, shift=1, minp=15)   # sebelum hari ini
    df["ll20"] = _roll(g, "a_low", "min", 20, shift=1, minp=15)
    df["hh15p"] = _roll(g, "a_high", "max", 15, shift=1, minp=10)
    df["ll3"] = _roll(g, "a_low", "min", 3)                        # termasuk hari ini
    df["ll5"] = _roll(g, "a_low", "min", CFG["swing_lookback"])
    df["ma20_min3"] = _roll(g, "ma20", "min", 3)
    df["prev_high"] = g["a_high"].shift(1)
    df["ret126"] = df["a_close"] / g["a_close"].shift(126) - 1
    pc = g["a_close"].shift(1)
    tr = np.maximum(df["a_high"] - df["a_low"],
                    np.maximum((df["a_high"] - pc).abs(), (df["a_low"] - pc).abs()))
    df["atr14"] = tr.groupby(df["code"]).transform(lambda s: s.rolling(14, min_periods=10).mean())
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    df["clv"] = ((df["close"] - df["low"]) / rng).fillna(0.5)
    df["volratio"] = df["volume"] / df["vol20"]
    df["is_ara"] = df["ret"] >= ara_limit(df["prev"]) - 0.01

    # Stochastic 5,3,3 (harga adjusted): %K mentah 5 hari -> SMA3 = %K, SMA3 dari %K = %D
    n, sk, sd = CFG["stoch"]
    hh = g["a_high"].transform(lambda s: s.rolling(n).max())
    ll = g["a_low"].transform(lambda s: s.rolling(n).min())
    k_raw = 100 * (df["a_close"] - ll) / (hh - ll).replace(0, np.nan)
    df["stoch_k"] = k_raw.groupby(df["code"]).transform(lambda s: s.rolling(sk).mean())
    df["stoch_d"] = df.groupby("code")["stoch_k"].transform(lambda s: s.rolling(sd).mean())
    gk = df.groupby("code", sort=False)
    df["stoch_k_prev"], df["stoch_d_prev"] = gk["stoch_k"].shift(1), gk["stoch_d"].shift(1)
    df["stoch_kmin3"] = gk["stoch_k"].transform(lambda s: s.rolling(3).min())

    # data lengkap: 200 baris terakhir saham ini mencakup <= 205 hari dataset
    df["cov_ok"] = (df["di"] - g["di"].shift(199)) <= 205
    liquid = ((df["value20"] >= CFG["min_value20"]) & (df["close"] >= CFG["min_price"])
              & df["cov_ok"])
    df["liquid"] = liquid
    df["rs_pct"] = np.nan
    lq = df[liquid & df["ret126"].notna()]
    df.loc[lq.index, "rs_pct"] = lq.groupby("Date")["ret126"].rank(pct=True)
    return df


def market_regime(df: pd.DataFrame, n_liquid: int = 100, ma: int = 50) -> pd.DataFrame:
    """Proxy IHSG = rata-rata return harian 100 saham terlikuid. Regime ON bila > MA50."""
    x = df[df["value20"].notna()].copy()
    x["rk"] = x.groupby("Date")["value20"].rank(ascending=False)
    m = x[x["rk"] <= n_liquid].groupby("Date")["ret"].mean().clip(-0.2, 0.2)
    idx = (1 + m).cumprod().to_frame("mkt")
    idx["mkt_ma"] = idx["mkt"].rolling(ma).mean()
    idx["regime_on"] = idx["mkt"] > idx["mkt_ma"]
    return idx


# ---------------------------------------------------------------- setup
def base_filter(df, c=CFG):
    return (df["liquid"]
            & (df["rs_pct"] >= 1 - c["rs_top"])
            & (df["a_close"] > df["ma50"]) & (df["ma50"] > df["ma200"])
            & (df["ma50"] > df["ma50_prev10"])
            & (~df["is_ara"]))


def setup_a(df, c=CFG):
    return (base_filter(df, c)
            & (df["hh20"] / df["ll20"] - 1 <= c["a_base_max_range"])
            & (df["a_close"] > df["hh20"])
            & (df["volratio"] >= c["a_vol_mult"])
            & (df["clv"] >= c["a_min_clv"])
            & (df["ret"] <= c["a_max_ret"]))


def setup_b(df, c=CFG):
    depth = 1 - df["ll3"] / df["hh15p"]
    return (base_filter(df, c)
            & (df["ll3"] <= df["ma20_min3"] * (1 + c["b_touch_band"]))   # sentuh MA20
            & (depth >= c["b_pull_min"]) & (depth <= c["b_pull_max"])
            & (df["vol3p"] < df["vol20"])                                 # koreksi volume kering
            & (df["a_close"] > df["prev_high"])                           # close tembus high kemarin
            & (df["a_close"] > df["a_open"])
            & (df["a_close"] >= df["ma20"]))


def pullback_zone(df, c=CFG):
    depth = 1 - df["ll3"] / df["hh15p"]
    return ((df["ll3"] <= df["ma20_min3"] * (1 + c["b_touch_band"]))
            & (depth >= c["b_pull_min"]) & (depth <= c["b_pull_max"]))


def setup_s_parts(df, c=CFG):
    """Setup S (utama sejak 23 Okt 2026) = gabungan dua varian Stochastic 5,3,3.
    v1: Setup B + Stochastic %K sempat < 20 dalam 3 hari terakhir.
    v2: zona pullback (sama dengan B) + %K memotong %D ke atas hari ini + %K sempat < 20 dalam 3 hari."""
    oversold = df["stoch_kmin3"] < c["stoch_oversold"]
    cross_up = (df["stoch_k"] > df["stoch_d"]) & (df["stoch_k_prev"] <= df["stoch_d_prev"])
    v1 = setup_b(df, c) & oversold
    v2 = base_filter(df, c) & pullback_zone(df, c) & cross_up & oversold
    return v1, v2


def setup_s(df, c=CFG):
    v1, v2 = setup_s_parts(df, c)
    return v1 | v2


# ---------------------------------------------------------------- trade plan (dipakai backtest & screener)
def plan_levels(high, low5, atr, c=CFG):
    """Semua input harga raw (belum di-adjust). Return trigger, SL, TP1, R%."""
    trig = float(round_tick(high + tick_size(high), up=True))
    sl_swing = low5 - tick_size(low5)
    sl_atr = trig - c["atr_mult"] * atr
    sl = max(sl_swing, sl_atr)
    if trig - sl < c["min_stop"] * trig:
        sl = trig * (1 - c["min_stop"])
    sl = float(round_tick(sl, up=False))
    r = trig - sl
    tp1 = float(round_tick(trig + c["tp_r"] * r, up=True))
    return trig, sl, tp1, r / trig


# ---------------------------------------------------------------- simulasi per trade
class Market:
    """Array numpy per baris (urut code, Date) untuk simulasi cepat."""

    def __init__(self, df):
        self.df = df
        self.code = df["code"].values
        self.di = df["di"].values
        self.adj = df["adj"].values
        for k in ("a_open", "a_high", "a_low", "a_close", "ma10", "ma20", "atr14", "ll5"):
            setattr(self, k, df[k].values.astype(float))
        last = np.r_[self.code[1:] != self.code[:-1], True]
        self.end = np.zeros(len(df), dtype=np.int64)
        ends = np.flatnonzero(last)
        starts = np.r_[0, ends[:-1] + 1]
        for s, e in zip(starts, ends):
            self.end[s:e + 1] = e


def sim_trade(m: Market, i: int, c=CFG):
    """Sinyal di baris i. Harga di-skala ke raw hari sinyal (adj_i) supaya tick benar."""
    s = 1.0 / m.adj[i]            # adjusted -> raw hari sinyal
    O, H, L, C = m.a_open * s, m.a_high * s, m.a_low * s, m.a_close * s
    trail = getattr(m, c["trail_ma"]) * s
    end = m.end[i]
    trig, sl, tp1, rpct = plan_levels(H[i], m.ll5[i] * s, m.atr14[i] * s, c)
    if not np.isfinite(rpct) or rpct > c["max_stop"]:
        return None
    slip = c["slip_ticks"]

    # ---- entry
    fj, entry = None, None
    for k in range(1, c["entry_valid_days"] + 1):
        j = i + k
        if j > end or m.di[j] - m.di[j - 1] > 3:     # data habis / lubang data
            return None
        if O[j] > trig * (1 + c["max_gap"]):
            return None
        if O[j] >= trig:
            entry = O[j] + tick_size(O[j]) * slip
        elif H[j] >= trig:
            entry = trig + tick_size(trig) * slip
        else:
            continue
        fj = j
        break
    if fj is None:
        return None
    R = trig - sl

    # ---- exit
    rem, stop, tp_hit, pending = 1.0, sl, False, None
    fills = []                    # (row, frac, px, reason)
    j = fj
    while True:
        bars = j - fj
        if j > fj and m.di[j] - m.di[j - 1] > 3:      # lubang data: tutup di close terakhir, dikecualikan
            fills.append((j - 1, rem, C[j - 1], "data_gap")); rem = 0; break
        if j > fj:
            if pending:
                fills.append((j, rem, O[j] - tick_size(O[j]) * slip, pending)); rem = 0; break
            if O[j] <= stop:
                fills.append((j, rem, O[j] - tick_size(O[j]) * slip, "gap_sl" if not tp_hit else "gap_be"))
                rem = 0; break
            if not tp_hit and O[j] >= tp1:
                fills.append((j, c["tp_frac"], O[j], "tp1")); rem -= c["tp_frac"]
                tp_hit, stop = True, max(stop, entry)
        if L[j] <= stop:
            fills.append((j, rem, stop - tick_size(stop) * slip, "sl" if not tp_hit else "be"))
            rem = 0; break
        if not tp_hit and H[j] >= tp1:
            fills.append((j, c["tp_frac"], tp1, "tp1")); rem -= c["tp_frac"]
            tp_hit, stop = True, max(stop, entry)
        if tp_hit and C[j] < trail[j]:
            pending = "trail"
        elif not tp_hit and bars >= c["time_stop"] and C[j] < entry + R:
            pending = "time"
        elif bars >= c["max_hold"]:
            pending = "max_hold"
        if j + 1 > end:
            fills.append((j, rem, C[j], "open_end")); rem = 0; break
        j += 1

    proceeds = sum(fr * px * (1 - c["fee_sell"]) for _, fr, px, _ in fills)
    cost = entry * (1 + c["fee_buy"])
    return dict(sig_row=i, fill_row=fj, exit_row=fills[-1][0], trig=trig, entry=entry,
                sl=sl, tp1=tp1, R=R, rpct=rpct, fills=fills,
                net=proceeds / cost - 1, r_mult=(proceeds - cost) / R,
                hold=fills[-1][0] - fj, reason=fills[-1][3], tp_hit=tp_hit,
                last_stop=stop, pending=pending, rem=sum(f[1] for f in fills if f[3] == "open_end"),
                open_end=fills[-1][3] in ("open_end", "data_gap"))


def run_trades(m: Market, rows, c=CFG) -> pd.DataFrame:
    out = []
    for i in rows:
        t = sim_trade(m, int(i), c)
        if t:
            out.append(t)
    t = pd.DataFrame(out)
    if len(t) == 0:
        return t
    d = m.df
    t["code"] = d["code"].values[t["sig_row"]]
    t["sig_date"] = d["Date"].values[t["sig_row"]]
    t["fill_date"] = d["Date"].values[t["fill_row"]]
    t["exit_date"] = d["Date"].values[t["exit_row"]]
    t["fill_di"] = m.di[t["fill_row"]]
    t["score"] = d["rs_pct"].values[t["sig_row"]]
    return t


def unique_trades(m: Market, t: pd.DataFrame) -> pd.DataFrame:
    """Satu posisi per saham: sinyal baru diabaikan selama trade sebelumnya di saham yang sama masih jalan.
    Mencegah satu kenaikan yang sama terhitung berkali-kali."""
    if len(t) == 0:
        return t
    t = t.sort_values(["fill_di", "score"], ascending=[True, False])
    busy, keep = {}, []
    for i, r in t.iterrows():
        if busy.get(r["code"], -1) >= r["fill_di"]:
            continue
        busy[r["code"]] = np.inf if r["open_end"] else m.di[r["exit_row"]]
        keep.append(i)
    return t.loc[keep]


# ---------------------------------------------------------------- portofolio
def portfolio(m: Market, trades: pd.DataFrame, n_days: int, start_di=0, c=CFG):
    """Modal awal c['capital'], risiko 1% equity/trade, maks 5 posisi, maks 30% equity/posisi.
    Hanya trade yang sudah selesai (bukan open_end) dihitung untuk statistik."""
    t = trades[trades["fill_di"] >= start_di].sort_values(["fill_di", "score"], ascending=[True, False])
    by_day = {d: g for d, g in t.groupby("fill_di")}
    cash, equity = c["capital"], c["capital"]
    pos = []              # dict(code, shares, rows, fills, entry_cost)
    curve, taken = [], []
    for d in range(start_di, n_days):
        # entry hari d
        if d in by_day:
            for _, tr in by_day[d].iterrows():
                if len(pos) >= c["max_pos"] or any(p["code"] == tr["code"] for p in pos):
                    continue
                sh = equity * c["risk_pct"] / tr["R"]
                sh = min(sh, equity * c["max_pos_frac"] / tr["entry"], cash / (tr["entry"] * (1 + c["fee_buy"])))
                lots = int(sh // 100)
                if lots < 1:
                    continue
                shares = lots * 100
                cash -= shares * tr["entry"] * (1 + c["fee_buy"])
                pos.append(dict(code=tr["code"], shares=shares, rem=1.0, fills=list(tr["fills"]),
                                fill_row=tr["fill_row"], sig_row=tr["sig_row"], r_mult=tr["r_mult"],
                                net=tr["net"], cost=shares * tr["entry"] * (1 + c["fee_buy"])))
        # exit hari d + mark to market
        mv = 0.0
        keep = []
        for p in pos:
            for f in [f for f in p["fills"] if m.di[f[0]] == d]:
                cash += p["shares"] * f[1] * f[2] * (1 - c["fee_sell"])
                p["rem"] -= f[1]
            if p["rem"] <= 1e-9:
                taken.append(dict(code=p["code"], sig_row=p["sig_row"], pnl_rp=None, r_mult=p["r_mult"], net=p["net"],
                                  rp=p["net"] * p["cost"]))
                continue
            # harga close posisi di hari d (baris terakhir code ini dengan di <= d)
            r0, r1 = p["fill_row"], m.end[p["fill_row"]]
            k = r0 + np.searchsorted(m.di[r0:r1 + 1], d, side="right") - 1
            mv += p["shares"] * p["rem"] * m.a_close[k] / m.adj[p["sig_row"]]
            keep.append(p)
        pos = keep
        equity = cash + mv
        curve.append((d, equity, len(pos)))
    cv = pd.DataFrame(curve, columns=["di", "equity", "n_pos"])
    return cv, pd.DataFrame(taken)


# ---------------------------------------------------------------- statistik
def trade_stats(t: pd.DataFrame) -> dict:
    t = t[~t["open_end"]] if len(t) else t
    if len(t) < 2:
        return dict(trades=len(t))
    r = t["r_mult"]
    w, l = r[r > 0].sum(), -r[r < 0].sum()
    return dict(
        trades=int(len(r)),
        win_rate=round(float((t["net"] > 0).mean()) * 100, 1),
        tp1_rate=round(float(t["tp_hit"].mean()) * 100, 1),
        avg_R=round(float(r.mean()), 3),
        median_R=round(float(r.median()), 3),
        t_stat=round(float(r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))), 2),
        pf=round(float(w / l), 2) if l > 0 else None,
        avg_net_pct=round(float(t["net"].mean()) * 100, 2),
        avg_hold=round(float(t["hold"].mean()), 1),
    )


def curve_stats(cv: pd.DataFrame, dates, capital) -> dict:
    eq = cv["equity"].values
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    yrs = (pd.Timestamp(dates[cv["di"].iloc[-1]]) - pd.Timestamp(dates[cv["di"].iloc[0]])).days / 365.25
    total = eq[-1] / capital - 1
    return dict(final_equity=round(eq[-1] / 1e6, 2), total_ret_pct=round(total * 100, 1),
                cagr_pct=round(((1 + total) ** (1 / yrs) - 1) * 100, 1) if yrs > 0 else None,
                max_dd_pct=round(dd * 100, 1), avg_pos=round(cv["n_pos"].mean(), 2),
                years=round(yrs, 2))
