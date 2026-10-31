import os
import json
import random
import requests
from collections import deque
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, HTTPException

app = FastAPI()

# --- WEST AFRICA TIMEZONE (WAT = UTC + 1) ---
WAT = timezone(timedelta(hours=1))

# Default base spread configurations
BANK_MARKUP = float(os.getenv("BANK_MARKUP", "0.0010"))
PALMPAY_SPREAD = float(os.getenv("PALMPAY_SPREAD", "0.0012"))
OPAY_SPREAD = float(os.getenv("OPAY_SPREAD", "0.0024"))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json"
}

PRICE_HISTORY = deque(maxlen=60)
BTC_PRICE_HISTORY = deque(maxlen=15)

def fetch_coingecko_market_data():
    """Fetches macro spot baseline (USDT/NGN) and Bitcoin (BTC/USD) from CoinGecko with error tracing."""
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": "tether,bitcoin", "vs_currencies": "ngn,usd"}
    try:
        res = requests.get(url, params=params, headers=HEADERS, timeout=6)
        print(f"🔍 CoinGecko Status Code: {res.status_code}")
        if res.status_code == 200:
            data = res.json()
            tether_ngn = float(data.get("tether", {}).get("ngn", 0))
            btc_usd = float(data.get("bitcoin", {}).get("usd", 0))
            print(f"✅ CoinGecko Fetched -> Tether NGN: {tether_ngn}, BTC USD: {btc_usd}")
            return tether_ngn, btc_usd
        else:
            print(f"⚠️ CoinGecko Non-200 Response: {res.text}")
    except Exception as err:
        print(f"❌ CoinGecko Exception Error: {err}")
    return 0.0, 0.0

def fetch_current_local_rate():
    """Fetches current local exchange rate ticker from Quidax."""
    url = "https://openapi.quidax.io/exchange-open-api/api/v1/markets/tickers/usdtngn"
    try:
        res = requests.get(url, headers=HEADERS, timeout=6)
        raw_content = res.json()
        quidax_json = json.loads(raw_content) if isinstance(raw_content, str) else raw_content
        market_data = quidax_json.get("data", {})
        usdtngn_data = market_data.get("usdtngn", {})
        ticker_data = usdtngn_data.get("ticker", {})
        price = float(ticker_data.get("last", ticker_data.get("sell", 0)))
        if price > 0:
            return price
    except Exception:
        pass
    return None

def get_wat_time_weights():
    now_wat = datetime.now(WAT)
    hour = now_wat.hour
    weekday = now_wat.weekday()
    time_weight = 0.0015 if (hour < 6 or hour >= 22) else 0.0008
    weekend_weight = 0.0010 if weekday >= 5 else 0.0
    
    # Automatic Salary Window Booster (27th to 5th adds +0.17% demand buffer)
    day = now_wat.day
    salary_weight = 0.0017 if (day >= 27 or day <= 5) else 0.0
    
    return time_weight, weekend_weight, salary_weight

def calculate_btc_volatility(current_btc_price):
    BTC_PRICE_HISTORY.append(current_btc_price)
    btc_delta_15m = 0.0
    if len(BTC_PRICE_HISTORY) > 1:
        oldest_btc = BTC_PRICE_HISTORY[0]
        if oldest_btc > 0:
            btc_delta_15m = ((current_btc_price - oldest_btc) / oldest_btc) * 100
    return round(btc_delta_15m, 2), abs(btc_delta_15m) >= 1.5

def calculate_rolling_velocity(current_macro_spot, current_local_rate, current_premium):
    now = datetime.now(WAT)
    PRICE_HISTORY.append((now, current_macro_spot, current_local_rate, current_premium))
    
    velocity_10m = 0.0
    velocity_60m = 0.0
    spot_delta_30m = 0.0
    p2p_delta_30m = 0.0

    # WARM-UP GUARD: Suppress artificial cold-start spikes until buffer is mature (>= 15 mins)
    if len(PRICE_HISTORY) >= 15:
        oldest_time, oldest_macro, oldest_local, oldest_premium = PRICE_HISTORY[0]
        time_span_min = (now - oldest_time).total_seconds() / 60.0
        if time_span_min >= 12.0:
            spot_delta_30m = ((current_macro_spot - oldest_macro) / oldest_macro) * 100
            p2p_delta_30m = ((current_premium - oldest_premium) / (oldest_premium if oldest_premium != 0 else 1)) * 100

    for past_time, past_macro, past_local, past_premium in PRICE_HISTORY:
        time_diff_min = (now - past_time).total_seconds() / 60.0
        if 9.0 <= time_diff_min <= 11.0:
            velocity_10m = current_premium - past_premium
        if 55.0 <= time_diff_min <= 65.0:
            velocity_60m = current_premium - past_premium

    is_sustained_surge = False
    is_sustained_crash = False
    is_global_spot_lag = False
    is_local_liquidity_shock = False

    if len(PRICE_HISTORY) >= 15:
        is_sustained_surge = (velocity_10m >= 0.7) or (velocity_60m >= 0.7) or (p2p_delta_30m >= 0.35)
        is_sustained_crash = (velocity_10m <= -0.7) or (velocity_60m <= -0.7) or (p2p_delta_30m <= -0.35)
        is_global_spot_lag = (abs(spot_delta_30m) >= 0.4) and (abs(p2p_delta_30m) < 0.2)
        is_local_liquidity_shock = (abs(spot_delta_30m) < 0.2) and (abs(p2p_delta_30m) >= 0.4)

    return {
        "velocity_10m": round(velocity_10m, 2),
        "velocity_60m": round(velocity_60m, 2),
        "spot_delta_30m": rou
