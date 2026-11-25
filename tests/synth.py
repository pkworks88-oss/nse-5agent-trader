import numpy as np, pandas as pd

def make_daily(n=900, seed=1, start_px=100.0, drift=0.0004, vol=0.015, end="2026-11-20"):
    rng = np.random.default_rng(seed)
    ret = rng.normal(drift, vol, n)
    c = start_px * np.cumprod(1 + ret)
    o = np.concatenate([[start_px], c[:-1]]) * (1 + rng.normal(0, 0.003, n))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.006, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.006, n)))
    v = rng.integers(8e5, 3e6, n).astype(float)
    idx = pd.bdate_range(end=end, periods=n)
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": v}, index=idx)

def make_universe(k=12, n=900):
    sectors = ["IT", "BANKING", "ENERGY", "AUTO", "PHARMA", "METALS"]
    tiers = ["LOW", "MID", "HIGH"]
    data, meta = {}, {}
    for j in range(k):
        s = f"S{j}.NS"
        data[s] = make_daily(n, seed=10 + j, drift=0.0003 + 0.0001 * (j % 4), vol=0.012 + 0.002 * (j % 3))
        meta[s] = {"tier": tiers[j % 3], "sector": sectors[j % 6], "beta": 0.8 + 0.1 * (j % 7)}
    idx = make_daily(n, seed=99, drift=0.0004, vol=0.008)
    vix = make_daily(n, seed=98, start_px=15, drift=0.0, vol=0.03)
    for col in ("Open", "High", "Low", "Close"):          # keep the fake VIX mean-reverting around 15
        vix[col] = 15 + (vix[col] - vix["Close"].rolling(30, min_periods=1).mean()) * 0.5
    return data, meta, idx, vix
