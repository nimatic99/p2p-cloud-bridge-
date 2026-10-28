import os
import json
import random
import requests
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

# In-memory rate velocity cache
MEMORY_CACHE = {
    "last_spot_price": None,
    "last_update_time": None
}

def get_wat_time_weights():
    now_wat = datetime.now(WAT)
    hour = now_wat.hour
    weekday = now_wat.weekday()  # 0=Mon, 5=Sat, 6=Sun
    
    # Night hours (10 PM - 6 AM WAT) increase illiquidity friction
    time_weight = 0.0015 if (hour < 6 or hour >= 22) else 0.0008
    # Weekend friction weight
    weekend_weight = 0.0010 if weekday >= 5 else 0.0
    
    return time_weight, weekend_weight

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

        # 2. Compute 60-Second Spot Velocity (Surge & Crash Engine)
        velocity_pct = 0.0
        is_surge = False
        is_crash = False
        
        if MEMORY_CACHE["last_spot_price"] is not None:
            prev_spot = MEMORY_CACHE["last_spot_price"]
            velocity_pct = ((spot_price - prev_spot) / prev_spot) * 100
            
            if velocity_pct >= 0.5:
                is_surge = True
            elif velocity_pct <= -0.5:
                is_crash = True
                
        # Update cache
        MEMORY_CACHE["last_spot_price"] = spot_price
        MEMORY_CACHE["last_update_time"] = datetime.now(WAT)

        # 3. Calculate WAT Cyclical Weights & Shared Pulse
        time_weight, weekend_weight = get_wat_time_weights()
        shared_market_pulse = random.gauss(0, 0.0004)

        # 4. Bidirectional Velocity Impact (Handling both Surges and Crashes)
        if is_surge:
            # Upside momentum: merchants scramble to reprice upward
            velocity_impact = (velocity_pct * 0.002)
        elif is_crash:
            # Downside crash: merchants lag and hold high sell ads, widening the apparent premium gap
            velocity_impact = (abs(velocity_pct) * 0.0035)
        else:
            velocity_impact = 0.0

        # --- DYNAMIC CHANNEL MARKUPS ---
        # Bank Wire Markup with full velocity integration
        bank_markup = BANK_MARKUP + time_weight + weekend_weight + velocity_impact + shared_market_pulse + random.gauss(0.0003, 0.0002)
        
        # Neobank Markups (Independent noise simulates payment rail demand shifts)
        opay_markup = OPAY_SPREAD + time_weight + weekend_weight + velocity_impact + shared_market_pulse + random.gauss(0.0006, 0.0003)
        palm_markup = PALMPAY_SPREAD + time_weight + weekend_weight + velocity_impact + shared_market_pulse + random.gauss(0.0004, 0.0002)
        monie_markup = opay_markup + random.gauss(0.0001, 0.0002)

        bank_val = round(spot_price * (1 + max(0.0002, bank_markup)), 2)
        opay_val = round(spot_price * (1 + max(0.0002, opay_markup)), 2)
        palm_val = round(spot_price * (1 + max(0.0002, palm_markup)), 2)
        monie_val = round(spot_price * (1 + max(0.0002, monie_markup)), 2)

        # 5. Global Macro Rates & Foreign Corridor Modeling
        fx_res = requests.get("https://open.er-api.com/v6/latest/USD", headers=HEADERS, timeout=7).json()
        rates = fx_res.get("rates", {})
        
        global_aed = float(rates.get("AED", 3.6725))
        global_cny = float(rates.get("CNY", 7.24))
        global_sar = float(rates.get("SAR", 3.75))

        # Dynamic Foreign Corridor Premiums (0.64% AED Bybit baseline + stochastic noise)
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
            "meta": {
                "velocity_pct": round(velocity_pct, 2),
                "is_surge": is_surge,
                "is_crash": is_crash
            }
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Market data aggregation error: {str(e)}")

@app.get("/health")
def health_check():
    return {"status": "awake", "engine": "operational"}
