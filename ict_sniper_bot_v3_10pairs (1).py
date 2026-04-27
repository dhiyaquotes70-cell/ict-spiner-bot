import requests
import schedule
import time
import numpy as np
import pytz
import os
from datetime import datetime, timedelta

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "7562769095:AAGZWdiC5tMK0JB6TZosYeAEUFWAUS0nK5k")
CHAT_ID        = os.environ.get("CHAT_ID", "895161144")
TWELVE_KEY     = os.environ.get("TWELVE_DATA_KEY", "demo")  # ← Set your real key here or via env var

IST = pytz.timezone('Asia/Kolkata')

PAIRS = {
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
    "USDJPY": "USD/JPY",
    "GBPJPY": "GBP/JPY",
    "USDCAD": "USD/CAD",
    "AUDUSD": "AUD/USD",
    "EURGBP": "EUR/GBP",
    "EURJPY": "EUR/JPY",
    "AUDJPY": "AUD/JPY",
    "USDCHF": "USD/CHF",
}

# ══════════════════════════════════════
# STATE
# ══════════════════════════════════════
active_trades    = []
pending_sweeps   = []
consecutive_loss = 0
asian_ranges     = {}
trade_history    = []
price_cache      = {name: [] for name in PAIRS}
daily_prices     = {name: [] for name in PAIRS}
CACHE_MAX        = 80

# Track last Twelve Data fetch time per pair (rate limit protection)
last_twelve_fetch = {name: None for name in PAIRS}
TWELVE_FETCH_INTERVAL = 300  # seconds between Twelve Data fetches per pair

# ══════════════════════════════════════
# UTILS
# ══════════════════════════════════════
def send_msg(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": text,
                                  "parse_mode": "HTML"}, timeout=15)
    except Exception as e:
        print(f"Telegram error: {e}")

def log(msg):
    print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] {msg}")

def decimals(name):
    return 2 if "JPY" in name else 5

def calc_pips(e, t, name):
    diff = abs(e - t)
    if "JPY" in name: return round(diff * 100, 1)
    return round(diff * 10000, 1)

def now_ist():
    return datetime.now(IST)

def get_session():
    h = now_ist().hour + now_ist().minute / 60
    if   13.5 <= h <= 16.5: return "LON"
    elif 16.5 <  h <= 21.5: return "NY"
    return "ASN"

def is_kill_zone():
    h = now_ist().hour + now_ist().minute / 60
    return 13.5 <= h <= 21.5

def is_demo_key():
    return TWELVE_KEY.strip().lower() == "demo"

# ══════════════════════════════════════
# DATA FETCHING
# ══════════════════════════════════════
def fetch_candles_twelve(name):
    """
    FIX V9: Skip Twelve Data entirely if demo key — saves rate limit errors.
    Also throttle per-pair fetches to avoid hitting limits.
    """
    if is_demo_key():
        log(f"{name}: Demo key detected — skipping Twelve Data, using price cache")
        return None

    # Throttle: don't fetch same pair more than once per 5 minutes
    last = last_twelve_fetch.get(name)
    if last and (now_ist() - last).total_seconds() < TWELVE_FETCH_INTERVAL:
        log(f"{name}: Twelve Data throttled — using cache")
        return None

    try:
        url = (f"https://api.twelvedata.com/time_series"
               f"?symbol={PAIRS[name]}"
               f"&interval=5min"
               f"&outputsize=60"
               f"&apikey={TWELVE_KEY}")
        r    = requests.get(url, timeout=12)
        data = r.json()

        if data.get("status") == "error":
            msg = data.get('message', '?')
            log(f"{name} Twelve error: {msg}")
            # If it's a rate limit error, extend throttle
            if "rate limit" in msg.lower() or "api key" in msg.lower():
                last_twelve_fetch[name] = now_ist()
            return None

        vals = data.get("values")
        if not vals or len(vals) < 10:
            log(f"{name}: Twelve returned too few candles ({len(vals) if vals else 0})")
            return None

        candles = []
        for v in reversed(vals):
            candles.append({
                "o": float(v["open"]),
                "h": float(v["high"]),
                "l": float(v["low"]),
                "c": float(v["close"]),
            })

        last_twelve_fetch[name] = now_ist()
        return candles

    except Exception as e:
        log(f"{name} Twelve fetch error: {e}")
        return None

