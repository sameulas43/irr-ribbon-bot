import requests
import pandas as pd
import numpy as np
import time
import json
import os
from datetime import datetime, timezone

# ══════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════
DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1479102147927871580/qlie8sZWBy2UeD85EEGZH7zz7twaPGrMbMm4fj5iIXzRu8O4MHDx-EBqcWS1fN8TJMYx"
ALPHA_KEY       = "EO7D68RIHHT4ZKTF"

# ── Paramètres IRR RIBBON ──
EMA8_LEN     = 8
EMA20_LEN    = 20
EMA21_LEN    = 21
EMA50_LEN    = 50
IMP_MULT     = 1.5
IMP_BODY     = 0.6
RET_BARS     = 6
REJ_WICK     = 0.55
REJ_BODY     = 0.30
ATR_MULT_SL  = 1.2
ATR_MULT_TP  = 2.5
RSI_LEN      = 14
RSI_OB       = 70
RSI_OS       = 30
RIBBON_SPREAD= 0.001

SESSIONS = [
    {"nom": "LONDON",   "debut": 7,  "fin": 12, "dM": 0, "fM": 0},
    {"nom": "NEW YORK", "debut": 13, "fin": 17, "dM": 0, "fM": 0},
]

# ── Actifs Alpha Vantage ──
ACTIFS = {
    "XAU/USD": {"func": "FX_INTRADAY", "from": "XAU", "to": "USD"},
    "EUR/USD": {"func": "FX_INTRADAY", "from": "EUR", "to": "USD"},
}

PERF_FILE = "performances_irr.json"
last_signals = {}

# ══════════════════════════════════════════════
#  ALPHA VANTAGE
# ══════════════════════════════════════════════
def get_fx_m5(from_sym, to_sym):
    url = (f"https://www.alphavantage.co/query"
           f"?function=FX_INTRADAY"
           f"&from_symbol={from_sym}&to_symbol={to_sym}"
           f"&interval=5min&outputsize=compact"
           f"&apikey={ALPHA_KEY}")
    try:
        r    = requests.get(url, timeout=15)
        data = r.json()
        key  = "Time Series FX (5min)"
        if key not in data:
            print(f"⚠️ {from_sym}/{to_sym} : {data.get('Note', data.get('Information', 'Erreur'))}")
            return None
        rows = []
        for ts, v in data[key].items():
            rows.append({"time": pd.Timestamp(ts),
                         "Open":  float(v["1. open"]),
                         "High":  float(v["2. high"]),
                         "Low":   float(v["3. low"]),
                         "Close": float(v["4. close"])})
        df = pd.DataFrame(rows).sort_values("time").reset_index(drop=True)
        print(f"✅ {from_sym}/{to_sym} : {len(df)} bougies — ${float(df['Close'].iloc[-1]):.4f}")
        return df
    except Exception as e:
        print(f"❌ {from_sym}/{to_sym} : {e}")
        return None

def get_us500_m5():
    url = (f"https://www.alphavantage.co/query"
           f"?function=TIME_SERIES_INTRADAY"
           f"&symbol=SPY&interval=5min"
           f"&outputsize=compact"
           f"&apikey={ALPHA_KEY}")
    try:
        r    = requests.get(url, timeout=15)
        data = r.json()
        key  = "Time Series (5min)"
        if key not in data:
            print(f"⚠️ US500 : {data.get('Note', data.get('Information', 'Erreur'))}")
            return None
        rows = []
        for ts, v in data[key].items():
            rows.append({"time": pd.Timestamp(ts),
                         "Open":  float(v["1. open"]),
                         "High":  float(v["2. high"]),
                         "Low":   float(v["3. low"]),
                         "Close": float(v["4. close"])})
        df = pd.DataFrame(rows).sort_values("time").reset_index(drop=True)
        print(f"✅ US500 : {len(df)} bougies — ${float(df['Close'].iloc[-1]):.2f}")
        return df
    except Exception as e:
        print(f"❌ US500 : {e}")
        return None

