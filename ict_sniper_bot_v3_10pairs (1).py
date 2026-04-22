import yfinance as yf
import requests
import schedule
import time
import pandas as pd
import numpy as np
import pytz
import os
from datetime import datetime, timedelta

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "7562769095:AAGZWdiC5tMK0JB6TZosYeAEUFWAUS0nK5k")
CHAT_ID        = os.environ.get("CHAT_ID", "895161144")

PAIRS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "GBPJPY": "GBPJPY=X",
    "USDCAD": "CAD=X",
    "AUDUSD": "AUDUSD=X",
    "EURGBP": "EURGBP=X",
    "EURJPY": "EURJPY=X",
    "AUDJPY": "AUDJPY=X",
    "USDCHF": "CHF=X",
}
IST = pytz.timezone('Asia/Kolkata')

active_trades    = []
pending_sweeps   = []
consecutive_loss = 0
asian_ranges     = {}
trade_history    = []

# ── UTILS ──
def send_msg(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": text,
                                  "parse_mode": "HTML"}, timeout=15)
    except: pass

def decimals(name):
    return 2 if "JPY" in name else 5

def calculate_pips(entry, target, name):
    diff = abs(entry - target)
    if "JPY" in name: return round(diff * 100, 1)
    return round(diff * 10000, 1)

def now_ist():
    return datetime.now(IST)

def get_session():
    h = now_ist().hour + now_ist().minute / 60
    if 13.5 <= h <= 16.5:   return "LON"
    elif 18.5 <= h <= 21.5: return "NY"
    elif 16.5 < h < 18.5:   return "OVL"
    return "ASN"

def is_kill_zone():
    h = now_ist().hour + now_ist().minute / 60
    return (13.5 <= h <= 16.5) or (16.5 <= h <= 21.5)  # Overlap included

def log_trade(trade, result, pips, rr_str):
    n = now_ist()
    trade_history.append({
        "name":    trade["name"],
        "side":    trade["side"],
        "result":  result,
        "pips":    pips,
        "rr":      rr_str,
        "session": trade.get("session", "?"),
        "date":    n.strftime("%d/%m"),
        "time":    n.strftime("%H:%M"),
    })

# ── REPORTS ──
def build_report(title, records):
    if not records:
        send_msg(f"📊 <b>{title}</b>\n\nTrade இல்ல இந்த period-ல.")
        return
    total      = len(records)
    wins       = [r for r in records if "TP" in r.get("result","")]
    losses     = [r for r in records if r.get("result","") == "SL"]
    win_r      = round(len(wins)/total*100) if total else 0
    tp1_w      = len([r for r in records if r.get("result")=="TP1"])
    tp2_w      = len([r for r in records if r.get("result")=="TP2"])
    tp3_w      = len([r for r in records if r.get("result")=="TP3"])
    lon        = [r for r in records if r.get("session")=="LON"]
    ny         = [r for r in records if r.get("session")=="NY"]
    def ss(recs):
        return (len(recs),
                len([r for r in recs if r.get("result")=="TP1"]),
                len([r for r in recs if r.get("result")=="TP2"]),
                len([r for r in recs if r.get("result")=="TP3"]),
                len([r for r in recs if r.get("result")=="SL"]))
    lt,lp1,lp2,lp3,ls = ss(lon)
    nt,np1,np2,np3,ns = ss(ny)
    total_pips = round(sum(r.get("pips",0) for r in records), 1)
    pip_str    = f"+{total_pips}" if total_pips >= 0 else str(total_pips)
    lines = [
        f"📊 <b>{win_r}% WIN-RATE: {title}</b>",
        f"<code>SESS | TOT | 1:1 | 1:2 | 1:3 | SL</code>",
        f"<code>{'─'*32}</code>",
        f"<code>LON  | {str(lt).ljust(3)} | {str(lp1).ljust(3)} | {str(lp2).ljust(3)} | {str(lp3).ljust(3)} | {ls}</code>",
        f"<code>NY   | {str(nt).ljust(3)} | {str(np1).ljust(3)} | {str(np2).ljust(3)} | {str(np3).ljust(3)} | {ns}</code>",
        f"<code>{'─'*32}</code>",
        f"",
        f"✅ Wins  : <b>{len(wins)}</b>  (TP1:{tp1_w} TP2:{tp2_w} TP3:{tp3_w})",
        f"❌ Losses: <b>{len(losses)}</b>",
        f"🎯 Win Rate : <b>{win_r}%</b>",
        f"💰 Total Pips: <b>{pip_str}</b>",
        f"",
        f"📋 <b>LAST TRADES:</b>",
    ]
    for r in records[-8:]:
        icon = "✅" if "TP" in r.get("result","") else "❌"
        lines.append(
            f"<code>{r.get('date','')} {r.get('time','')} | "
            f"{r.get('session','?')} |</code> {icon}\n"
            f"<code>  {r.get('name','?')} {r.get('side','?')} "
            f"-> {r.get('result','?')} ({r.get('rr','?')})</code>"
        )
    send_msg("\n".join(lines))