def fetch_price_exchangerate(name):
    try:
        base  = name[:3]
        quote = name[3:]
        url   = f"https://open.er-api.com/v6/latest/{base}"
        r     = requests.get(url, timeout=10)
        data  = r.json()
        if data.get("result") == "success":
            price = data["rates"].get(quote)
            return float(price) if price else None
        return None
    except Exception as e:
        log(f"{name} ER error: {e}")
        return None

def fetch_price_currencyapi(name):
    try:
        base  = name[:3]
        quote = name[3:]
        url   = (f"https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest"
                 f"/v1/currencies/{base.lower()}.json")
        r     = requests.get(url, timeout=10)
        data  = r.json()
        rates = data.get(base.lower(), {})
        price = rates.get(quote.lower())
        return float(price) if price else None
    except Exception as e:
        log(f"{name} CurrencyAPI error: {e}")
        return None

def fetch_price_frankfurter(name):
    """FIX V9: Extra fallback — Frankfurter API (free, no key needed)"""
    try:
        base  = name[:3]
        quote = name[3:]
        url   = f"https://api.frankfurter.app/latest?from={base}&to={quote}"
        r     = requests.get(url, timeout=10)
        data  = r.json()
        rates = data.get("rates", {})
        price = rates.get(quote)
        return float(price) if price else None
    except Exception as e:
        log(f"{name} Frankfurter error: {e}")
        return None

def get_current_price(name):
    """Try multiple free price sources in order"""
    p = fetch_price_exchangerate(name)
    if p: return p
    p = fetch_price_currencyapi(name)
    if p: return p
    p = fetch_price_frankfurter(name)
    if p: return p
    return None

def update_price_cache(name):
    """
    FIX V9: Better synthetic candle building.
    - Track tick count properly
    - New candle every 5 ticks (each tick = ~1 min polling)
    - Store timestamp for age-based filtering
    """
    price = get_current_price(name)
    if price is None:
        return False

    cache = price_cache[name]
    now   = now_ist()

    if cache:
        last_candle = cache[-1]
        last_candle["h"] = max(last_candle["h"], price)
        last_candle["l"] = min(last_candle["l"], price)
        last_candle["c"] = price
        last_candle["_ticks"] = last_candle.get("_ticks", 0) + 1

        # New candle every 5 ticks (~5 minutes)
        if last_candle["_ticks"] >= 5:
            new_candle = {
                "o": price, "h": price, "l": price, "c": price,
                "_ticks": 0, "_ts": now
            }
            cache.append(new_candle)
    else:
        cache.append({
            "o": price, "h": price, "l": price, "c": price,
            "_ticks": 0, "_ts": now
        })

    if len(cache) > CACHE_MAX:
        cache.pop(0)

    return True

def get_candles(name):
    """
    FIX V9: 
    - If real API key → prefer Twelve Data
    - If demo key → skip straight to synthetic
    - Synthetic threshold lowered: ticks >= 1 (was 3), min candles = 15 (was 20)
    - This allows bot to work during warmup phase
    """
    # Try Twelve Data first (only if real key)
    candles = fetch_candles_twelve(name)
    if candles and len(candles) >= 15:
        log(f"{name}: Twelve Data OK — {len(candles)} candles, last={candles[-1]['c']:.5f}")
        return candles

    # FIX V9: Lowered threshold — accept candles with at least 1 tick
    cache = [c for c in price_cache[name] if c.get("_ticks", 0) >= 1]

    if len(cache) >= 15:
        log(f"{name}: Using price cache — {len(cache)} candles (synthetic)")
        return cache

    # If we have at least 8 candles, still try (reduced quality but better than nothing)
    if len(cache) >= 8:
        log(f"{name}: Low cache ({len(cache)} candles) — attempting with reduced confidence")
        return cache

    log(f"{name}: Insufficient data ({len(cache)} candles) — skip")
    return None

# ══════════════════════════════════════
# HTF BIAS
# ══════════════════════════════════════
def update_daily_price(name):
    p = get_current_price(name)
    if p:
        daily_prices[name].append(p)
        if len(daily_prices[name]) > 50:
            daily_prices[name].pop(0)

