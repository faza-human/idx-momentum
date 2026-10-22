"""Sumber data EOD IDX. Semua fungsi mengembalikan DataFrame dengan skema COLS (harga mentah,
belum di-adjust) atau melempar SourceError.

Prioritas: IDX resmi -> imq21 -> Yahoo Finance.
- IDX resmi  : idx.co.id GetStockSummary (lengkap: Previous resmi, frekuensi, asing). Sering diblokir
               Cloudflare dari server cloud; dicoba dengan curl_cffi (meniru Chrome) bila terpasang.
- imq21      : sumber yang dipakai repo nofendian17/idx_dataset. Pernah mengirim kolom harga terotasi
               -> dideteksi & diperbaiki di normalize().
- Yahoo      : per saham (kode .JK). Harga Yahoo sudah disesuaikan split -> dikembalikan ke harga mentah
               memakai data split. 'Value' = perkiraan (volume x harga rata-rata OHLC).
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

try:                                    # meniru sidik TLS browser -> lebih jarang diblokir
    from curl_cffi import requests as http
    IMPERSONATE = {"impersonate": "chrome"}
except Exception:                       # pragma: no cover
    import requests as http
    IMPERSONATE = {}

log = logging.getLogger("sources")
WIB = timezone(timedelta(hours=7))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/138.0.0.0 Safari/537.36")
COLS = ["Date", "Stock Code", "Board", "Previous Price", "Last Price", "Open Price", "High Price",
        "Low Price", "Volume", "Value", "Frequency", "Foreign Buy", "Foreign Sell", "Listed Shares", "Source"]
PRICE = ["Previous Price", "Last Price", "Open Price", "High Price", "Low Price"]


class SourceError(Exception):
    pass


def _get(url, headers=None, timeout=25, tries=3, params=None):
    last = None
    for k in range(tries):
        try:
            r = http.get(url, headers={"User-Agent": UA, **(headers or {})}, timeout=timeout,
                         params=params, **IMPERSONATE)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
            if r.status_code in (401, 403, 404):
                break
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        time.sleep(3 * (k + 1))
    raise SourceError(f"{url.split('?')[0]} -> {last}")


# ------------------------------------------------------------------ normalisasi & validasi
def ohlc_ok(p, c, o, h, l, active):
    v = (h >= np.maximum(o, c)) & (l <= np.minimum(o, c)) & (l > 0)
    return float(v[active].mean()) if active.any() else 1.0


def normalize(df: pd.DataFrame, source: str) -> pd.DataFrame:
    """Lengkapi kolom, perbaiki rotasi kolom harga, isi O/H/L kosong saat tidak ada transaksi."""
    df = df.copy()
    for c in COLS:
        if c not in df:
            df[c] = 0 if c not in ("Board", "Source", "Date", "Stock Code") else ""
    df["Source"] = source
    df["Board"] = df["Board"].replace("", "RG").fillna("RG")
    for c in PRICE + ["Volume", "Value", "Frequency", "Foreign Buy", "Foreign Sell", "Listed Shares"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    x = df[PRICE].to_numpy(float)
    act = df["Volume"].to_numpy() > 0
    if ohlc_ok(x[:, 4], x[:, 0], x[:, 1], x[:, 2], x[:, 3], act) > ohlc_ok(*x.T, act):
        log.warning("%s: kolom harga terotasi -> diperbaiki", source)
        x = x[:, [4, 0, 1, 2, 3]]
        df[PRICE] = x
    # tidak ada transaksi: close = previous, O/H/L = close
    no_close = df["Last Price"] <= 0
    df.loc[no_close, "Last Price"] = df.loc[no_close, "Previous Price"]
    for c in ("Open Price", "High Price", "Low Price"):
        z = df[c] <= 0
        df.loc[z, c] = df.loc[z, "Last Price"]
    df["Stock Code"] = df["Stock Code"].astype(str).str.strip().str.upper()
    df = df[df["Stock Code"].str.fullmatch(r"[A-Z0-9]{4}")]
    return df.drop_duplicates("Stock Code")[COLS].sort_values("Stock Code").reset_index(drop=True)


# ------------------------------------------------------------------ IDX resmi
def idx_official(d: date) -> pd.DataFrame:
    r = _get("https://www.idx.co.id/primary/TradingSummary/GetStockSummary",
             params={"length": 9999, "start": 0, "date": d.strftime("%Y%m%d")},
             headers={"Accept": "application/json, text/plain, */*",
                      "Referer": "https://www.idx.co.id/id/data-pasar/ringkasan-perdagangan/ringkasan-saham/"})
    try:
        rows = r.json().get("data", [])
    except json.JSONDecodeError as e:
        raise SourceError(f"IDX: bukan JSON ({r.text[:80]!r})") from e
    if not rows:
        return pd.DataFrame(columns=COLS)
    j = pd.DataFrame(rows)
    got = pd.to_datetime(j["Date"].iloc[0]).date()
    if got != d:
        raise SourceError(f"IDX: tanggal data {got} != {d}")
    df = pd.DataFrame({
        "Date": d.isoformat(), "Stock Code": j["StockCode"], "Board": "RG",
        "Previous Price": j["Previous"], "Last Price": j["Close"], "Open Price": j["OpenPrice"],
        "High Price": j["High"], "Low Price": j["Low"], "Volume": j["Volume"], "Value": j["Value"],
        "Frequency": j.get("Frequency", 0), "Foreign Buy": j.get("ForeignBuy", 0),
        "Foreign Sell": j.get("ForeignSell", 0), "Listed Shares": j.get("ListedShares", 0)})
    return normalize(df, "idx")


# ------------------------------------------------------------------ imq21
def imq21(d: date) -> pd.DataFrame:
    url = "https://imq21.com/daftar-saham"
    r = _get(url, params={"date": d.isoformat(), "_": int(time.time() * 1000)},
             headers={"Accept": "application/json, text/javascript, */*; q=0.01",
                      "Referer": f"{url}?date={d.isoformat()}", "X-Requested-With": "XMLHttpRequest"})
    data = r.json()
    rows = data.get("data", []) if isinstance(data, dict) else data
    if not rows:
        return pd.DataFrame(columns=COLS)
    j = pd.DataFrame(rows)
    dates = pd.to_datetime(j.get("date", pd.Series([d.isoformat()]))).dt.date.unique()
    if len(dates) != 1 or dates[0] != d:
        raise SourceError(f"imq21: tanggal data {list(dates)[:3]} != {d}")
    df = pd.DataFrame({
        "Date": d.isoformat(), "Stock Code": j["list_stock_code"], "Board": j.get("board", "RG"),
        "Previous Price": j["previous"], "Last Price": j["last"], "Open Price": j["open"],
        "High Price": j["high"], "Low Price": j["low"], "Volume": j["tot_volume"], "Value": j["tot_value"]})
    return normalize(df, "imq21")


# ------------------------------------------------------------------ Yahoo
def _yahoo_one(code: str, p1: int, p2: int, decimals: int = 0) -> pd.DataFrame | None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{code}"
    try:
        r = _get(url, params={"period1": p1, "period2": p2, "interval": "1d", "events": "split"},
                 timeout=20, tries=3)
        res = r.json()["chart"]["result"]
    except Exception:  # noqa: BLE001
        return None
    if not res or "timestamp" not in res[0]:
        return None
    res = res[0]
    q = res["indicators"]["quote"][0]
    tz = timedelta(seconds=res.get("meta", {}).get("gmtoffset", 7 * 3600))
    days = [(datetime.fromtimestamp(t, timezone.utc) + tz).date() for t in res["timestamp"]]
    df = pd.DataFrame({"d": days, "o": q["open"], "h": q["high"], "l": q["low"], "c": q["close"],
                       "v": q["volume"]}).dropna(subset=["c"]).drop_duplicates("d", keep="last")
    if df.empty:
        return None
    # kembalikan ke harga mentah: harga sebelum tanggal split x (numerator/denominator)
    splits = res.get("events", {}).get("splits", {})
    sp = sorted(((datetime.fromtimestamp(int(s["date"]), timezone.utc) + tz).date(),
                 s["numerator"] / s["denominator"]) for s in splits.values()) if splits else []
    mult = np.ones(len(df))
    split_today = np.ones(len(df))
    for sd, ratio in sp:
        if ratio <= 0:
            continue
        mult[df["d"].values < sd] *= ratio
        split_today[df["d"].values == sd] = ratio
    for c in ("o", "h", "l", "c"):
        df[c] = np.round(df[c].astype(float) * mult, decimals)
    df["v"] = df["v"].fillna(0)
    # Previous = close kemarin, disesuaikan bila hari ini tanggal split (meniru 'Previous' resmi IDX)
    df["prev"] = (df["c"].shift(1) / split_today).round(decimals)
    df["code"] = code.replace(".JK", "")
    return df


def yahoo(codes, start: date, end: date, workers=8) -> pd.DataFrame:
    """Riwayat harian banyak saham. Return long DataFrame (skema COLS, belum dinormalisasi per tanggal)."""
    p1 = int(datetime(start.year, start.month, start.day, tzinfo=WIB).timestamp()) - 86400 * 10
    p2 = int(datetime(end.year, end.month, end.day, tzinfo=WIB).timestamp()) + 86400
    out, fail = [], 0
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(_yahoo_one, f"{c}.JK", p1, p2): c for c in codes}
        for f in as_completed(futs):
            r = f.result()
            if r is None:
                fail += 1
            else:
                out.append(r)
    log.info("Yahoo: %d saham ok, %d gagal/tidak ada", len(out), fail)
    if not out:
        raise SourceError("Yahoo: semua permintaan gagal")
    y = pd.concat(out, ignore_index=True)
    y = y[(y["d"] >= start) & (y["d"] <= end)]
    df = pd.DataFrame({
        "Date": y["d"].astype(str), "Stock Code": y["code"], "Board": "RG",
        "Previous Price": y["prev"].fillna(y["c"]), "Last Price": y["c"], "Open Price": y["o"],
        "High Price": y["h"], "Low Price": y["l"], "Volume": y["v"],
        "Value": (y["v"] * (y["o"] + y["h"] + y["l"] + y["c"]) / 4).round()})
    return df


def yahoo_day(df_long: pd.DataFrame, d: date) -> pd.DataFrame:
    x = df_long[df_long["Date"] == d.isoformat()]
    return normalize(x, "yahoo") if len(x) else pd.DataFrame(columns=COLS)


def yahoo_index(start: date, end: date, symbol="^JKSE") -> pd.DataFrame:
    p1 = int(datetime(start.year, start.month, start.day, tzinfo=WIB).timestamp())
    p2 = int(datetime(end.year, end.month, end.day, tzinfo=WIB).timestamp()) + 86400
    r = _yahoo_one(symbol, p1, p2, decimals=2)
    if r is None:
        raise SourceError(f"Yahoo {symbol}: gagal")
    return pd.DataFrame({"Date": r["d"].astype(str), "Open": r["o"], "High": r["h"], "Low": r["l"],
                         "Close": r["c"], "Volume": r["v"], "Source": "yahoo"})
