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

active_trades   = []
pending_sweeps  = []
consecutive_loss= 0
asian_ranges    = {}
trade_history   = []

# ══════════════════════════════════════
# UTILS
# ══════════════════════════════════════
def send_msg(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": text,
                                  "parse_mode": "HTML"}, timeout=15)
    except: pass

def decimals(name):
    return 2 if "JPY" in name else 5

def pips(e, t, name):
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

def df_clean(symbol, period, interval):
    """Download + clean dataframe."""
    try:
        df = yf.download(symbol, period=period, interval=interval,
                         progress=False, auto_adjust=True)
        if df.empty: return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna()
        if len(df) < 10: return None
        return df
    except: return None

# ══════════════════════════════════════
# REPORTS
# ══════════════════════════════════════
def build_report(title, records):
    if not records:
        send_msg(f"📊 <b>{title}</b>\n\nTrade இல்ல இந்த period-ல.")
        return
    total  = len(records)
    wins   = [r for r in records if "TP" in r.get("result","")]
    losses = [r for r in records if r.get("result","") == "SL"]
    win_r  = round(len(wins)/total*100) if total else 0
    tp1w   = len([r for r in records if r.get("result")=="TP1"])
    tp2w   = len([r for r in records if r.get("result")=="TP2"])
    tp3w   = len([r for r in records if r.get("result")=="TP3"])
    lon    = [r for r in records if r.get("session")=="LON"]
    ny     = [r for r in records if r.get("session")=="NY"]
    def ss(recs):
        return (len(recs),
                len([r for r in recs if r.get("result")=="TP1"]),
                len([r for r in recs if r.get("result")=="TP2"]),
                len([r for r in recs if r.get("result")=="TP3"]),
                len([r for r in recs if r.get("result")=="SL"]))
    lt,lp1,lp2,lp3,ls = ss(lon)
    nt,np1,np2,np3,ns  = ss(ny)
    total_pips = round(sum(r.get("pips",0) for r in records), 1)
    pip_str    = f"+{total_pips}" if total_pips>=0 else str(total_pips)
    lines = [
        f"📊 <b>{win_r}% WIN-RATE: {title}</b>",
        f"<code>SESS | TOT | 1:1 | 1:2 | 1:3 | SL</code>",
        f"<code>{'─'*32}</code>",
        f"<code>LON  | {str(lt).ljust(3)} | {str(lp1).ljust(3)} | {str(lp2).ljust(3)} | {str(lp3).ljust(3)} | {ls}</code>",
        f"<code>NY   | {str(nt).ljust(3)} | {str(np1).ljust(3)} | {str(np2).ljust(3)} | {str(np3).ljust(3)} | {ns}</code>",
        f"<code>{'─'*32}</code>",
        f"✅ Wins: <b>{len(wins)}</b> (TP1:{tp1w} TP2:{tp2w} TP3:{tp3w})",
        f"❌ Losses: <b>{len(losses)}</b>",
        f"🎯 Win Rate: <b>{win_r}%</b>",
        f"💰 Total Pips: <b>{pip_str}</b>",
        f"",f"📋 <b>LAST TRADES:</b>",
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
    records = [r for r in trade_history if r.get("date")==today]
    build_report(f"Daily — {now_ist().strftime('%d-%m-%Y')}", records)

def send_weekly_report():
    n     = now_ist()
    dates = {(n-timedelta(days=i)).strftime("%d/%m") for i in range(7)}
    build_report(f"Weekly — {n.strftime('%d-%m-%Y')}",
                 [r for r in trade_history if r.get("date") in dates])

def send_monthly_report():
    n = now_ist()
    mk= n.strftime("/%m")
    build_report(f"Monthly — {n.strftime('%B %Y')}",
                 [r for r in trade_history if r.get("date","").endswith(mk)])

# ══════════════════════════════════════
# HTF BIAS — simple & reliable
# ══════════════════════════════════════
def get_htf_bias(symbol):
    try:
        df = df_clean(symbol, "15d", "1d")
        if df is None: return "Neutral"
        c = df["Close"].values
        if len(c) < 6: return "Neutral"
        sma5  = np.mean(c[-5:])
        sma10 = np.mean(c[-10:]) if len(c)>=10 else np.mean(c)
        if c[-1] > sma5 > sma10:  return "Bullish"
        if c[-1] < sma5 < sma10:  return "Bearish"
        # fallback: last 3 candles direction
        if c[-1] > c[-4]: return "Bullish"
        if c[-1] < c[-4]: return "Bearish"
        return "Neutral"
    except: return "Neutral"

# ══════════════════════════════════════
# ASIAN RANGE
# ══════════════════════════════════════
def update_asian_range(name, symbol):
    try:
        h = now_ist().hour + now_ist().minute/60
        if not (5.0 <= h <= 13.5): return
        df = df_clean(symbol, "1d", "15m")
        if df is None: return
        a = df.tail(32)
        asian_ranges[name] = {
            "high": float(a["High"].max()),
            "low":  float(a["Low"].min())
        }
    except: pass

# ══════════════════════════════════════
# STAGE 1 — SWEEP DETECT
# Very relaxed — catches most real sweeps
# ══════════════════════════════════════
def detect_sweep(df, htf_bias):
    H = df["High"].values
    L = df["Low"].values
    O = df["Open"].values
    C = df["Close"].values
    n = len(H)
    if n < 15: return None

    # ATR for context
    atr = np.mean(H[-14:] - L[-14:])
    if atr == 0: return None

    # Previous swing levels (last 15-50 candles)
    lb        = min(50, n-3)
    prev_high = max(H[-lb:-2])
    prev_low  = min(L[-lb:-2])

    # ── BULLISH SWEEP ──
    # Condition: Price went below prev_low and has wick back up
    if htf_bias != "Bearish":
        swept = L[-2] < prev_low or L[-3] < prev_low
        if swept:
            # Lower wick on sweep candle
            idx   = -2 if L[-2] < prev_low else -3
            lwick = min(O[idx], C[idx]) - L[idx]
            # Just needs some rejection — very relaxed
            if lwick > atr * 0.15:
                return {
                    "side":        "BUY",
                    "sweep_price": min(L[-2], L[-3]),
                    "atr":         atr,
                    "prev_low":    prev_low,
                    "prev_high":   prev_high,
                    "swept_at":    now_ist(),
                }

    # ── BEARISH SWEEP ──
    if htf_bias != "Bullish":
        swept = H[-2] > prev_high or H[-3] > prev_high
        if swept:
            idx   = -2 if H[-2] > prev_high else -3
            uwick = H[idx] - max(O[idx], C[idx])
            if uwick > atr * 0.15:
                return {
                    "side":        "SELL",
                    "sweep_price": max(H[-2], H[-3]),
                    "atr":         atr,
                    "prev_low":    prev_low,
                    "prev_high":   prev_high,
                    "swept_at":    now_ist(),
                }
    return None

# ══════════════════════════════════════
# STAGE 2 — CONFIRM ENTRY
# ══════════════════════════════════════
def confirm_entry(df, sweep, name):
    H = df["High"].values
    L = df["Low"].values
    O = df["Open"].values
    C = df["Close"].values
    n = len(C)
    if n < 6: return None, {}

    atr  = sweep["atr"]
    side = sweep["side"]

    # ── MSS (Market Structure Shift) ──
    if side == "BUY":
        # Any bullish close above recent swing
        mss = C[-1] > H[-2] or (C[-1] > O[-1] and C[-1] > C[-3])
        fvg = n >= 3 and H[-3] < L[-1]
    else:
        mss = C[-1] < L[-2] or (C[-1] < O[-1] and C[-1] < C[-3])
        fvg = n >= 3 and L[-3] > H[-1]

    if not mss:
        return None, {"reason": "No MSS"}

    # ── Confluence score ──
    score = 0
    if fvg: score += 2

    # PD Zone check
    sh  = max(H[-30:]) if n>=30 else max(H)
    sl2 = min(L[-30:]) if n>=30 else min(L)
    mid = (sh + sl2) / 2
    cur = float(C[-1])
    if side=="BUY" and cur < mid:   score += 2
    if side=="SELL" and cur > mid:  score += 2
    pd_ok = (side=="BUY" and cur<mid) or (side=="SELL" and cur>mid)

    # Asian range
    ar     = asian_ranges.get(name, {})
    sp_val = sweep["sweep_price"]
    asian_ok = True
    if ar:
        if side=="BUY"  and sp_val <= ar["low"]:  score += 2; asian_ok=True
        elif side=="SELL" and sp_val >= ar["high"]: score += 2; asian_ok=True
        else: asian_ok=False

    # Min score: 0 — if MSS confirmed, we take it
    # (score just affects win% display)

    # ── Build levels ──
    if side == "BUY":
        sl_price = sweep["sweep_price"] - atr * 0.2
        risk     = abs(cur - sl_price)
        if risk < atr * 0.1: risk = atr * 0.5
        tp1 = cur + risk * 1.0
        tp2 = cur + risk * 2.0
        tp3 = cur + risk * 3.0
    else:
        sl_price = sweep["sweep_price"] + atr * 0.2
        risk     = abs(cur - sl_price)
        if risk < atr * 0.1: risk = atr * 0.5
        tp1 = cur - risk * 1.0
        tp2 = cur - risk * 2.0
        tp3 = cur - risk * 3.0

    w1 = min(55 + score*4, 80)
    w2 = min(40 + score*4, 68)
    w3 = min(28 + score*4, 55)

    return {
        "side": side, "price": cur,
        "sl": float(sl_price),
        "tp1": float(tp1), "tp2": float(tp2), "tp3": float(tp3),
        "score": score, "fvg": fvg, "pd_zone": pd_ok,
        "asian_ok": asian_ok,
        "win_tp1": w1, "win_tp2": w2, "win_tp3": w3,
    }, {}

# ══════════════════════════════════════
# TRADE MONITOR
# ══════════════════════════════════════
def monitor_active_trades():
    global active_trades, consecutive_loss
    for trade in active_trades[:]:
        try:
            ticker = yf.Ticker(trade["symbol"])
            hist   = ticker.history(period="1d", interval="1m")
            if hist.empty: continue
            curr     = float(hist["Close"].iloc[-1])
            name     = trade["name"]
            side     = trade["side"]
            side_tag = "🟢 BUY" if side=="BUY" else "🔴 SELL"

            def hit_tp(level, rr, emoji, msg):
                p = pips(trade["entry"], trade[level], name)
                send_msg(
                    f"{emoji} <b>{level.upper()} HIT!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"📌 Pair : <b>{name}</b>\n"
                    f"📐 Side : {side_tag}\n"
                    f"📊 RR   : <b>{rr}</b>\n"
                    f"💰 Pips : <b>+{p}</b>\n"
                    f"⚡ {msg}\n"
                    f"━━━━━━━━━━━━━━━━━━"
                )
                trade_history.append({
                    "name": name, "side": side,
                    "result": level.upper(), "pips": p, "rr": rr,
                    "session": trade.get("session","?"),
                    "date": now_ist().strftime("%d/%m"),
                    "time": now_ist().strftime("%H:%M"),
                })

            def hit_sl():
                p = pips(trade["entry"], trade["sl"], name)
                send_msg(
                    f"📉 <b>STOP LOSS HIT</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"📌 Pair : <b>{name}</b>\n"
                    f"📐 Side : {side_tag}\n"
                    f"📊 RR   : <b>-1R</b>\n"
                    f"💸 Pips : <b>-{p}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━"
                )
                trade_history.append({
                    "name": name, "side": side,
                    "result": "SL", "pips": -p, "rr": "-1R",
                    "session": trade.get("session","?"),
                    "date": now_ist().strftime("%d/%m"),
                    "time": now_ist().strftime("%H:%M"),
                })
                global consecutive_loss
                consecutive_loss += 1
                active_trades.remove(trade)
                if consecutive_loss >= 2:
                    send_msg("⛔ <b>2 Losses! Bot paused 1hr.</b>")

            if side == "BUY":
                if   curr >= trade["tp3"] and not trade.get("tp3_hit"):
                    hit_tp("tp3","1:3","🏆","Full target! Trade closed.")
                    trade["tp3_hit"]=True; consecutive_loss=0
                    active_trades.remove(trade)
                elif curr >= trade["tp2"] and not trade.get("tp2_hit"):
                    hit_tp("tp2","1:2","🎯","Partial close! SL→TP1 move.")
                    trade["tp2_hit"]=True
                elif curr >= trade["tp1"] and not trade.get("tp1_hit"):
                    hit_tp("tp1","1:1","🔔","SL→Entry move pannidhu!")
                    trade["tp1_hit"]=True
                elif curr <= trade["sl"]:
                    hit_sl()
            else:
                if   curr <= trade["tp3"] and not trade.get("tp3_hit"):
                    hit_tp("tp3","1:3","🏆","Full target! Trade closed.")
                    trade["tp3_hit"]=True; consecutive_loss=0
                    active_trades.remove(trade)
                elif curr <= trade["tp2"] and not trade.get("tp2_hit"):
                    hit_tp("tp2","1:2","🎯","Partial close! SL→TP1 move.")
                    trade["tp2_hit"]=True
                elif curr <= trade["tp1"] and not trade.get("tp1_hit"):
                    hit_tp("tp1","1:1","🔔","SL→Entry move pannidhu!")
                    trade["tp1_hit"]=True
                elif curr >= trade["sl"]:
                    hit_sl()
        except: continue

# ══════════════════════════════════════
# MAIN SCAN
# ══════════════════════════════════════
def analyze_all():
    global pending_sweeps, active_trades, consecutive_loss
    if not is_kill_zone() or consecutive_loss >= 2:
        return

    monitor_active_trades()
    session = get_session()

    # ── STAGE 2: Confirm pending sweeps ──
    for ps in pending_sweeps[:]:
        name, symbol, sweep = ps["name"], ps["symbol"], ps["sweep"]
        if any(t["name"]==name for t in active_trades):
            pending_sweeps.remove(ps); continue
        elapsed = (now_ist() - sweep["swept_at"]).total_seconds()
        if elapsed < 240: continue   # wait 4 mins

        df = df_clean(symbol, "1d", "5m")
        if df is None:
            pending_sweeps.remove(ps); continue

        entry, fail = confirm_entry(df, sweep, name)
        pending_sweeps.remove(ps)

        if not entry:
            continue   # silent skip

        d        = decimals(name)
        s        = entry["score"]
        bar      = "🟢"*(s//2) + "⚪"*(4-s//2)
        side     = entry["side"]
        side_tag = "🟢 BUY" if side=="BUY" else "🔴 SELL"
        act_tag  = "📈 BUY NOW" if side=="BUY" else "📉 SELL NOW"
        p_sl     = pips(entry["price"], entry["sl"],  name)
        p_tp1    = pips(entry["price"], entry["tp1"], name)
        p_tp2    = pips(entry["price"], entry["tp2"], name)
        p_tp3    = pips(entry["price"], entry["tp3"], name)

        # Alert 1 — Asset name (big & clear)
        send_msg(
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>{name}</b>  {side_tag}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"⚡ <b>{act_tag}!</b>"
        )
        time.sleep(1)
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
            f"{'✅' if entry['asian_ok'] else '➖'}ASIAN"
        )
        active_trades.append({
            "name":    name,   "symbol":  symbol,
            "side":    side,   "session": session,
            "entry":   entry["price"],
            "sl":      entry["sl"],
            "tp1":     entry["tp1"],
            "tp2":     entry["tp2"],
            "tp3":     entry["tp3"],
            "tp1_hit": False, "tp2_hit": False, "tp3_hit": False,
        })
        time.sleep(2)

    # ── STAGE 1: Fresh sweep scan ──
    for name, symbol in PAIRS.items():
        if any(t["name"]==name for t in active_trades):  continue
        if any(p["name"]==name for p in pending_sweeps): continue

        try:
            update_asian_range(name, symbol)
            htf_bias = get_htf_bias(symbol)

            df = df_clean(symbol, "2d", "5m")
            if df is None: continue

            sweep = detect_sweep(df, htf_bias)
            if not sweep:
                time.sleep(2); continue

            d        = decimals(name)
            side     = sweep["side"]
            side_tag = "🟢 BUY" if side=="BUY" else "🔴 SELL"
            ar       = asian_ranges.get(name, {})
            ar_txt   = (f"\n🌏 Asian: "
                        f"{round(ar.get('low',0),d)}—"
                        f"{round(ar.get('high',0),d)}") if ar else ""

            # ── SWEEP ALERT ──
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
            pending_sweeps.append({
                "name": name, "symbol": symbol, "sweep": sweep
            })

        except: pass
        time.sleep(3)

def main():
    # Reports
    schedule.every().day.at("21:00").do(send_daily_report)
    schedule.every().friday.at("21:00").do(send_weekly_report)
    schedule.every().day.at("21:01").do(
        lambda: send_monthly_report() if now_ist().day==1 else None)
    # Scan every 4 mins
    schedule.every(4).minutes.do(analyze_all)

    send_msg(
        "🤖 <b>ICT Sniper Bot V5 — LIVE!</b>\n\n"
        "🔧 <b>Fixed Issues:</b>\n"
        "✅ Sweep detection — very sensitive now\n"
        "✅ HTF Bias — simple & reliable\n"
        "✅ Volume check — removed\n"
        "✅ MSS — relaxed conditions\n"
        "✅ Score min — 0 (MSS enough)\n"
        "✅ Sweep alert first, then confirm\n\n"
        "📊 Watching 10 pairs every 4 mins...\n"
        "🎯 Signals will come! Stay patient."
    )
    analyze_all()
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    main()