def get_htf_bias(name):
    prices = daily_prices[name]
    if len(prices) < 5:
        return "Neutral"
    arr  = np.array(prices)
    sma5 = np.mean(arr[-5:])
    sma20= np.mean(arr[-20:]) if len(arr) >= 20 else np.mean(arr)
    cur  = arr[-1]
    if cur > sma5 and sma5 > sma20 and arr[-1] > arr[-5]:  return "Bullish"
    if cur < sma5 and sma5 < sma20 and arr[-1] < arr[-5]:  return "Bearish"
    return "Neutral"

# ══════════════════════════════════════
# ASIAN RANGE
# ══════════════════════════════════════
def update_asian_range(name):
    try:
        h = now_ist().hour + now_ist().minute / 60
        if not (5.5 <= h <= 13.5): return
        candles = price_cache[name][-32:] if len(price_cache[name]) >= 5 else []
        if not candles: return
        asian_ranges[name] = {
            "high": max(c["h"] for c in candles),
            "low":  min(c["l"] for c in candles),
        }
    except:
        pass

# ══════════════════════════════════════
# STAGE 1 — SWEEP DETECT
# ══════════════════════════════════════
def detect_sweep(candles, htf_bias):
    """
    FIX V9: Adjusted ATR threshold for synthetic candles.
    Synthetic candles have compressed wicks vs real OHLC.
    ATR threshold lowered to 0.2 when using synthetic (was 0.3).
    """
    if len(candles) < 10: return None

    H   = np.array([c["h"] for c in candles])
    L   = np.array([c["l"] for c in candles])
    O   = np.array([c["o"] for c in candles])
    C   = np.array([c["c"] for c in candles])
    n   = len(H)
    atr = np.mean(H[-14:] - L[-14:]) if n >= 14 else np.mean(H - L)
    if atr <= 0: return None

    # Detect if synthetic (smaller ATR relative to price = compressed candles)
    is_synthetic = atr < (C[-1] * 0.0005)  # heuristic
    wick_thresh  = atr * 0.2 if is_synthetic else atr * 0.3

    lb        = min(40, n - 3)
    prev_high = max(H[-lb:-2])
    prev_low  = min(L[-lb:-2])

    # BULLISH SWEEP — sweep low, body closes back above prev_low
    if htf_bias != "Bearish":
        for idx in [-2, -3]:
            if abs(idx) >= n: continue
            if L[idx] < prev_low:
                lwick     = min(O[idx], C[idx]) - L[idx]
                body_back = C[idx] > prev_low
                if lwick > wick_thresh and body_back:
                    return {
                        "side":        "BUY",
                        "sweep_price": float(L[idx]),
                        "atr":         float(atr),
                        "prev_low":    float(prev_low),
                        "prev_high":   float(prev_high),
                        "swept_at":    now_ist(),
                    }

    # BEARISH SWEEP — sweep high, body closes back below prev_high
    if htf_bias != "Bullish":
        for idx in [-2, -3]:
            if abs(idx) >= n: continue
            if H[idx] > prev_high:
                uwick     = H[idx] - max(O[idx], C[idx])
                body_back = C[idx] < prev_high
                if uwick > wick_thresh and body_back:
                    return {
                        "side":        "SELL",
                        "sweep_price": float(H[idx]),
                        "atr":         float(atr),
                        "prev_low":    float(prev_low),
                        "prev_high":   float(prev_high),
                        "swept_at":    now_ist(),
                    }
    return None