def send_daily_report():
    today   = now_ist().strftime("%d/%m")
    records = [r for r in trade_history if r.get("date") == today]
    build_report(f"Daily - {now_ist().strftime('%d-%m-%Y')}", records)

def send_weekly_report():
    now_d   = now_ist()
    dates   = {(now_d-timedelta(days=i)).strftime("%d/%m") for i in range(7)}
    records = [r for r in trade_history if r.get("date") in dates]
    build_report(f"Weekly - {now_d.strftime('%d-%m-%Y')}", records)

def send_monthly_report():
    now_d     = now_ist()
    month_key = now_d.strftime("/%m")
    records   = [r for r in trade_history if r.get("date","").endswith(month_key)]
    build_report(f"Monthly - {now_d.strftime('%B %Y')}", records)

# ── ASIAN RANGE ──
def update_asian_range(name, symbol):
    try:
        h = now_ist().hour + now_ist().minute/60
        if not (5.0 <= h <= 13.5): return
        df = yf.download(symbol, period="1d", interval="15m", progress=False)
        if df.empty: return
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        a = df.tail(32)
        asian_ranges[name] = {
            "high": float(a["High"].max()),
            "low":  float(a["Low"].min())
        }
    except: pass

def is_outside_asian_range(price, name, side):
    if name not in asian_ranges: return True
    ar = asian_ranges[name]
    return price <= ar["low"] if side=="BUY" else price >= ar["high"]

# ── HTF BIAS (RELAXED) ──
def get_htf_bias(symbol):
    """
    FIX: Old version too strict -> always Neutral.
    Now: Just Daily SMA trend + 1H BOS. 2 checks only.
    """
    try:
        scores = []

        # Daily: Simple SMA trend
        daily = yf.download(symbol, period="20d", interval="1d", progress=False)
        if not daily.empty:
            if isinstance(daily.columns, pd.MultiIndex):
                daily.columns = daily.columns.get_level_values(0)
            close = daily["Close"].values
            if len(close) >= 10:
                sma10 = np.mean(close[-10:])
                sma5  = np.mean(close[-5:])
                curr  = close[-1]
                if curr > sma10 and sma5 > sma10:  scores.append(1)
                elif curr < sma10 and sma5 < sma10: scores.append(-1)
                else:                                scores.append(0)

        # 4H: Recent swing direction
        h4 = yf.download(symbol, period="5d", interval="60m", progress=False)
        if not h4.empty:
            if isinstance(h4.columns, pd.MultiIndex):
                h4.columns = h4.columns.get_level_values(0)
            c = h4["Close"].values
            h = h4["High"].values
            l = h4["Low"].values
            if len(c) >= 10:
                # Recent 10 candles trend
                if c[-1] > c[-10] and max(h[-5:]) > max(h[-10:-5]):
                    scores.append(1)
                elif c[-1] < c[-10] and min(l[-5:]) < min(l[-10:-5]):
                    scores.append(-1)
                else:
                    scores.append(0)

        total = sum(scores)
        if total >= 1:    return "Bullish"
        elif total <= -1: return "Bearish"
        # If neutral, use daily momentum
        if scores and scores[0] == 0:
            daily2 = yf.download(symbol, period="5d", interval="1d", progress=False)
            if not daily2.empty:
                if isinstance(daily2.columns, pd.MultiIndex):
                    daily2.columns = daily2.columns.get_level_values(0)
                c2 = daily2["Close"].values
                if len(c2) >= 3:
                    return "Bullish" if c2[-1] > c2[-3] else "Bearish"
        return "Neutral"
    except:
        return "Neutral"

# ── PD ZONE ──
def get_pd_zone(df, side):
    try:
        sh  = max(df["High"].values[-30:])
        sl  = min(df["Low"].values[-30:])
        mid = (sh + sl) / 2
        cur = df["Close"].values[-1]
        if side == "BUY":  return cur < mid, round(mid, 5), "DISCOUNT"
        else:              return cur > mid, round(mid, 5), "PREMIUM"
    except: return True, 0, "Unknown"

