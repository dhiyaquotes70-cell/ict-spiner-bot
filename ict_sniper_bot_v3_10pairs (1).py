import requests
import schedule
import time
import numpy as np
import pytz
import os
from datetime import datetime, timedelta

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "7562769095:AAGZWdiC5tMK0JB6TZosYeAEUFWAUS0nK5k")
CHAT_ID        = os.environ.get("CHAT_ID", "895161144")

IST = pytz.timezone('Asia/Kolkata')

# Pair -> Twelve Data symbol
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

active_trades    = []
pending_sweeps   = []
consecutive_loss = 0
asian_ranges     = {}
trade_history    = []

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

# ══════════════════════════════════════
# DATA — Twelve Data FREE (no key for 8 req/min)
# ══════════════════════════════════════
TWELVE_KEY = os.environ.get("TWELVE_DATA_KEY", "demo")

def fetch_candles_twelve(name):
    """Twelve Data — free tier, no key needed for demo"""
    try:
        sym = PAIRS[name].replace("/", "")
        url = (f"https://api.twelvedata.com/time_series"
               f"?symbol={PAIRS[name]}"
               f"&interval=5min"
               f"&outputsize=60"
               f"&apikey={TWELVE_KEY}")
        r    = requests.get(url, timeout=12)
        data = r.json()
        if data.get("status") == "error":
            log(f"{name} Twelve error: {data.get('message','?')}")
            return None
        vals = data.get("values")
        if not vals or len(vals) < 10:
            return None
        candles = []
        for v in reversed(vals):
            candles.append({
                "o": float(v["open"]),
                "h": float(v["high"]),
                "l": float(v["low"]),
                "c": float(v["close"]),
            })
        return candles
    except Exception as e:
        log(f"{name} Twelve fetch error: {e}")
        return None

def fetch_candles_polygon(name):
    """Polygon.io free tier — forex OHLC"""
    try:
        base  = name[:3]
        quote = name[3:]
        end   = now_ist().strftime("%Y-%m-%d")
        start = (now_ist() - timedelta(days=3)).strftime("%Y-%m-%d")
        url   = (f"https://api.polygon.io/v2/aggs/ticker/C:{base}{quote}/range/5/minute"
                 f"/{start}/{end}?adjusted=true&sort=asc&limit=60"
                 f"&apiKey=demo")
        r    = requests.get(url, timeout=12)
        data = r.json()
        results = data.get("results", [])
        if not results or len(results) < 10:
            return None
        candles = [{"o":v["o"],"h":v["h"],"l":v["l"],"c":v["c"]}
                   for v in results]
        return candles
    except Exception as e:
        log(f"{name} Polygon error: {e}")
        return None

def fetch_price_fcsapi(name):
    """FCS API — free forex price"""
    try:
        sym = PAIRS[name].replace("/", "")
        url = f"https://fcsapi.com/api-v3/forex/latest?symbol={sym}&access_key=demo"
        r   = requests.get(url, timeout=10)
        d   = r.json()
        if d.get("status") and d.get("response"):
            p = float(d["response"][0]["c"])
            return p
        return None
    except Exception as e:
        log(f"{name} FCS error: {e}")
        return None

def fetch_price_exchangerate(name):
    """ExchangeRate API — always free, just current price"""
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
    """currencyapi.com — free tier"""
    try:
        base  = name[:3]
        quote = name[3:]
        url   = f"https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/{base.lower()}.json"
        r     = requests.get(url, timeout=10)
        data  = r.json()
        rates = data.get(base.lower(), {})
        price = rates.get(quote.lower())
        return float(price) if price else None
    except Exception as e:
        log(f"{name} CurrencyAPI error: {e}")
        return None

# Price cache — build synthetic candles from live prices
price_cache = {name: [] for name in PAIRS}
CACHE_MAX   = 80

def get_current_price(name):
    """Get latest single price — multiple fallbacks"""
    # Try ExchangeRate first (most reliable)
    p = fetch_price_exchangerate(name)
    if p: return p
    # Try jsdelivr currency API
    p = fetch_price_currencyapi(name)
    if p: return p
    return None

def update_price_cache(name):
    """Add current price to cache as a synthetic candle"""
    price = get_current_price(name)
    if price is None: return False
    cache = price_cache[name]
    if cache:
        last = cache[-1]["c"]
        # Build candle
        candle = {
            "o": last,
            "h": max(last, price),
            "l": min(last, price),
            "c": price,
        }
    else:
        candle = {"o": price, "h": price, "l": price, "c": price}
    cache.append(candle)
    if len(cache) > CACHE_MAX:
        cache.pop(0)
    return True