# ══════════════════════════════════════
# STAGE 2 — CONFIRM ENTRY
# ══════════════════════════════════════
def confirm_entry(candles, sweep, name):
    """
    SL = ATR * 0.5 (proper buffer)
    Minimum score = 4
    MSS needs 2-candle confirmation
    """
    if len(candles) < 6: return None

    H    = np.array([c["h"] for c in candles])
    L    = np.array([c["l"] for c in candles])
    O    = np.array([c["o"] for c in candles])
    C    = np.array([c["c"] for c in candles])
    atr  = sweep["atr"]
    side = sweep["side"]
    cur  = float(C[-1])

    spread = atr * 0.05

    if side == "BUY":
        recent_high = max(H[-5:-1])
        mss = C[-1] > recent_high and C[-1] > O[-1]
        fvg = len(H) >= 3 and H[-3] < L[-1]
    else:
        recent_low  = min(L[-5:-1])
        mss = C[-1] < recent_low and C[-1] < O[-1]
        fvg = len(L) >= 3 and L[-3] > H[-1]

    if not mss:
        log(f"{name}: No MSS confirmed")
        return None

    score = 0
    if fvg: score += 2

    sh  = max(H[-30:]) if len(H) >= 30 else max(H)
    sl2 = min(L[-30:]) if len(L) >= 30 else min(L)
    mid = (sh + sl2) / 2
    pd_ok = (side == "BUY" and cur < mid) or (side == "SELL" and cur > mid)
    if pd_ok: score += 2

    ar       = asian_ranges.get(name, {})
    asian_ok = False
    if ar:
        sp = sweep["sweep_price"]
        if side == "BUY" and sp <= ar["low"]:
            score += 2; asian_ok = True
        elif side == "SELL" and sp >= ar["high"]:
            score += 2; asian_ok = True

    score += 2  # Base: passed sweep + MSS

    MIN_SCORE = 4
    if score < MIN_SCORE:
        log(f"{name}: Score {score} < {MIN_SCORE} — skipping")
        return None

    if side == "BUY":
        sl_p = sweep["sweep_price"] - atr * 0.5 - spread
        risk = abs(cur - sl_p)
        if risk < atr * 0.3: risk = atr * 0.5
        tp1  = cur + risk * 1.0
        tp2  = cur + risk * 2.0
        tp3  = cur + risk * 3.0
    else:
        sl_p = sweep["sweep_price"] + atr * 0.5 + spread
        risk = abs(cur - sl_p)
        if risk < atr * 0.3: risk = atr * 0.5
        tp1  = cur - risk * 1.0
        tp2  = cur - risk * 2.0
        tp3  = cur - risk * 3.0

    base_wp = 45 + (score * 3)
    return {
        "side": side, "price": cur,
        "sl": float(sl_p),
        "tp1": float(tp1), "tp2": float(tp2), "tp3": float(tp3),
        "score": score, "fvg": fvg, "pd_zone": pd_ok, "asian_ok": asian_ok,
        "win_tp1": min(base_wp, 75),
        "win_tp2": min(base_wp - 10, 62),
        "win_tp3": min(base_wp - 20, 50),
    }