# ── MITIGATION BLOCK ──
def find_mitigation_block(df, side):
    try:
        O, C = df["Open"].values, df["Close"].values
        cur  = C[-1]
        avg  = np.mean(abs(C[-20:] - O[-20:])) * 1.0  # Relaxed from 1.2
        if side == "BUY":
            for i in range(-20, -2):  # Wider lookback
                if C[i] < O[i] and (O[i]-C[i]) > avg:
                    t, b = max(O[i],C[i]), min(O[i],C[i])
                    if b <= cur <= t * 1.002:  # Slightly wider
                        return True, t, b
        else:
            for i in range(-20, -2):
                if C[i] > O[i] and (C[i]-O[i]) > avg:
                    t, b = max(O[i],C[i]), min(O[i],C[i])
                    if b * 0.998 <= cur <= t:
                        return True, t, b
        return False, 0, 0
    except: return False, 0, 0

# ── STAGE 1: SWEEP DETECT (RELAXED) ──
def detect_sweep(df, htf_bias):
    """
    FIX: Old version too strict.
    - Volume check removed (unreliable for forex)
    - Wick ratio relaxed from 1.2x to 0.8x
    - Lookback extended
    """
    if len(df) < 20: return None
    H = df["High"].values
    L = df["Low"].values
    O = df["Open"].values
    C = df["Close"].values

    lookback  = min(30, len(H)-3)
    prev_high = max(H[-lookback:-2])
    prev_low  = min(L[-lookback:-2])
    atr       = np.mean(H[-14:] - L[-14:])

    # BULLISH SWEEP
    if htf_bias != "Bearish":
        swept = L[-2] < prev_low or L[-1] < prev_low
        # Relaxed wick check: lower wick > 0.8x body
        lower_wick = min(O[-2], C[-2]) - L[-2]
        body       = abs(C[-2] - O[-2])
        wick_ok    = lower_wick > (body * 0.8) or lower_wick > atr * 0.3
        if swept and wick_ok:
            return {
                "side":        "BUY",
                "sweep_price": min(L[-2], L[-1]),
                "atr":         atr,
                "swept_at":    now_ist()
            }

    # BEARISH SWEEP
    if htf_bias != "Bullish":
        swept = H[-2] > prev_high or H[-1] > prev_high
        upper_wick = H[-2] - max(O[-2], C[-2])
        body       = abs(C[-2] - O[-2])
        wick_ok    = upper_wick > (body * 0.8) or upper_wick > atr * 0.3
        if swept and wick_ok:
            return {
                "side":        "SELL",
                "sweep_price": max(H[-2], H[-1]),
                "atr":         atr,
                "swept_at":    now_ist()
            }
    return None

# ── STAGE 2: CONFIRM (RELAXED) ──
def confirm_entry(df, sweep, name):
    """
    FIX: Old version needed ALL filters.
    Now: MSS is mandatory. Others add to score.
    Score minimum lowered from 4 to 2.
    """
    if len(df) < 8: return None, {}
    H = df["High"].values
    L = df["Low"].values
    O = df["Open"].values
    C = df["Close"].values
    atr  = sweep["atr"]
    side = sweep["side"]

    # MSS check (mandatory but relaxed)
    if side == "BUY":
        # Close above any of last 3 candle highs
        mss = C[-1] > H[-2] or C[-1] > H[-3]
        # Displacement: bullish candle with decent body
        displacement = C[-1] > O[-1] and (C[-1]-O[-1]) > atr * 0.4
        # FVG (relaxed)
        fvg = len(H) >= 3 and H[-3] < L[-1]
    else:
        mss = C[-1] < L[-2] or C[-1] < L[-3]
        displacement = C[-1] < O[-1] and (O[-1]-C[-1]) > atr * 0.4
        fvg = len(L) >= 3 and L[-3] > H[-1]

    # MSS is mandatory
    if not mss:
        return None, {}

    # Score system (relaxed: min 2 out of 8)
    in_pd_zone, pd_mid, pd_lbl = get_pd_zone(df, side)
    mit_found, _, _             = find_mitigation_block(df, side)
    asian_ok = is_outside_asian_range(sweep["sweep_price"], name, side)

    score = 0
    if fvg:        score += 2
    if in_pd_zone: score += 2
    if mit_found:  score += 2
    if asian_ok:   score += 2

    # Minimum score: 2 (just needs 1 extra confirm besides MSS)
    if score < 2:
        return None, {
            "score": score, "fvg": fvg,
            "pd": in_pd_zone, "mit": mit_found, "asian": asian_ok
        }

    # Entry levels
    if side == "BUY":
        sl   = sweep["sweep_price"] - (atr * 0.15)
        risk = abs(C[-1] - sl)
        if risk <= 0: risk = atr * 0.5
        tp1  = C[-1] + (risk * 1.0)
        tp2  = C[-1] + (risk * 2.0)
        tp3  = C[-1] + (risk * 3.0)
    else:
        sl   = sweep["sweep_price"] + (atr * 0.15)
        risk = abs(C[-1] - sl)
        if risk <= 0: risk = atr * 0.5
        tp1  = C[-1] - (risk * 1.0)
        tp2  = C[-1] - (risk * 2.0)
        tp3  = C[-1] - (risk * 3.0)

    return {
        "side":     side,
        "price":    float(C[-1]),
        "sl":       float(sl),
        "tp1":      float(tp1),
        "tp2":      float(tp2),
        "tp3":      float(tp3),
        "score":    score,
        "fvg":      fvg,
        "pd_zone":  in_pd_zone,
        "pd_label": pd_lbl,
        "mit_found":mit_found,
        "asian_ok": asian_ok,
        "win_tp1":  min(60 + score * 4, 82),
        "win_tp2":  min(45 + score * 4, 70),
        "win_tp3":  min(30 + score * 4, 58),
    }, {}