def get_candles(name):
    """
    Priority:
    1. Twelve Data (real OHLC 5m candles)
    2. Synthetic candles from price cache
    """
    # Try Twelve Data
    candles = fetch_candles_twelve(name)
    if candles and len(candles) >= 20:
        log(f"{name}: Twelve Data OK — {len(candles)} candles, last={candles[-1]['c']:.5f}")
        return candles

    # Use price cache (synthetic)
    cache = price_cache[name]
    if len(cache) >= 20:
        log(f"{name}: Using price cache — {len(cache)} candles")
        return cache

    # Try to build cache now
    ok = update_price_cache(name)
    if ok and len(price_cache[name]) >= 5:
        log(f"{name}: Price cache built — {len(price_cache[name])} points")
        return price_cache[name]

    log(f"{name}: No data available")
    return None

# ══════════════════════════════════════
# HTF BIAS — from daily prices
# ══════════════════════════════════════
daily_prices = {name: [] for name in PAIRS}

def update_daily_price(name):
    p = get_current_price(name)
    if p:
        daily_prices[name].append(p)
        if len(daily_prices[name]) > 20:
            daily_prices[name].pop(0)

def get_htf_bias(name):
    prices = daily_prices[name]
    if len(prices) < 3:
        return "Neutral"
    arr = np.array(prices)
    sma = np.mean(arr[-5:]) if len(arr) >= 5 else np.mean(arr)
    cur = arr[-1]
    if cur > sma and arr[-1] > arr[-3]:   return "Bullish"
    if cur < sma and arr[-1] < arr[-3]:   return "Bearish"
    return "Neutral"

# ══════════════════════════════════════
# ASIAN RANGE
# ══════════════════════════════════════
def update_asian_range(name):
    try:
        h = now_ist().hour + now_ist().minute / 60
        if not (5.0 <= h <= 13.5): return
        candles = price_cache[name][-32:] if len(price_cache[name]) >= 10 else []
        if not candles: return
        asian_ranges[name] = {
            "high": max(c["h"] for c in candles),
            "low":  min(c["l"] for c in candles),
        }
    except: pass

# ══════════════════════════════════════
# STAGE 1 — SWEEP DETECT
# ══════════════════════════════════════
def detect_sweep(candles, htf_bias):
    if len(candles) < 15: return None
    H   = np.array([c["h"] for c in candles])
    L   = np.array([c["l"] for c in candles])
    O   = np.array([c["o"] for c in candles])
    C   = np.array([c["c"] for c in candles])
    n   = len(H)
    atr = np.mean(H[-14:] - L[-14:])
    if atr <= 0: return None

    lb        = min(40, n - 3)
    prev_high = max(H[-lb:-2])
    prev_low  = min(L[-lb:-2])

    # BULLISH SWEEP
    if htf_bias != "Bearish":
        for idx in [-2, -3]:
            if abs(idx) >= n: continue
            if L[idx] < prev_low:
                lwick = min(O[idx], C[idx]) - L[idx]
                if lwick > atr * 0.1:
                    return {
                        "side":        "BUY",
                        "sweep_price": float(min(L[idx], L[-1])),
                        "atr":         float(atr),
                        "prev_low":    float(prev_low),
                        "prev_high":   float(prev_high),
                        "swept_at":    now_ist(),
                    }

    # BEARISH SWEEP
    if htf_bias != "Bullish":
        for idx in [-2, -3]:
            if abs(idx) >= n: continue
            if H[idx] > prev_high:
                uwick = H[idx] - max(O[idx], C[idx])
                if uwick > atr * 0.1:
                    return {
                        "side":        "SELL",
                        "sweep_price": float(max(H[idx], H[-1])),
                        "atr":         float(atr),
                        "prev_low":    float(prev_low),
                        "prev_high":   float(prev_high),
                        "swept_at":    now_ist(),
                    }
    return None