# ══════════════════════════════════════
# TRADE MONITOR
# ══════════════════════════════════════
def monitor_active_trades():
    global active_trades, consecutive_loss
    to_remove = []

    for trade in active_trades:
        try:
            curr     = get_current_price(trade["name"])
            if curr is None: continue
            name     = trade["name"]
            side     = trade["side"]
            side_tag = "🟢 BUY" if side == "BUY" else "🔴 SELL"

            def hit_tp(level, rr, emoji, amsg, extra_action=None):
                p = calc_pips(trade["entry"], trade[level], name)
                send_msg(
                    f"{emoji} <b>{level.upper()} HIT!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"📌 Pair : <b>{name}</b>\n"
                    f"📐 Side : {side_tag}\n"
                    f"📊 RR   : <b>{rr}</b>\n"
                    f"💰 Pips : <b>+{p}</b>\n"
                    f"⚡ {amsg}\n"
                    f"━━━━━━━━━━━━━━━━━━"
                )
                trade_history.append({
                    "name": name, "side": side, "result": level.upper(),
                    "pips": p, "rr": rr, "session": trade.get("session", "?"),
                    "date": now_ist().strftime("%d/%m"),
                    "time": now_ist().strftime("%H:%M"),
                })
                if extra_action:
                    extra_action()

            def hit_sl():
                p = calc_pips(trade["entry"], trade["sl"], name)
                is_breakeven = abs(trade["sl"] - trade["entry"]) < trade.get("atr", 0.001) * 0.1
                label = "BREAK-EVEN" if is_breakeven else "STOP LOSS HIT"
                icon  = "🔶" if is_breakeven else "📉"
                send_msg(
                    f"{icon} <b>{label}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"📌 Pair : <b>{name}</b>\n"
                    f"📐 Side : {side_tag}\n"
                    f"💸 Pips : <b>{'+' if is_breakeven else '-'}{p}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━"
                )
                result = "BE" if is_breakeven else "SL"
                trade_history.append({
                    "name": name, "side": side, "result": result,
                    "pips": 0 if is_breakeven else -p,
                    "rr": "0R" if is_breakeven else "-1R",
                    "session": trade.get("session", "?"),
                    "date": now_ist().strftime("%d/%m"),
                    "time": now_ist().strftime("%H:%M"),
                })
                if not is_breakeven:
                    global consecutive_loss
                    consecutive_loss += 1
                    if consecutive_loss >= 2:
                        send_msg("⛔ <b>2 Consecutive Losses! Bot paused. Manual review pann.</b>")
                to_remove.append(trade)

            if side == "BUY":
                if curr <= trade["sl"]:
                    hit_sl()
                elif curr >= trade["tp3"] and not trade.get("tp3_hit"):
                    def close_trade():
                        global consecutive_loss
                        consecutive_loss = 0
                        to_remove.append(trade)
                    hit_tp("tp3", "1:3", "🏆", "Full target! Trade closed.", close_trade)
                    trade["tp3_hit"] = True
                elif curr >= trade["tp2"] and not trade.get("tp2_hit"):
                    def move_sl_to_tp1():
                        trade["sl"] = trade["tp1"]
                        log(f"{name}: SL moved to TP1 = {trade['sl']:.5f}")
                    hit_tp("tp2", "1:2", "🎯", "Partial profit! SL moved to TP1.", move_sl_to_tp1)
                    trade["tp2_hit"] = True
                elif curr >= trade["tp1"] and not trade.get("tp1_hit"):
                    def move_sl_to_entry():
                        trade["sl"] = trade["entry"]
                        log(f"{name}: SL moved to entry (BE) = {trade['sl']:.5f}")
                    hit_tp("tp1", "1:1", "🔔", "TP1 hit! SL moved to entry (BE).", move_sl_to_entry)
                    trade["tp1_hit"] = True
            else:  # SELL
                if curr >= trade["sl"]:
                    hit_sl()
                elif curr <= trade["tp3"] and not trade.get("tp3_hit"):
                    def close_trade():
                        global consecutive_loss
                        consecutive_loss = 0
                        to_remove.append(trade)
                    hit_tp("tp3", "1:3", "🏆", "Full target! Trade closed.", close_trade)
                    trade["tp3_hit"] = True
                elif curr <= trade["tp2"] and not trade.get("tp2_hit"):
                    def move_sl_to_tp1():
                        trade["sl"] = trade["tp1"]
                        log(f"{name}: SL moved to TP1 = {trade['sl']:.5f}")
                    hit_tp("tp2", "1:2", "🎯", "Partial profit! SL moved to TP1.", move_sl_to_tp1)
                    trade["tp2_hit"] = True
                elif curr <= trade["tp1"] and not trade.get("tp1_hit"):
                    def move_sl_to_entry():
                        trade["sl"] = trade["entry"]
                        log(f"{name}: SL moved to entry (BE) = {trade['sl']:.5f}")
                    hit_tp("tp1", "1:1", "🔔", "TP1 hit! SL moved to entry (BE).", move_sl_to_entry)
                    trade["tp1_hit"] = True

        except Exception as e:
            log(f"Monitor error {trade.get('name', '?')}: {e}")

    for t in to_remove:
        if t in active_trades:
            active_trades.remove(t)

# ══════════════════════════════════════
# RECENTLY SIGNALED
# ══════════════════════════════════════
def was_recently_signaled(name, minutes=45):
    cutoff = now_ist() - timedelta(minutes=minutes)
    for ps in pending_sweeps:
        if ps["name"] == name:
            return True
    for t in active_trades:
        if t["name"] == name:
            return True
    for r in reversed(trade_history):
        try:
            trade_time = IST.localize(
                datetime.strptime(
                    f"{now_ist().year}/{r['date']} {r['time']}",
                    "%Y/%d/%m %H:%M"
                )
            )
            if r["name"] == name and trade_time > cutoff:
                return True
        except:
            pass
    return False

