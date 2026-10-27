import os
import requests
from fastapi import FastAPI, HTTPException

app = FastAPI()

# Calibrated channel markups established from your merchant observation
BASE_P2P_MARGIN = float(os.getenv("BASE_P2P_MARGIN", "0.0360")) # 3.60% Bank Transfer baseline
OPAY_SPREAD = float(os.getenv("OPAY_SPREAD", "0.0007"))             # +0.07% extra friction
PALMPAY_SPREAD = float(os.getenv("PALMPAY_SPREAD", "0.0012"))       # +0.12% extra friction

@app.get("/get-market-data")
def get_market_data():
    try:
        # 1. Fetch live unblocked USDT/NGN baseline from Quidax (No fake fallbacks)
        quidax_res = requests.get("https://api.quidax.com/v1/markets/usdtngn", timeout=5).json()
        quidax_spot = float(quidax_res.get("data", {}).get("ticker", {}).get("last"))

        # 2. Fetch global FX macro feeds via ExchangeRate-API
        fx_res = requests.get("https://open.er-api.com/v6/latest/USD", timeout=5).json()
        rates = fx_res.get("rates", {})
        
        global_aed = rates.get("AED", 3.6725)
        global_cny = rates.get("CNY", 7.24)
        global_sar = rates.get("SAR", 3.75)

        # 3. Compute calibrated P2P channel execution rates
        bank_ngn = round(quidax_spot * (1 + BASE_P2P_MARGIN), 2)
        opay_ngn = round(quidax_spot * (1 + BASE_P2P_MARGIN + OPAY_SPREAD), 2)
        palmpay_ngn = round(quidax_spot * (1 + BASE_P2P_MARGIN + PALMPAY_SPREAD), 2)
        moniepoint_ngn = opay_ngn  # Synced closely with neobank velocity

        # 4. Modeled local OTC/corridor rates relative to global baselines
        local_aed = global_aed * 1.003
        local_cny = global_cny * 1.012
        local_sar = global_sar * 1.004

        return {
            "binance_ngn": quidax_spot,       
            "bank_ngn": bank_ngn,             
            "opay_ngn": opay_ngn,             
            "palm_ngn": palmpay_ngn,          
            "moniepoint_ngn": moniepoint_ngn, 
            "global_aed": global_aed,
            "local_aed": local_aed,
            "global_cny": global_cny,
            "local_cny": local_cny,
            "global_sar": global_sar,
            "local_sar": local_sar,
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Market data aggregation failed: {str(e)}")

@app.get("/")
def health_check():
    return {"status": "Render P2P Cloud Bridge is Live"}