# ══════════════════════════════════════
# STAGE 2 — CONFIRM
# ══════════════════════════════════════
def confirm_entry(candles, sweep, name):
    if len(candles) < 6: return None, {}
    H   = np.array([c["h"] for c in candles])
    L   = np.array([c["l"] for c in candles])
    O   = np.array([c["o"] for c in candles])
    C   = np.array([c["c"] for c in candles])
    atr = sweep["atr"]
    side= sweep["side"]
    cur = float(C[-1])

    if side == "BUY":
        mss = C[-1] > H[-2] or (C[-1] > O[-1] and C[-1] > C[-3])
        fvg = len(H) >= 3 and H[-3] < L[-1]
    else:
        mss = C[-1] < L[-2] or (C[-1] < O[-1] and C[-1] < C[-3])
        fvg = len(L) >= 3 and L[-3] > H[-1]

    if not mss:
        log(f"{name}: No MSS")
        return None, {}

    score  = 0
    if fvg: score += 2

    sh  = max(H[-30:]) if len(H)>=30 else max(H)
    sl2 = min(L[-30:]) if len(L)>=30 else min(L)
    mid = (sh + sl2) / 2
    pd_ok = (side=="BUY" and cur < mid) or (side=="SELL" and cur > mid)
    if pd_ok: score += 2

    ar       = asian_ranges.get(name, {})
    asian_ok = True
    if ar:
        sp = sweep["sweep_price"]
        if side=="BUY" and sp <= ar["low"]:    score += 2
        elif side=="SELL" and sp >= ar["high"]: score += 2
        else: asian_ok = False

    if side == "BUY":
        sl_p = sweep["sweep_price"] - atr * 0.2
        risk = abs(cur - sl_p) or atr * 0.5
        tp1, tp2, tp3 = cur+risk, cur+risk*2, cur+risk*3
    else:
        sl_p = sweep["sweep_price"] + atr * 0.2
        risk = abs(cur - sl_p) or atr * 0.5
        tp1, tp2, tp3 = cur-risk, cur-risk*2, cur-risk*3

    return {
        "side": side, "price": cur,
        "sl": float(sl_p),
        "tp1": float(tp1), "tp2": float(tp2), "tp3": float(tp3),
        "score": score, "fvg": fvg, "pd_zone": pd_ok, "asian_ok": asian_ok,
        "win_tp1": min(55+score*4, 80),
        "win_tp2": min(40+score*4, 68),
        "win_tp3": min(28+score*4, 55),
    }, {}

# ══════════════════════════════════════
# TRADE MONITOR
# ══════════════════════════════════════
def monitor_active_trades():
    global active_trades, consecutive_loss
    for trade in active_trades[:]:
        try:
            curr = get_current_price(trade["name"])
            if curr is None: continue
            name     = trade["name"]
            side     = trade["side"]
            side_tag = "🟢 BUY" if side=="BUY" else "🔴 SELL"

            def hit_tp(level, rr, emoji, amsg):
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
                    "pips": p, "rr": rr, "session": trade.get("session","?"),
                    "date": now_ist().strftime("%d/%m"),
                    "time": now_ist().strftime("%H:%M"),
                })

            def hit_sl():
                p = calc_pips(trade["entry"], trade["sl"], name)
                send_msg(
                    f"📉 <b>STOP LOSS HIT</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"📌 Pair : <b>{name}</b>\n"
                    f"📐 Side : {side_tag}\n"
                    f"💸 Pips : <b>-{p}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━"
                )
                trade_history.append({
                    "name": name, "side": side, "result": "SL",
                    "pips": -p, "rr": "-1R", "session": trade.get("session","?"),
                    "date": now_ist().strftime("%d/%m"),
                    "time": now_ist().strftime("%H:%M"),
                })
                global consecutive_loss
                consecutive_loss += 1
                active_trades.remove(trade)
                if consecutive_loss >= 2:
                    send_msg("⛔ <b>2 Losses! Bot paused.</b>")

            if side == "BUY":
                if   curr >= trade["tp3"] and not trade.get("tp3_hit"):
                    hit_tp("tp3","1:3","🏆","Full target! Closed.")
                    trade["tp3_hit"]=True; consecutive_loss=0; active_trades.remove(trade)
                elif curr >= trade["tp2"] and not trade.get("tp2_hit"):
                    hit_tp("tp2","1:2","🎯","Partial! SL→TP1."); trade["tp2_hit"]=True
                elif curr >= trade["tp1"] and not trade.get("tp1_hit"):
                    hit_tp("tp1","1:1","🔔","SL→Entry pannidhu!"); trade["tp1_hit"]=True
                elif curr <= trade["sl"]: hit_sl()
            else:
                if   curr <= trade["tp3"] and not trade.get("tp3_hit"):
                    hit_tp("tp3","1:3","🏆","Full target! Closed.")
                    trade["tp3_hit"]=True; consecutive_loss=0; active_trades.remove(trade)
                elif curr <= trade["tp2"] and not trade.get("tp2_hit"):
                    hit_tp("tp2","1:2","🎯","Partial! SL→TP1."); trade["tp2_hit"]=True
                elif curr <= trade["tp1"] and not trade.get("tp1_hit"):
                    hit_tp("tp1","1:1","🔔","SL→Entry pannidhu!"); trade["tp1_hit"]=True
                elif curr >= trade["sl"]: hit_sl()
        except Exception as e:
            log(f"Monitor error {trade.get('name','?')}: {e}")