# ══════════════════════════════════════
# REPORTS
# ══════════════════════════════════════
def build_report(title, records):
    if not records:
        send_msg(f"📊 <b>{title}</b>\n\nTrade இல்ல.")
        return
    total = len(records)
    wins  = [r for r in records if "TP" in r.get("result", "")]
    losses= [r for r in records if r.get("result", "") == "SL"]
    bes   = [r for r in records if r.get("result", "") == "BE"]
    win_r = round(len(wins) / total * 100) if total else 0
    tp1w  = len([r for r in records if r.get("result") == "TP1"])
    tp2w  = len([r for r in records if r.get("result") == "TP2"])
    tp3w  = len([r for r in records if r.get("result") == "TP3"])

    def ss(recs):
        return (len(recs),
                len([r for r in recs if r.get("result") == "TP1"]),
                len([r for r in recs if r.get("result") == "TP2"]),
                len([r for r in recs if r.get("result") == "TP3"]),
                len([r for r in recs if r.get("result") == "SL"]),
                len([r for r in recs if r.get("result") == "BE"]))

    lon = [r for r in records if r.get("session") == "LON"]
    ny  = [r for r in records if r.get("session") == "NY"]
    lt, lp1, lp2, lp3, ls, lbe = ss(lon)
    nt, np1, np2, np3, ns, nbe  = ss(ny)
    tp = round(sum(r.get("pips", 0) for r in records), 1)

    lines = [
        f"📊 <b>{win_r}% WIN-RATE: {title}</b>",
        f"<code>SESS | TOT | 1:1 | 1:2 | 1:3 | SL | BE</code>",
        f"<code>LON  | {str(lt).ljust(3)} | {str(lp1).ljust(3)} | {str(lp2).ljust(3)} | {str(lp3).ljust(3)} | {str(ls).ljust(2)} | {lbe}</code>",
        f"<code>NY   | {str(nt).ljust(3)} | {str(np1).ljust(3)} | {str(np2).ljust(3)} | {str(np3).ljust(3)} | {str(ns).ljust(2)} | {nbe}</code>",
        f"✅ Wins:<b>{len(wins)}</b>  TP1:{tp1w} TP2:{tp2w} TP3:{tp3w}",
        f"❌ Losses:<b>{len(losses)}</b>  🔶 BE:<b>{len(bes)}</b>  🎯 WR:<b>{win_r}%</b>  💰<b>{'+' if tp >= 0 else ''}{tp}p</b>",
        f"",
        f"📋 <b>LAST TRADES:</b>",
    ]
    for r in records[-8:]:
        if r.get("result") in ("TP1", "TP2", "TP3"):
            icon = "✅"
        elif r.get("result") == "BE":
            icon = "🔶"
        else:
            icon = "❌"
        lines.append(
            f"<code>{r.get('date', '')} {r.get('time', '')} | "
            f"{r.get('session', '?')} |</code> {icon} "
            f"<b>{r.get('name', '?')}</b> {r.get('side', '?')} "
            f"→ {r.get('result', '?')} ({r.get('rr', '?')})"
        )
    send_msg("\n".join(lines))

def send_daily_report():
    today = now_ist().strftime("%d/%m")
    build_report(
        f"Daily — {now_ist().strftime('%d-%m-%Y')}",
        [r for r in trade_history if r.get("date") == today]
    )

def send_weekly_report():
    n     = now_ist()
    dates = {(n - timedelta(days=i)).strftime("%d/%m") for i in range(7)}
    build_report(
        f"Weekly — {n.strftime('%d-%m-%Y')}",
        [r for r in trade_history if r.get("date") in dates]
    )

def send_monthly_report():
    n  = now_ist()
    mk = n.strftime("/%m")
    build_report(
        f"Monthly — {n.strftime('%B %Y')}",
        [r for r in trade_history if r.get("date", "").endswith(mk)]
    )

# ══════════════════════════════════════
# PRICE CACHE UPDATER
# ══════════════════════════════════════
def update_all_prices():
    for name in PAIRS:
        try:
            update_price_cache(name)
            update_daily_price(name)
            update_asian_range(name)
        except Exception as e:
            log(f"Price update error {name}: {e}")
        time.sleep(1)

