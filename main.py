import ccxt
import pandas as pd
import numpy as np
import requests
import time
import json
import os
from datetime import datetime, timezone

# ══════════════════════════════════════════════
#  CONFIG — IRR RIBBON MULTI-ACTIFS
# ══════════════════════════════════════════════
DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1479102147927871580/qlie8sZWBy2UeD85EEGZH7zz7twaPGrMbMm4fj5iIXzRu8O4MHDx-EBqcWS1fN8TJMYx"

EMA8_LEN      = 8
EMA20_LEN     = 20
EMA21_LEN     = 21
EMA50_LEN     = 50
IMP_MULT      = 1.2
IMP_BODY      = 0.55
RET_BARS      = 8
REJ_WICK      = 0.45
REJ_BODY      = 0.35
ATR_MULT_SL   = 1.2
ATR_MULT_TP   = 2.5
RSI_LEN       = 14
RSI_OB        = 72
RSI_OS        = 28
RIBBON_SPREAD = 0.0005

SESSIONS = [
    {"nom": "LONDON",   "debut": 7,  "fin": 12},
    {"nom": "NEW YORK", "debut": 13, "fin": 17},
]

# Actifs avec sources Kraken
ACTIFS = {
    "XAU/USD": ("kraken", "XAUT/USD"),
    "EUR/USD": ("kraken", "EUR/USD"),
    "US500": ("yfinance", "^GSPC"),
}

PERF_FILE    = "performances_irr.json"
last_signals = {}

# ══════════════════════════════════════════════
#  DONNÉES
# ══════════════════════════════════════════════
def get_ohlcv(nom_actif, ex_name, symbol):
    try:
        ex   = getattr(ccxt, ex_name)()
        bars = ex.fetch_ohlcv(symbol, "5m", limit=100)
        if not bars or len(bars) < 60:
            return None
        df = pd.DataFrame(bars, columns=["time","Open","High","Low","Close","Volume"])
        df["time"] = pd.to_datetime(df["time"], unit="ms")
        df = df.dropna().reset_index(drop=True)
        print(f"✅ {nom_actif} : {len(df)} bougies — ${df['Close'].iloc[-1]:.4f}")
        return df
    except Exception as e:
        print(f"⚠️ {nom_actif} {ex_name} : {e}")
        # Fallback yfinance
        try:
            import yfinance as yf
            fallback = {"XAU/USD": "GC=F", "EUR/USD": "EURUSD=X", "US500": "^GSPC"}
            ticker = fallback.get(nom_actif)
            if not ticker: return None
            df = yf.download(ticker, period="5d", interval="5m",
                           progress=False, auto_adjust=True)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.dropna().reset_index(drop=True)
            if len(df) < 60: return None
            print(f"✅ {nom_actif} yfinance fallback : ${float(df['Close'].iloc[-1]):.4f}")
            return df
        except:
            return None

# ══════════════════════════════════════════════
#  SESSIONS
# ══════════════════════════════════════════════
def in_session():
    now = datetime.now(timezone.utc)
    nm  = now.hour * 60 + now.minute
    for s in SESSIONS:
        if s["debut"] * 60 <= nm < s["fin"] * 60:
            return True, s["nom"]
    return False, None

# ══════════════════════════════════════════════
#  INDICATEURS
# ══════════════════════════════════════════════
def calc_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calc_rsi(series, period):
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss
    return 100 - (100 / (1 + rs))

def calc_atr(high, low, close, period):
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

