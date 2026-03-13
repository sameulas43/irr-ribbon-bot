import ccxt
import yfinance as yf
import pandas as pd
import requests
import time
from datetime import datetime, timezone

# ══════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════
DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1479102147927871580/qlie8sZWBy2UeD85EEGZH7zz7twaPGrMbMm4fj5iIXzRu8O4MHDx-EBqcWS1fN8TJMYx"

EMA_FAST = 20
EMA_SLOW = 50
SL_PCTS  = {"XAUUSD": 0.03, "EURUSD": 0.001, "US500": 0.003}  # SL % du prix
TP_MULT  = 2.0  # TP = SL x 2

# Derniers signaux par actif
last_signals = {
    "XAUUSD": {"type": None, "bar": -999},
    "EURUSD": {"type": None, "bar": -999},
    "US500":  {"type": None, "bar": -999},
}

# ══════════════════════════════════════════════
#  DONNÉES
# ══════════════════════════════════════════════
def get_xauusd():
    try:
        ex   = ccxt.kraken()
        bars = ex.fetch_ohlcv("XAUT/USD", "5m", limit=100)
        df   = pd.DataFrame(bars, columns=["time","Open","High","Low","Close","Volume"])
        df["time"] = pd.to_datetime(df["time"], unit="ms")
        df = df.dropna().reset_index(drop=True)
        print(f"✅ XAU/USD : ${df['Close'].iloc[-1]:.2f}")
        return df
    except Exception as e:
        print(f"❌ XAU/USD : {e}")
        return None

def get_eurusd():
    try:
        ex   = ccxt.kraken()
        bars = ex.fetch_ohlcv("EUR/USD", "5m", limit=100)
        df   = pd.DataFrame(bars, columns=["time","Open","High","Low","Close","Volume"])
        df["time"] = pd.to_datetime(df["time"], unit="ms")
        df = df.dropna().reset_index(drop=True)
        print(f"✅ EUR/USD : ${df['Close'].iloc[-1]:.5f}")
        return df
    except Exception as e:
        print(f"❌ EUR/USD : {e}")
        return None

def get_us500():
    try:
        df = yf.download("^GSPC", period="2d", interval="5m", progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna().reset_index()
        df = df.rename(columns={"Datetime": "time"})
        print(f"✅ US500 : ${float(df['Close'].iloc[-1]):.2f}")
        return df
    except Exception as e:
        print(f"❌ US500 : {e}")
        return None

# ══════════════════════════════════════════════
#  INDICATEURS
# ══════════════════════════════════════════════
def calc_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

# ══════════════════════════════════════════════
#  ANALYSE — MÊME LOGIQUE QUE SNIPER XAUUSD
# ══════════════════════════════════════════════
def analyser(df, nom):
    global last_signals

    if df is None or len(df) < 60:
        print(f"⚠️ {nom} : pas assez de données")
        return

    close    = df["Close"]
    ema_fast = calc_ema(close, EMA_FAST)
    ema_slow = calc_ema(close, EMA_SLOW)
    bar_index = len(df)

    cl_curr = float(close.iloc[-2])
    cl_prev = float(close.iloc[-3])
    ef_curr = float(ema_fast.iloc[-2])
    ef_prev = float(ema_fast.iloc[-3])
    es_curr = float(ema_slow.iloc[-2])

    bull       = ef_curr > es_curr
    bear       = ef_curr < es_curr
    crossover  = (cl_prev < ef_prev) and (cl_curr > ef_curr)
    crossunder = (cl_prev > ef_prev) and (cl_curr < ef_curr)

    buy  = bull and crossover
    sell = bear and crossunder

    print(f"📊 {nom} ${cl_curr:.4f} | EMA20:{ef_curr:.4f} | {'▲' if bull else '▼'} | Cross↑:{crossover} Cross↓:{crossunder}")

    if buy or sell:
        signal = "BUY" if buy else "SELL"

        # Anti-doublon
        if last_signals[nom]["type"] == signal and (bar_index - last_signals[nom]["bar"]) < 3:
            print(f"⏭️ {nom} doublon ignoré")
            return

        sl_pct = SL_PCTS[nom]
        sl = cl_curr * (1 - sl_pct) if signal == "BUY" else cl_curr * (1 + sl_pct)
        tp = cl_curr * (1 + sl_pct * TP_MULT) if signal == "BUY" else cl_curr * (1 - sl_pct * TP_MULT)

        last_signals[nom]["type"] = signal
        last_signals[nom]["bar"]  = bar_index

        print(f"🚨 {nom} SIGNAL {signal} @ {cl_curr:.4f}")
        send_discord(signal, nom, cl_curr, sl, tp, ef_curr, es_curr)
    else:
        print(f"⏳ {nom} pas de signal")

# ══════════════════════════════════════════════
#  DISCORD
# ══════════════════════════════════════════════
def send_discord(signal, nom, prix, sl, tp, ema20, ema50):
    ic, color = ("🟢", 3066993) if signal == "BUY" else ("🔴", 15158332)
    now = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    rr  = abs(tp - prix) / abs(sl - prix)

    payload = {
        "username": "⚡ SNIPER MULTI M5",
        "embeds": [{"title": f"{ic} SIGNAL {signal} — {nom} M5",
            "color": color,
            "fields": [
                {"name": "💰 Entrée",      "value": f"{prix:.4f}",  "inline": True},
                {"name": "🛑 Stop Loss",   "value": f"{sl:.4f}",    "inline": True},
                {"name": "🎯 Take Profit", "value": f"{tp:.4f}",    "inline": True},
                {"name": "📊 EMA 20",      "value": f"{ema20:.4f}", "inline": True},
                {"name": "📊 EMA 50",      "value": f"{ema50:.4f}", "inline": True},
                {"name": "⚖️ R/R",         "value": f"1:{rr:.1f}",  "inline": True},
            ],
            "footer": {"text": f"SNIPER MULTI · {now}"},
            "timestamp": datetime.now(timezone.utc).isoformat()}]}
    try:
        r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        print("✅ Discord" if r.status_code == 204 else f"⚠️ {r.status_code}")
    except Exception as e:
        print(f"❌ Discord : {e}")

def send_heartbeat():
    now = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    payload = {
        "username": "⚡ SNIPER MULTI M5",
        "embeds": [{"title": "💓 Serveur actif 24/7",
            "color": 16776960,
            "fields": [
                {"name": "Actifs",  "value": "XAU/USD · EUR/USD · US500", "inline": True},
                {"name": "Heure",   "value": now,                          "inline": True},
                {"name": "Source",  "value": "Kraken + yfinance",          "inline": True},
            ],
            "footer": {"text": "SNIPER MULTI — Railway 24/7"}}]}
    try:
        requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        print(f"💓 Heartbeat — {now}")
    except: pass

# ══════════════════════════════════════════════
#  BOUCLE PRINCIPALE
# ══════════════════════════════════════════════
def main():
    print("⚡ SNIPER MULTI M5 — XAU/USD · EUR/USD · US500")
    print("=" * 50)
    send_heartbeat()
    hb = 0

    while True:
        print(f"\n{'='*50}\n⏰ {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")

        analyser(get_xauusd(), "XAUUSD")
        analyser(get_eurusd(), "EURUSD")
        analyser(get_us500(),  "US500")

        hb += 1
        if hb >= 12:
            send_heartbeat()
            hb = 0

        now  = datetime.now(timezone.utc)
        secs = 300 - (now.minute % 5) * 60 - now.second + 5
        print(f"⏰ Prochaine analyse dans {secs}s")
        time.sleep(secs)

if __name__ == "__main__":
    main()