# ══════════════════════════════════════
# REPORTS
# ══════════════════════════════════════
def build_report(title, records):
    if not records:
        send_msg(f"📊 <b>{title}</b>\n\nTrade இல்ல."); return
    total = len(records)
    wins  = [r for r in records if "TP" in r.get("result","")]
    losses= [r for r in records if r.get("result","")=="SL"]
    win_r = round(len(wins)/total*100) if total else 0
    tp1w  = len([r for r in records if r.get("result")=="TP1"])
    tp2w  = len([r for r in records if r.get("result")=="TP2"])
    tp3w  = len([r for r in records if r.get("result")=="TP3"])
    lon   = [r for r in records if r.get("session")=="LON"]
    ny    = [r for r in records if r.get("session")=="NY"]
    def ss(recs):
        return(len(recs),len([r for r in recs if r.get("result")=="TP1"]),
               len([r for r in recs if r.get("result")=="TP2"]),
               len([r for r in recs if r.get("result")=="TP3"]),
               len([r for r in recs if r.get("result")=="SL"]))
    lt,lp1,lp2,lp3,ls = ss(lon)
    nt,np1,np2,np3,ns  = ss(ny)
    tp = round(sum(r.get("pips",0) for r in records),1)
    lines=[
        f"📊 <b>{win_r}% WIN-RATE: {title}</b>",
        f"<code>SESS | TOT | 1:1 | 1:2 | 1:3 | SL</code>",
        f"<code>LON  | {str(lt).ljust(3)} | {str(lp1).ljust(3)} | {str(lp2).ljust(3)} | {str(lp3).ljust(3)} | {ls}</code>",
        f"<code>NY   | {str(nt).ljust(3)} | {str(np1).ljust(3)} | {str(np2).ljust(3)} | {str(np3).ljust(3)} | {ns}</code>",
        f"✅ Wins:<b>{len(wins)}</b> TP1:{tp1w} TP2:{tp2w} TP3:{tp3w}",
        f"❌ Losses:<b>{len(losses)}</b> 🎯 WR:<b>{win_r}%</b> 💰<b>{'+' if tp>=0 else ''}{tp}p</b>",
        f"","📋 <b>LAST TRADES:</b>",
    ]
    for r in records[-8:]:
        icon="✅" if "TP" in r.get("result","") else "❌"
        lines.append(f"<code>{r.get('date','')} {r.get('time','')} | "
                     f"{r.get('session','?')} |</code> {icon} "
                     f"<b>{r.get('name','?')}</b> {r.get('side','?')} "
                     f"→ {r.get('result','?')} ({r.get('rr','?')})")
    send_msg("\n".join(lines))

def send_daily_report():
    today=now_ist().strftime("%d/%m")
    build_report(f"Daily — {now_ist().strftime('%d-%m-%Y')}",
                 [r for r in trade_history if r.get("date")==today])

def send_weekly_report():
    n=now_ist(); dates={(n-timedelta(days=i)).strftime("%d/%m") for i in range(7)}
    build_report(f"Weekly — {n.strftime('%d-%m-%Y')}",
                 [r for r in trade_history if r.get("date") in dates])

def send_monthly_report():
    n=now_ist(); mk=n.strftime("/%m")
    build_report(f"Monthly — {n.strftime('%B %Y')}",
                 [r for r in trade_history if r.get("date","").endswith(mk)])