# ══════════════════════════════════════════════
#  LOGIQUE IRR RIBBON
# ══════════════════════════════════════════════
def analyser_irr(df, nom_actif):
    if df is None or len(df) < 60:
        return None

    close = df["Close"]
    high  = df["High"]
    low   = df["Low"]
    open_ = df["Open"]

    ema8  = calc_ema(close, EMA8_LEN)
    ema20 = calc_ema(close, EMA20_LEN)
    ema21 = calc_ema(close, EMA21_LEN)
    ema50 = calc_ema(close, EMA50_LEN)
    atr   = calc_atr(high, low, close, 14)
    rsi   = calc_rsi(close, RSI_LEN)

    signals = []
    for i in range(-RET_BARS - 5, -1):
        try:
            e8  = float(ema8.iloc[i])
            e20 = float(ema20.iloc[i])
            e21 = float(ema21.iloc[i])
            e50 = float(ema50.iloc[i])
            at  = float(atr.iloc[i])
            cl  = float(close.iloc[i])
            hi  = float(high.iloc[i])
            lo  = float(low.iloc[i])
            op  = float(open_.iloc[i])
            rs  = float(rsi.iloc[i])
            if at == 0 or np.isnan(at): continue

            body       = abs(cl - op)
            range_bar  = hi - lo
            upper_wick = hi - max(cl, op)
            lower_wick = min(cl, op) - lo
            body_ratio = body / range_bar if range_bar > 0 else 0

            bull_trend  = e8 > e20 and e20 > e21 and e21 > e50
            bear_trend  = e8 < e20 and e20 < e21 and e21 < e50
            ribbon_bull = (e8 - e50) / e50 >= RIBBON_SPREAD if e50 > 0 else False
            ribbon_bear = (e50 - e8) / e50 >= RIBBON_SPREAD if e50 > 0 else False

            imp_bull = (cl > op and body >= IMP_MULT * at and
                       body_ratio >= IMP_BODY and cl > e21 and
                       bull_trend and ribbon_bull)
            imp_bear = (cl < op and body >= IMP_MULT * at and
                       body_ratio >= IMP_BODY and cl < e21 and
                       bear_trend and ribbon_bear)

            signals.append({
                "idx": i, "imp_bull": imp_bull, "imp_bear": imp_bear,
                "cl": cl, "hi": hi, "lo": lo, "op": op,
                "e8": e8, "e21": e21, "e50": e50, "at": at, "rs": rs,
                "body_ratio": body_ratio, "upper_wick": upper_wick,
                "lower_wick": lower_wick, "range_bar": range_bar,
                "bull_trend": bull_trend, "bear_trend": bear_trend,
                "ribbon_bull": ribbon_bull, "ribbon_bear": ribbon_bear
            })
        except: continue

    if not signals: return None

    last_imp_bull_idx = None
    last_imp_bear_idx = None
    for j, s in enumerate(signals):
        if s["imp_bull"]: last_imp_bull_idx = j
        if s["imp_bear"]: last_imp_bear_idx = j

    last = signals[-1]
    cl, hi, lo = last["cl"], last["hi"], last["lo"]
    at, rs, e21 = last["at"], last["rs"], last["e21"]
    sess, sess_nom = in_session()

    if last_imp_bull_idx is not None:
        bars_since = len(signals) - 1 - last_imp_bull_idx
        if 0 < bars_since <= RET_BARS:
            touch  = (lo <= e21 * 1.002 and lo >= e21 * 0.998 and cl > e21)
            reject = ((last["lower_wick"] / max(last["range_bar"], 0.0001)) >= REJ_WICK and
                      last["body_ratio"] <= REJ_BODY and cl > last["op"])
            if touch and reject and rs < RSI_OB and sess and last["bull_trend"] and last["ribbon_bull"]:
                sl = lo - at * ATR_MULT_SL
                tp = cl + at * ATR_MULT_TP
                rr = (tp - cl) / (cl - sl) if (cl - sl) > 0 else 0
                return {"signal": "BUY", "actif": nom_actif, "prix": cl,
                        "sl": sl, "tp": tp, "rr": rr, "e21": e21,
                        "e50": last["e50"], "atr": at, "rsi": rs,
                        "session": sess_nom, "bars_since_imp": bars_since}

    if last_imp_bear_idx is not None:
        bars_since = len(signals) - 1 - last_imp_bear_idx
        if 0 < bars_since <= RET_BARS:
            touch  = (hi >= e21 * 0.998 and hi <= e21 * 1.002 and cl < e21)
            reject = ((last["upper_wick"] / max(last["range_bar"], 0.0001)) >= REJ_WICK and
                      last["body_ratio"] <= REJ_BODY and cl < last["op"])
            if touch and reject and rs > RSI_OS and sess and last["bear_trend"] and last["ribbon_bear"]:
                sl = hi + at * ATR_MULT_SL
                tp = cl - at * ATR_MULT_TP
                rr = (cl - tp) / (sl - cl) if (sl - cl) > 0 else 0
                return {"signal": "SELL", "actif": nom_actif, "prix": cl,
                        "sl": sl, "tp": tp, "rr": rr, "e21": e21,
                        "e50": last["e50"], "atr": at, "rsi": rs,
                        "session": sess_nom, "bars_since_imp": bars_since}
    return None