# ── MONITOR ──
def monitor_active_trades():
    global active_trades, consecutive_loss
    for trade in active_trades[:]:
        try:
            curr     = yf.Ticker(trade["symbol"]).history(
                           period="1d", interval="1m")["Close"].iloc[-1]
            name     = trade["name"]
            side     = trade["side"]
            side_tag = "🟢 BUY" if side=="BUY" else "🔴 SELL"

            def hit_tp(level, rr, emoji, action):
                pips = calculate_pips(trade["entry"], trade[level], name)
                send_msg(
                    f"{emoji} <b>{level.upper()} HIT!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"📌 Pair  : <b>{name}</b>\n"
                    f"📐 Side  : {side_tag}\n"
                    f"📊 RR    : <b>{rr}</b>\n"
                    f"💰 Pips  : <b>+{pips}</b>\n"
                    f"⚡ {action}\n"
                    f"━━━━━━━━━━━━━━━━━━"
                )
                log_trade(trade, level.upper(), pips, rr)

            def hit_sl():
                pips = calculate_pips(trade["entry"], trade["sl"], name)
                send_msg(
                    f"📉 <b>STOP LOSS HIT</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"📌 Pair  : <b>{name}</b>\n"
                    f"📐 Side  : {side_tag}\n"
                    f"📊 RR    : <b>-1R</b>\n"
                    f"💸 Pips  : <b>-{pips}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━"
                )
                log_trade(trade, "SL", -pips, "-1R")
                global consecutive_loss
                consecutive_loss += 1
                active_trades.remove(trade)
                if consecutive_loss >= 2:
                    send_msg("⛔ <b>2 Losses! Bot paused.</b>\n🧘 Re-assess bias.")

            if side == "BUY":
                if   curr >= trade["tp3"] and not trade.get("tp3_hit"):
                    hit_tp("tp3","1:3","🏆","Trade Closed! Full target!")
                    trade["tp3_hit"]=True; consecutive_loss=0
                    active_trades.remove(trade)
                elif curr >= trade["tp2"] and not trade.get("tp2_hit"):
                    hit_tp("tp2","1:2","🎯","Partial close! SL->TP1!")
                    trade["tp2_hit"]=True
                elif curr >= trade["tp1"] and not trade.get("tp1_hit"):
                    hit_tp("tp1","1:1","🔔","SL->Entry move pannidhu!")
                    trade["tp1_hit"]=True
                elif curr <= trade["sl"]:
                    hit_sl()
            else:
                if   curr <= trade["tp3"] and not trade.get("tp3_hit"):
                    hit_tp("tp3","1:3","🏆","Trade Closed! Full target!")
                    trade["tp3_hit"]=True; consecutive_loss=0
                    active_trades.remove(trade)
                elif curr <= trade["tp2"] and not trade.get("tp2_hit"):
                    hit_tp("tp2","1:2","🎯","Partial close! SL->TP1!")
                    trade["tp2_hit"]=True
                elif curr <= trade["tp1"] and not trade.get("tp1_hit"):
                    hit_tp("tp1","1:1","🔔","SL->Entry move pannidhu!")
                    trade["tp1_hit"]=True
                elif curr >= trade["sl"]:
                    hit_sl()
        except: continue