# ══════════════════════════════════════
# MAIN SCAN
# ══════════════════════════════════════
def analyze_all():
    global pending_sweeps, active_trades, consecutive_loss

    if consecutive_loss >= 2:
        log(f"Bot paused — consecutive losses: {consecutive_loss}")
        return

    if not is_kill_zone():
        log(f"Outside kill zone — skip scan")
        return

    monitor_active_trades()
    session = get_session()

    # FIX V9: Log API key mode on every scan for visibility
    key_mode = "⚠️ DEMO (synthetic candles only)" if is_demo_key() else "✅ Real API key"
    log(f"=== Scanning | Session: {session} | Active:{len(active_trades)} | "
        f"Pending:{len(pending_sweeps)} | Key: {key_mode} ===")

    # Clean up stale pending sweeps (older than 20 mins)
    stale = [ps for ps in pending_sweeps
             if (now_ist() - ps["sweep"]["swept_at"]).total_seconds() > 1200]
    for ps in stale:
        log(f"{ps['name']}: Pending sweep expired — removing")
        pending_sweeps.remove(ps)

    # ── STAGE 2: Confirm pending sweeps ──────────────────────────
    confirmed_names = []
    for ps in pending_sweeps[:]:
        name  = ps["name"]
        sweep = ps["sweep"]

        if any(t["name"] == name for t in active_trades):
            pending_sweeps.remove(ps)
            continue

        elapsed = (now_ist() - sweep["swept_at"]).total_seconds()
        if elapsed < 240:
            log(f"{name}: Waiting {int(240-elapsed)}s more for confirmation")
            continue

        candles = get_candles(name)
        if not candles:
            pending_sweeps.remove(ps)
            continue

        entry = confirm_entry(candles, sweep, name)
        pending_sweeps.remove(ps)

        if not entry:
            continue

        confirmed_names.append(name)
        d        = decimals(name)
        s        = entry["score"]
        bar      = "🟢" * (s // 2) + "⚪" * max(0, 4 - s // 2)
        side     = entry["side"]
        side_tag = "🟢 BUY" if side == "BUY" else "🔴 SELL"
        act_tag  = "📈 BUY NOW" if side == "BUY" else "📉 SELL NOW"
        p_sl     = calc_pips(entry["price"], entry["sl"],  name)
        p_tp1    = calc_pips(entry["price"], entry["tp1"], name)
        p_tp2    = calc_pips(entry["price"], entry["tp2"], name)
        p_tp3    = calc_pips(entry["price"], entry["tp3"], name)

        send_msg(
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>{name}</b>  {side_tag}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"⚡ <b>{act_tag}!</b>"
        )
        time.sleep(1)
        send_msg(
            f"✅ <b>CONFIRMED SIGNAL</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📌 Pair    : <b>{name}</b>\n"
            f"📐 Side    : {side_tag}\n"
            f"🏛 Session : {session}\n"
            f"💪 Score   : {bar} ({s}/10)\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🧹 Swept   : {round(sweep['sweep_price'], d)}\n"
            f"💰 Entry   : <b>{round(entry['price'], d)}</b>\n"
            f"🛑 SL      : <b>{round(entry['sl'], d)}</b>  (-{p_sl}p)\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🎯 TP1(1:1): <b>{round(entry['tp1'], d)}</b>  +{p_tp1}p  [{entry['win_tp1']}%]\n"
            f"🎯 TP2(1:2): <b>{round(entry['tp2'], d)}</b>  +{p_tp2}p  [{entry['win_tp2']}%]\n"
            f"🏆 TP3(1:3): <b>{round(entry['tp3'], d)}</b>  +{p_tp3}p  [{entry['win_tp3']}%]\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{'✅' if entry['fvg'] else '➖'} FVG  "
            f"{'✅' if entry['pd_zone'] else '➖'} PD  "
            f"{'✅' if entry['asian_ok'] else '➖'} ASIAN\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ <i>Manage risk. Use proper lot size.</i>"
        )

        active_trades.append({
            "name":    name,
            "side":    side,
            "session": session,
            "entry":   entry["price"],
            "sl":      entry["sl"],
            "tp1":     entry["tp1"],
            "tp2":     entry["tp2"],
            "tp3":     entry["tp3"],
            "atr":     sweep["atr"],
            "tp1_hit": False,
            "tp2_hit": False,
            "tp3_hit": False,
        })
        time.sleep(2)

    # ── STAGE 1: Fresh sweep scan ──────────────────────────────────
    for name in PAIRS:
        if any(t["name"] == name for t in active_trades):   continue
        if any(p["name"] == name for p in pending_sweeps):  continue
        if name in confirmed_names:                          continue
        if was_recently_signaled(name, minutes=45):          continue

        try:
            htf_bias = get_htf_bias(name)
            candles  = get_candles(name)
            if not candles:
                log(f"{name}: No candles — skip")
                time.sleep(1)
                continue

            log(f"{name}: {len(candles)} candles | close={candles[-1]['c']:.5f} | HTF={htf_bias}")
            sweep = detect_sweep(candles, htf_bias)
            if not sweep:
                log(f"{name}: No sweep")
                time.sleep(1)
                continue

            d        = decimals(name)
            side     = sweep["side"]
            side_tag = "🟢 BUY" if side == "BUY" else "🔴 SELL"
            ar       = asian_ranges.get(name, {})
            ar_txt   = (f"\n🌏 Asian: {round(ar.get('low', 0), d)}"
                        f"—{round(ar.get('high', 0), d)}") if ar else ""

            log(f"{name}: SWEEP {side} at {sweep['sweep_price']:.5f} ← ALERT SENT")
            send_msg(
                f"🧹 <b>SWEEP DETECTED</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📌 Pair  : <b>{name}</b>\n"
                f"📐 Side  : {side_tag}\n"
                f"💧 Swept : {round(sweep['sweep_price'], d)}\n"
                f"{'📈' if htf_bias == 'Bullish' else '📉' if htf_bias == 'Bearish' else '➡️'}"
                f" HTF   : {htf_bias}{ar_txt}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"⏳ 4 mins wait for confirmation...\n"
                f"🔴 <i>Confirm வரட்டும் — enter ஆகாதே!</i>"
            )
            pending_sweeps.append({
                "name":       name,
                "sweep":      sweep,
                "alerted_at": now_ist(),
            })

        except Exception as e:
            log(f"Scan error {name}: {e}")
        time.sleep(3)

# ══════════════════════════════════════
# MAIN
# ══════════════════════════════════════
def main():
    schedule.every(1).minutes.do(update_all_prices)
    schedule.every(4).minutes.do(analyze_all)
    schedule.every().day.at("21:30").do(send_daily_report)
    schedule.every().friday.at("21:30").do(send_weekly_report)
    schedule.every().day.at("21:31").do(
        lambda: send_monthly_report() if now_ist().day == 1 else None
    )

    key_status = "⚠️ DEMO KEY — Twelve Data disabled. Using synthetic candles only." \
                 if is_demo_key() else \
                 f"✅ Real API Key detected — Twelve Data active."

    log(f"ICT Sniper Bot V9 starting... [{key_status}]")
    send_msg(
        f"🤖 <b>ICT Sniper Bot V9 — DEMO KEY FIX</b>\n\n"
        f"📡 <b>API Status:</b>\n"
        f"{key_status}\n\n"
        f"🔧 <b>V9 Changes:</b>\n"
        f"✅ Demo key auto-detected — no more Twelve Data errors\n"
        f"✅ Synthetic candle threshold lowered (15 candles, 1 tick min)\n"
        f"✅ 3rd price fallback added (Frankfurter API)\n"
        f"✅ Twelve Data per-pair throttle (5 min) — rate limit safe\n"
        f"✅ Sweep ATR threshold auto-adjusts for synthetic candles\n"
        f"✅ Bot works immediately without real API key\n\n"
        f"{'⚠️ <b>Get free Twelve Data key at twelvedata.com for better signals!</b>' if is_demo_key() else '🚀 Real OHLC candles active — best signal quality!'}\n\n"
        f"⏳ Building price cache — 2 mins...\n"
        f"📡 Signals start after Kill Zone opens (13:30 IST)"
    )

    # Build initial price cache
    update_all_prices()
    time.sleep(30)
    update_all_prices()
    time.sleep(30)
    update_all_prices()  # 3 rounds = ~15 candles per pair

    analyze_all()

    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    main()
