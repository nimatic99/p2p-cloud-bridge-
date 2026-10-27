import os
import json
import requests
from fastapi import FastAPI, HTTPException

app = FastAPI()

# Calibrated real-world merchant markups
BANK_MARKUP = float(os.getenv("BANK_MARKUP", "0.0010"))       # 0.10%
PALMPAY_SPREAD = float(os.getenv("PALMPAY_SPREAD", "0.0012")) # 0.12%
OPAY_SPREAD = float(os.getenv("OPAY_SPREAD", "0.0024"))       # 0.24%

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json"
}

@app.get("/get-market-data")
def get_market_data():
    try:
        # 1. Fetch live USDT/NGN market ticker from Quidax OpenAPI endpoint
        quidax_url = "https://openapi.quidax.io/exchange-open-api/api/v1/markets/tickers/usdtngn"
        quidax_res = requests.get(quidax_url, headers=HEADERS, timeout=7)
        
        if quidax_res.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Quidax HTTP {quidax_res.status_code}")

        raw_content = quidax_res.json()
        quidax_json = json.loads(raw_content) if isinstance(raw_content, str) else raw_content
        
        market_data = quidax_json.get("data", {})
        usdtngn_data = market_data.get("usdtngn", {})
        ticker_data = usdtngn_data.get("ticker", {})
        
        spot_price = float(ticker_data.get("last", ticker_data.get("sell", 0)))
        
        if spot_price <= 0:
            raise ValueError(f"Invalid spot rate extracted: {quidax_json}")

        # 2. Fetch global FX macro feeds via ExchangeRate-API
        fx_res = requests.get("https://open.er-api.com/v6/latest/USD", headers=HEADERS, timeout=7).json()
        rates = fx_res.get("rates", {})
        
        global_aed = float(rates.get("AED", 3.6725))
        global_cny = float(rates.get("CNY", 7.24))
        global_sar = float(rates.get("SAR", 3.75))

        # 3. Compute execution rates using your tight markups
        bank_val = round(spot_price * (1 + BANK_MARKUP), 2)
        opay_val = round(spot_price * (1 + OPAY_SPREAD), 2)
        palm_val = round(spot_price * (1 + PALMPAY_SPREAD), 2)
        monie_val = opay_val

        # 4. Modeled local OTC/corridor rates
        local_aed = round(global_aed * 1.003, 4)
        local_cny = round(global_cny * 1.012, 4)
        local_sar = round(global_sar * 1.004, 4)

        # Keys precisely mapped to match local orchestrator requirements
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
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Market data aggregation error: {str(e)}")

@app.get("/")
def health_check():
    return {"status": "Render P2P Cloud Bridge is Live"}