# ══════════════════════════════════════════════
#  DISCORD
# ══════════════════════════════════════════════
def send_discord(data):
    ic, color = ("🟢", 3066993) if data["signal"] == "BUY" else ("🔴", 15158332)
    now = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    payload = {
        "username": "⚡ IRR RIBBON BOT",
        "embeds": [{"title": f"{ic} SIGNAL {data['signal']} — {data['actif']} M5",
            "color": color,
            "fields": [
                {"name": "💰 Entrée",      "value": f"${data['prix']:.4f}", "inline": True},
                {"name": "🛑 Stop Loss",   "value": f"${data['sl']:.4f}",   "inline": True},
                {"name": "🎯 Take Profit", "value": f"${data['tp']:.4f}",   "inline": True},
                {"name": "⚖️ R/R",         "value": f"1:{data['rr']:.1f}",  "inline": True},
                {"name": "📊 RSI",         "value": f"{data['rsi']:.1f}",   "inline": True},
                {"name": "📉 ATR",         "value": f"{data['atr']:.4f}",   "inline": True},
                {"name": "📈 EMA 21",      "value": f"{data['e21']:.4f}",   "inline": True},
                {"name": "📈 EMA 50",      "value": f"{data['e50']:.4f}",   "inline": True},
                {"name": "🕐 Session",     "value": data["session"],         "inline": True},
                {"name": "⏱️ Bougies/IMP","value": str(data["bars_since_imp"]), "inline": True},
            ],
            "footer": {"text": f"IRR RIBBON · Kraken · {now}"},
            "timestamp": datetime.now(timezone.utc).isoformat()}]}
    try:
        r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        print("✅ Discord envoyé" if r.status_code == 204 else f"⚠️ {r.status_code}")
    except Exception as e:
        print(f"❌ Discord : {e}")

def send_heartbeat(stats):
    now = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    sess, nom = in_session()
    fields = [
        {"name": "Statut", "value": f"🟢 {nom}" if sess else "⏳ Hors session", "inline": True},
        {"name": "Heure",  "value": now, "inline": True},
        {"name": "Source", "value": "✅ Kraken temps réel", "inline": True},
    ]
    for a, s in stats.items():
        fields.append({"name": f"📊 {a}",
                      "value": f"BUY:{s['buy']} SELL:{s['sell']}", "inline": True})
    payload = {"username": "⚡ IRR RIBBON BOT", "embeds": [{"title": "💓 IRR RIBBON — Serveur 24/7",
        "color": 16776960, "fields": fields,
        "footer": {"text": "IRR RIBBON — Railway 24/7"}}]}
    try:
        requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        print(f"💓 Heartbeat — {now}")
    except: pass

def load_perf():
    if os.path.exists(PERF_FILE):
        with open(PERF_FILE) as f: return json.load(f)
    return {a: {"buy": 0, "sell": 0} for a in ACTIFS}

def save_perf(perf):
    with open(PERF_FILE, "w") as f: json.dump(perf, f, indent=2)

# ══════════════════════════════════════════════
#  BOUCLE PRINCIPALE
# ══════════════════════════════════════════════
def main():
    print("⚡ IRR RIBBON BOT — Kraken temps réel")
    print("=" * 50)
    perf = load_perf()
    send_heartbeat(perf)
    heartbeat_counter = 0

    while True:
        print(f"\n{'='*50}\n⏰ {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
        sess, sess_nom = in_session()
        print(f"🕐 {'✅ Session '+sess_nom if sess else '❌ Hors session'}")

        for nom_actif, (ex_name, symbol) in ACTIFS.items():
            print(f"\n📊 {nom_actif}...")
            df     = get_ohlcv(nom_actif, ex_name, symbol)
            result = analyser_irr(df, nom_actif)
            if result:
                last = last_signals.get(nom_actif)
                if last and last["signal"] == result["signal"] and \
                   abs(last["prix"] - result["prix"]) < 1.0:
                    print(f"⏭️ Doublon ignoré"); continue
                send_discord(result)
                last_signals[nom_actif] = result
                perf[nom_actif][result["signal"].lower()] += 1
                save_perf(perf)
            else:
                print(f"⏳ Pas de signal IRR")

        heartbeat_counter += 1
        if heartbeat_counter >= 12:
            perf = load_perf()
            send_heartbeat(perf)
            heartbeat_counter = 0

        now = datetime.now(timezone.utc)
        secs_to_next = 300 - (now.minute % 5) * 60 - now.second + 5
        print(f"\n⏰ Prochaine analyse dans {secs_to_next}s")
        time.sleep(secs_to_next)

if __name__ == "__main__":
    main()
