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

# In-memory rolling history buffer storing tuples of (timestamp, spot_price, bank_premium)
# Keeps up to 60 minutes of history (assuming 60s polling interval)
PRICE_HISTORY = deque(maxlen=60)

def get_wat_time_weights():
    now_wat = datetime.now(WAT)
    hour = now_wat.hour
    weekday = now_wat.weekday()  # 0=Mon, 5=Sat, 6=Sun
    
    # Night hours (10 PM - 6 AM WAT) increase illiquidity friction
    time_weight = 0.0015 if (hour < 6 or hour >= 22) else 0.0008
    weekend_weight = 0.0010 if weekday >= 5 else 0.0
    
    return time_weight, weekend_weight

def calculate_rolling_velocity(current_spot, current_premium):
    now = datetime.now(WAT)
    PRICE_HISTORY.append((now, current_spot, current_premium))
    
    velocity_10m = 0.0
    velocity_60m = 0.0
    
    for past_time, past_spot, past_premium in PRICE_HISTORY:
        time_diff_min = (now - past_time).total_seconds() / 60.0
        
        # Snapshot closest to 10 minutes ago
        if 9.0 <= time_diff_min <= 11.0:
            velocity_10m = current_premium - past_premium
            
        # Snapshot closest to 60 minutes ago
        if 55.0 <= time_diff_min <= 65.0:
            velocity_60m = current_premium - past_premium

    # Macro shift trigger if either 10m or 60m window experiences >= 0.7% spread shift
    is_sustained_surge = (velocity_10m >= 0.7) or (velocity_60m >= 0.7)
    is_sustained_crash = (velocity_10m <= -0.7) or (velocity_60m <= -0.7)

    return {
        "velocity_10m": round(velocity_10m, 2),
        "velocity_60m": round(velocity_60m, 2),
        "is_sustained_surge": is_sustained_surge,
        "is_sustained_crash": is_sustained_crash
    }

@app.get("/get-market-data")
def get_market_data():
    try:
        # 1. Ingest spot USDT/NGN rate from Quidax API
        quidax_url = "https://openapi.quidax.io/exchange-open-api/api/v1/markets/tickers/usdtngn"
        quidax_res = requests.get(quidax_url, headers=HEADERS, timeout=7)
        raw_content = quidax_res.json()
        quidax_json = json.loads(raw_content) if isinstance(raw_content, str) else raw_content
        
        market_data = quidax_json.get("data", {})
        usdtngn_data = market_data.get("usdtngn", {})
        ticker_data = usdtngn_data.get("ticker", {})
        
        spot_price = float(ticker_data.get("last", ticker_data.get("sell", 0)))
        if spot_price <= 0:
            raise ValueError(f"Invalid spot rate: {quidax_json}")

        # 2. Calculate WAT Cyclical Weights & Shared Pulse
        time_weight, weekend_weight = get_wat_time_weights()
        shared_market_pulse = random.gauss(0, 0.0004)

        # 3. Dynamic Channel Markups
        bank_markup = BANK_MARKUP + time_weight + weekend_weight + shared_market_pulse + random.gauss(0.0003, 0.0002)
        opay_markup = OPAY_SPREAD + time_weight + weekend_weight + shared_market_pulse + random.gauss(0.0006, 0.0003)
        palm_markup = PALMPAY_SPREAD + time_weight + weekend_weight + shared_market_pulse + random.gauss(0.0004, 0.0002)
        monie_markup = opay_markup + random.gauss(0.0001, 0.0002)

        bank_val = round(spot_price * (1 + max(0.0002, bank_markup)), 2)
        opay_val = round(spot_price * (1 + max(0.0002, opay_markup)), 2)
        palm_val = round(spot_price * (1 + max(0.0002, palm_markup)), 2)
        monie_val = round(spot_price * (1 + max(0.0002, monie_markup)), 2)

        # Compute Base P2P Premium percentage for rolling velocity checks
        base_p2p_premium = ((bank_val - spot_price) / spot_price) * 100
        rolling_metrics = calculate_rolling_velocity(spot_price, base_p2p_premium)

        # 4. Global Macro Rates & Foreign Corridor Modeling
        fx_res = requests.get("https://open.er-api.com/v6/latest/USD", headers=HEADERS, timeout=7).json()
        rates = fx_res.get("rates", {})
        
        global_aed = float(rates.get("AED", 3.6725))
        global_cny = float(rates.get("CNY", 7.24))
        global_sar = float(rates.get("SAR", 3.75))

        local_aed = round(global_aed * (1 + 0.0064 + random.gauss(0, 0.0002)), 4)
        local_cny = round(global_cny * (1 + 0.0120 + random.gauss(0, 0.0003)), 4)
        local_sar = round(global_sar * (1 + 0.0040 + random.gauss(0, 0.0002)), 4)

        return {
            "global_ngn": spot_price,       
            "bank": bank_val,             
            "opay": opay_val,             
            "palm": palm_val,          
            "monie": monie_val, 
            "global_aed": global_aed,
            "local_aed": local_aed,
            "global_cny": global_cny,
            "local_cny": local_cny,
            "global_sar": global_sar,
            "local_sar": local_sar,
            "meta": rolling_metrics
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Market data aggregation error: {str(e)}")

@app.get("/health")
def health_check():
    return {"status": "awake", "engine": "operational"}