# ══════════════════════════════════════════════
#  SESSIONS
# ══════════════════════════════════════════════
def in_session():
    now = datetime.now(timezone.utc)
    nm  = now.hour * 60 + now.minute
    for s in SESSIONS:
        if s["debut"]*60+s["dM"] <= nm < s["fin"]*60+s["fM"]:
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
    tr = pd.concat([high-low,(high-close.shift()).abs(),(low-close.shift()).abs()],axis=1).max(axis=1)
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

            body        = abs(cl - op)
            range_bar   = hi - lo
            upper_wick  = hi - max(cl, op)
            lower_wick  = min(cl, op) - lo
            body_ratio  = body / range_bar if range_bar > 0 else 0

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
        except Exception:
            continue

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

    # BUY
    if last_imp_bull_idx is not None:
        bars_since = len(signals) - 1 - last_imp_bull_idx
        if 0 < bars_since <= RET_BARS:
            touch  = (lo <= e21*1.0015 and lo >= e21*0.9985 and cl > e21)
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

    # SELL
    if last_imp_bear_idx is not None:
        bars_since = len(signals) - 1 - last_imp_bear_idx
        if 0 < bars_since <= RET_BARS:
            touch  = (hi >= e21*0.9985 and hi <= e21*1.0015 and cl < e21)
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
                {"name": "💰 Entrée",        "value": f"${data['prix']:.4f}",  "inline": True},
                {"name": "🛑 Stop Loss",      "value": f"${data['sl']:.4f}",    "inline": True},
                {"name": "🎯 Take Profit",    "value": f"${data['tp']:.4f}",    "inline": True},
                {"name": "⚖️ R/R",            "value": f"1:{data['rr']:.1f}",   "inline": True},
                {"name": "📊 RSI",            "value": f"{data['rsi']:.1f}",    "inline": True},
                {"name": "📉 ATR",            "value": f"{data['atr']:.4f}",    "inline": True},
                {"name": "📈 EMA 21",         "value": f"{data['e21']:.4f}",    "inline": True},
                {"name": "📈 EMA 50",         "value": f"{data['e50']:.4f}",    "inline": True},
                {"name": "🕐 Session",        "value": data["session"],          "inline": True},
                {"name": "⏱️ Bougies / IMP",  "value": str(data["bars_since_imp"]), "inline": True},
            ],
            "footer": {"text": f"IRR RIBBON · Alpha Vantage · {now}"},
            "timestamp": datetime.now(timezone.utc).isoformat()}]}
    try:
        r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        print("✅ Discord envoyé" if r.status_code == 204 else f"⚠️ Discord {r.status_code}")
    except Exception as e:
        print(f"❌ Discord : {e}")

def send_heartbeat(stats):
    now  = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    sess, nom = in_session()
    fields = [
        {"name": "Statut",  "value": f"🟢 Session {nom}" if sess else "⏳ Hors session", "inline": True},
        {"name": "Heure",   "value": now, "inline": True},
        {"name": "Source",  "value": "✅ Alpha Vantage", "inline": True},
    ]
    for actif, s in stats.items():
        fields.append({"name": f"📊 {actif}", "value": f"BUY: {s['buy']} | SELL: {s['sell']}", "inline": True})
    payload = {"username": "⚡ IRR RIBBON BOT", "embeds": [{"title": "💓 IRR RIBBON — Serveur actif 24/7",
        "color": 16776960, "fields": fields,
        "footer": {"text": "IRR RIBBON LDN+NY — Railway Server"}}]}
    try:
        requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        print(f"💓 Heartbeat — {now}")
    except: pass

# ══════════════════════════════════════════════
#  PERFORMANCES
# ══════════════════════════════════════════════
def load_perf():
    if os.path.exists(PERF_FILE):
        with open(PERF_FILE) as f: return json.load(f)
    return {a: {"buy": 0, "sell": 0, "signals": []} for a in ["XAU/USD", "EUR/USD", "US500"]}

def save_perf(perf):
    with open(PERF_FILE, "w") as f: json.dump(perf, f, indent=2)

# ══════════════════════════════════════════════
#  BOUCLE PRINCIPALE
# ══════════════════════════════════════════════
def main():
    print("⚡ IRR RIBBON BOT — Alpha Vantage (prix réels)")
    print("=" * 50)

    perf = load_perf()
    send_heartbeat(perf)
    heartbeat_counter = 0

    while True:
        print(f"\n{'='*50}\n⏰ {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
        sess, sess_nom = in_session()
        print(f"🕐 {'✅ Session '+sess_nom if sess else '❌ Hors session'}")

        # XAU/USD
        print("\n📊 XAU/USD...")
        df_xau = get_fx_m5("XAU", "USD")
        time.sleep(15)  # Respecter la limite API (25 req/jour)

        # EUR/USD
        print("\n📊 EUR/USD...")
        df_eur = get_fx_m5("EUR", "USD")
        time.sleep(15)

        # US500 (SPY)
        print("\n📊 US500...")
        df_us5 = get_us500_m5()
        time.sleep(15)

        donnees = {"XAU/USD": df_xau, "EUR/USD": df_eur, "US500": df_us5}

        for nom_actif, df in donnees.items():
            result = analyser_irr(df, nom_actif)
            if result:
                last = last_signals.get(nom_actif)
                if last and last["signal"] == result["signal"] and abs(last["prix"] - result["prix"]) < 0.5:
                    print(f"⏭️ Doublon {nom_actif} ignoré"); continue
                send_discord(result)
                last_signals[nom_actif] = result
                perf[nom_actif][result["signal"].lower()] += 1
                perf[nom_actif]["signals"].append({
                    "time": datetime.now(timezone.utc).isoformat(),
                    "signal": result["signal"], "prix": result["prix"],
                    "sl": result["sl"], "tp": result["tp"], "rr": result["rr"]
                })
                if len(perf[nom_actif]["signals"]) > 100:
                    perf[nom_actif]["signals"] = perf[nom_actif]["signals"][-100:]
                save_perf(perf)
            else:
                print(f"⏳ {nom_actif} — pas de signal IRR")

        heartbeat_counter += 1
        if heartbeat_counter >= 12:
            perf = load_perf()
            send_heartbeat(perf)
            heartbeat_counter = 0

        now = datetime.now(timezone.utc)
        secs_to_next = 300 - (now.minute % 5) * 60 - now.second + 30
        print(f"\n⏰ Prochaine analyse dans {secs_to_next}s")
        time.sleep(secs_to_next)

if __name__ == "__main__":
    main()