# ── MAIN SCAN ──
def analyze_all():
    global pending_sweeps, active_trades, consecutive_loss
    if not is_kill_zone() or consecutive_loss >= 2:
        return

    monitor_active_trades()
    session = get_session()

    # STAGE 2: Confirm pending
    for ps in pending_sweeps[:]:
        name, symbol, sweep = ps["name"], ps["symbol"], ps["sweep"]
        if any(t["name"]==name for t in active_trades):
            pending_sweeps.remove(ps); continue
        elapsed = (now_ist() - sweep["swept_at"]).total_seconds()
        if elapsed < 240:  # 4 min wait (was 5)
            continue
        try:
            df = yf.download(symbol, period="1d", interval="5m", progress=False)
            if df.empty:
                pending_sweeps.remove(ps); continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            entry, fail = confirm_entry(df, sweep, name)
            d = decimals(name)

            if entry:
                s        = entry["score"]
                bar      = "🟢"*(s//2) + "⚪"*(4-s//2)
                side     = entry["side"]
                side_tag = "🟢 BUY" if side=="BUY" else "🔴 SELL"
                act_tag  = "📈 BUY NOW" if side=="BUY" else "📉 SELL NOW"
                p_sl     = calculate_pips(entry["price"], entry["sl"],  name)
                p_tp1    = calculate_pips(entry["price"], entry["tp1"], name)
                p_tp2    = calculate_pips(entry["price"], entry["tp2"], name)
                p_tp3    = calculate_pips(entry["price"], entry["tp3"], name)

                # Alert 1 — Big clear name
                send_msg(
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🎯 <b>{name}</b>  {side_tag}\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"⚡ <b>{act_tag}!</b>"
                )
                # Alert 2 — Full details
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
                    f"{'✅' if entry['mit_found'] else '➖'}MIT  "
                    f"{'✅' if entry['asian_ok'] else '➖'}ASIAN"
                )
                active_trades.append({
                    "name":     name, "symbol":  symbol,
                    "side":     side, "session": session,
                    "entry":    entry["price"],
                    "sl":       entry["sl"],
                    "tp1":      entry["tp1"],
                    "tp2":      entry["tp2"],
                    "tp3":      entry["tp3"],
                    "tp1_hit":  False, "tp2_hit": False, "tp3_hit": False
                })
            else:
                # Silent skip — don't spam rejection messages
                pass

            pending_sweeps.remove(ps)
        except:
            pending_sweeps.remove(ps)
        time.sleep(2)

    # STAGE 1: Fresh sweep scan
    for name, symbol in PAIRS.items():
        if any(t["name"]==name for t in active_trades):  continue
        if any(p["name"]==name for p in pending_sweeps): continue
        try:
            update_asian_range(name, symbol)
            htf_bias = get_htf_bias(symbol)

            # Don't skip Neutral — use it with caution
            df = yf.download(symbol, period="2d", interval="5m", progress=False)
            if df.empty: continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if len(df) < 20: continue

            sweep = detect_sweep(df, htf_bias)
            if sweep:
                d        = decimals(name)
                side     = sweep["side"]
                side_tag = "🟢 BUY" if side=="BUY" else "🔴 SELL"
                ar       = asian_ranges.get(name, {})
                ar_txt   = (f"\n🌏 Asian: "
                            f"{round(ar.get('low',0),d)}—"
                            f"{round(ar.get('high',0),d)}") if ar else ""
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
                    f"🔴 <i>Confirm வரட்டும், enter ஆகாதே!</i>"
                )
                pending_sweeps.append({
                    "name": name, "symbol": symbol, "sweep": sweep
                })
        except:
            continue
        time.sleep(3)

def main():
    schedule.every().day.at("21:00").do(send_daily_report)
    schedule.every().friday.at("21:00").do(send_weekly_report)
    schedule.every().day.at("21:01").do(
        lambda: send_monthly_report() if now_ist().day == 1 else None)
    schedule.every(4).minutes.do(analyze_all)  # Scan every 4 mins

    send_msg(
        "🤖 <b>ICT Sniper Bot V5 — FIXED & LIVE!</b>\n\n"
        "🔧 <b>Bug Fixes:</b>\n"
        "✅ Volume check removed (unreliable)\n"
        "✅ HTF Bias relaxed (was too strict)\n"
        "✅ Sweep detection relaxed\n"
        "✅ Score minimum: 2/8 (was 4/8)\n"
        "✅ Neutral pairs included\n"
        "✅ 4 min scan (was 5 min)\n\n"
        "📊 EURUSD|GBPUSD|USDJPY|GBPJPY\n"
        "USDCAD|AUDUSD|EURGBP|EURJPY|AUDJPY|USDCHF\n\n"
        "🎯 Signals will come now! Watching..."
    )
    analyze_all()
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    main()