# ══════════════════════════════════════
# PRICE CACHE UPDATER (runs every 1 min)
# ══════════════════════════════════════
def update_all_prices():
    """Update price cache for all pairs every minute"""
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
    if not is_kill_zone() or consecutive_loss >= 2:
        log(f"Skip scan — KZ:{is_kill_zone()} Loss:{consecutive_loss}")
        return

    monitor_active_trades()
    session = get_session()
    log(f"=== Scanning all pairs | Session: {session} ===")

    # STAGE 2: Confirm pending
    for ps in pending_sweeps[:]:
        name  = ps["name"]
        sweep = ps["sweep"]
        if any(t["name"]==name for t in active_trades):
            pending_sweeps.remove(ps); continue
        elapsed = (now_ist() - sweep["swept_at"]).total_seconds()
        if elapsed < 240: continue

        candles = get_candles(name)
        pending_sweeps.remove(ps)
        if not candles: continue

        entry, _ = confirm_entry(candles, sweep, name)
        if not entry: continue

        d        = decimals(name)
        s        = entry["score"]
        bar      = "🟢"*(s//2)+"⚪"*(4-s//2)
        side     = entry["side"]
        side_tag = "🟢 BUY" if side=="BUY" else "🔴 SELL"
        act_tag  = "📈 BUY NOW" if side=="BUY" else "📉 SELL NOW"
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
            f"💪 Score   : {bar} ({s}/8)\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🧹 Swept   : {round(sweep['sweep_price'],d)}\n"
            f"💰 Entry   : <b>{round(entry['price'],d)}</b>\n"
            f"🛑 SL      : <b>{round(entry['sl'],d)}</b>  (-{p_sl}p)\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🎯 TP1(1:1): <b>{round(entry['tp1'],d)}</b>  +{p_tp1}p  [{entry['win_tp1']}%]\n"
            f"🎯 TP2(1:2): <b>{round(entry['tp2'],d)}</b>  +{p_tp2}p  [{entry['win_tp2']}%]\n"
            f"🏆 TP3(1:3): <b>{round(entry['tp3'],d)}</b>  +{p_tp3}p  [{entry['win_tp3']}%]\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{'✅' if entry['fvg'] else '➖'}FVG  "
            f"{'✅' if entry['pd_zone'] else '➖'}PD  "
            f"{'✅' if entry['asian_ok'] else '➖'}ASIAN"
        )
        active_trades.append({
            "name": name, "side": side, "session": session,
            "entry": entry["price"], "sl": entry["sl"],
            "tp1": entry["tp1"], "tp2": entry["tp2"], "tp3": entry["tp3"],
            "tp1_hit": False, "tp2_hit": False, "tp3_hit": False,
        })
        time.sleep(2)

    # STAGE 1: Sweep scan
    for name in PAIRS:
        if any(t["name"]==name for t in active_trades):  continue
        if any(p["name"]==name for p in pending_sweeps): continue
        try:
            htf_bias = get_htf_bias(name)
            candles  = get_candles(name)
            if not candles:
                log(f"{name}: No candles — skip"); time.sleep(1); continue

            log(f"{name}: {len(candles)} candles | close={candles[-1]['c']:.5f} | HTF={htf_bias}")
            sweep = detect_sweep(candles, htf_bias)
            if not sweep:
                log(f"{name}: No sweep"); time.sleep(1); continue

            # SWEEP FOUND!
            d        = decimals(name)
            side     = sweep["side"]
            side_tag = "🟢 BUY" if side=="BUY" else "🔴 SELL"
            ar       = asian_ranges.get(name, {})
            ar_txt   = (f"\n🌏 Asian: {round(ar.get('low',0),d)}"
                        f"—{round(ar.get('high',0),d)}") if ar else ""
            log(f"{name}: SWEEP {side} at {sweep['sweep_price']:.5f} ← ALERT SENT")

            send_msg(
                f"🧹 <b>SWEEP DETECTED</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📌 Pair  : <b>{name}</b>\n"
                f"📐 Side  : {side_tag}\n"
                f"💧 Swept : {round(sweep['sweep_price'],d)}\n"
                f"{'📈' if htf_bias=='Bullish' else '📉' if htf_bias=='Bearish' else '➡️'}"
                f" HTF   : {htf_bias}{ar_txt}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"⏳ 4 mins wait...\n"
                f"🔴 <i>Confirm வரட்டும் — enter ஆகாதே!</i>"
            )
            pending_sweeps.append({"name": name, "sweep": sweep})
        except Exception as e:
            log(f"Scan error {name}: {e}")
        time.sleep(3)

def main():
    schedule.every(1).minutes.do(update_all_prices)
    schedule.every(4).minutes.do(analyze_all)
    schedule.every().day.at("21:00").do(send_daily_report)
    schedule.every().friday.at("21:00").do(send_weekly_report)
    schedule.every().day.at("21:01").do(
        lambda: send_monthly_report() if now_ist().day==1 else None)

    log("Bot starting — building price cache...")
    send_msg(
        "🤖 <b>ICT Sniper Bot V7 — FIXED!</b>\n\n"
        "🔧 <b>Root Cause Fixed:</b>\n"
        "❌ yfinance — Yahoo Finance blocked on Railway\n"
        "✅ Now using: Twelve Data + ExchangeRate APIs\n"
        "✅ Price cache system (1 min update)\n"
        "✅ No Yahoo Finance dependency\n\n"
        "⏳ Building price cache... 2 mins wait.\n"
        "📡 Then signals will start coming!"
    )

    # Build initial cache
    update_all_prices()
    time.sleep(30)
    update_all_prices()

    analyze_all()
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    main()
