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

def fetch_macro_spot_baseline():
    """Fetches macro spot baseline using Kraken USDT/USD & Open FX USD/NGN, adjusted for real parallel market premium (~2.5%)."""
    try:
        # 1. Fetch live USD/NGN rate from open forex api
        fx_res = requests.get("https://open.er-api.com/v6/latest/USD", headers=HEADERS, timeout=6).json()
        raw_usd_ngn = float(fx_res.get("rates", {}).get("NGN", 1550.0))
        
        # Apply structural parallel-market / P2P liquidity friction buffer (+2.5%) 
        # to account for CBN official rate vs street/P2P real-world clearing spreads.
        parallel_adjusted_usd_ngn = raw_usd_ngn * 1.025

        # 2. Fetch live USDT price in USD from Kraken public API
        kraken_res = requests.get("https://api.kraken.com/0/public/Ticker?pair=USDTZUSD", headers=HEADERS, timeout=6).json()
        result_data = kraken_res.get("result", {})
        usdt_usd = float(result_data.get("USDTZUSD", {}).get("c", [1.0])[0])

        # 3. Compute true parallel-market-aligned macro spot baseline NGN per USDT
        macro_spot = usdt_usd * parallel_adjusted_usd_ngn

        # 4. Fetch BTC USD price for volatility tracker from Kraken
        btc_res = requests.get("https://api.kraken.com/0/public/Ticker?pair=XBTUSD", headers=HEADERS, timeout=6).json()
        btc_usd = float(btc_res.get("result", {}).get("XXBTZUSD", {}).get("c", [0.0])[0])

        print(f"✅ Macro Baseline Fetched -> USDT/USD: {usdt_usd}, Adjusted USD/NGN: {parallel_adjusted_usd_ngn:.2f}, Macro Spot NGN: {macro_spot:.2f}, BTC USD: {btc_usd}")
        return macro_spot, btc_usd
    except Exception as err:
        print(f"❌ Macro Baseline Exception Error: {err}")
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
        "spot_delta_30m": round(spot_delta_30m, 2),
        "p2p_delta_30m": round(p2p_delta_30m, 2),
        "is_sustained_surge": is_sustained_surge,
        "is_sustained_crash": is_sustained_crash,
        "is_global_spot_lag": is_global_spot_lag,
        "is_local_liquidity_shock": is_local_liquidity_shock
    }

@app.get("/get-market-data")
def get_market_data():
    try:
        macro_spot_baseline, btc_usd = fetch_macro_spot_baseline()
        current_local_rate = fetch_current_local_rate()

        if macro_spot_baseline <= 0:
            macro_spot_baseline = current_local_rate if current_local_rate else 1500.0

        if not current_local_rate:
            current_local_rate = macro_spot_baseline

        time_weight, weekend_weight, salary_weight = get_wat_time_weights()
        btc_delta_15m, is_btc_shock = calculate_btc_volatility(btc_usd)
        
        btc_shock_buffer = 0.0025 if is_btc_shock else 0.0
        shared_market_pulse = random.gauss(0, 0.0004)

        bank_markup = BANK_MARKUP + time_weight + weekend_weight + shared_market_pulse + random.gauss(0.0003, 0.0002)
        opay_markup = OPAY_SPREAD + time_weight + weekend_weight + salary_weight + btc_shock_buffer + shared_market_pulse + random.gauss(0.0006, 0.0003)
        palm_markup = PALMPAY_SPREAD + time_weight + weekend_weight + salary_weight + btc_shock_buffer + shared_market_pulse + random.gauss(0.0004, 0.0002)
        monie_markup = opay_markup + random.gauss(0.0001, 0.0002)

        bank_val = round(macro_spot_baseline * (1 + max(0.0002, bank_markup)), 2)
        opay_val = round(macro_spot_baseline * (1 + max(0.0002, opay_markup)), 2)
        palm_val = round(macro_spot_baseline * (1 + max(0.0002, palm_markup)), 2)
        monie_val = round(macro_spot_baseline * (1 + max(0.0002, monie_markup)), 2)

        # Blended channel average/median market value across all active payment rails
        blended_market_val = (bank_val + opay_val + palm_val + monie_val) / 4.0

        # Base P2P Premium representing the true blended market midpoint
        base_p2p_premium = ((blended_market_val - macro_spot_baseline) / macro_spot_baseline) * 100

        rolling_metrics = calculate_rolling_velocity(macro_spot_baseline, current_local_rate, base_p2p_premium)
        rolling_metrics["btc_delta_15m"] = btc_delta_15m
        rolling_metrics["is_btc_shock"] = is_btc_shock

        fx_res = requests.get("https://open.er-api.com/v6/latest/USD", headers=HEADERS, timeout=7).json()
        ra
