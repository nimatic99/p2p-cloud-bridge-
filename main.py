import os
import requests
from fastapi import FastAPI, HTTPException

app = FastAPI()

# Calibrated channel markups
BASE_P2P_MARGIN = float(os.getenv("BASE_P2P_MARGIN", "0.0360")) # 3.60% Bank Transfer baseline
OPAY_SPREAD = float(os.getenv("OPAY_SPREAD", "0.0007"))             # +0.07% extra friction
PALMPAY_SPREAD = float(os.getenv("PALMPAY_SPREAD", "0.0012"))       # +0.12% extra friction

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json"
}

@app.get("/get-market-data")
def get_market_data():
    try:
        # 1. Fetch all market tickers from Quidax open API
        quidax_url = "https://openapi.quidax.io/exchange-open-api/api/v1/markets/tickers"
        quidax_res = requests.get(quidax_url, headers=HEADERS, timeout=7)
        quidax_json = quidax_res.json()
        
        # 2. Iterate through the market list to find usdtngn dynamically
        markets_data = quidax_json.get("data", [])
        quidax_spot = 0.0
        
        for market in markets_data:
            # Check market name or symbol fields
            market_name = str(market.get("market", "")).lower()
            if "usdtngn" in market_name or market_name == "usdtngn":
                ticker = market.get("ticker", {})
                quidax_spot = float(ticker.get("last", ticker.get("sell", 0)))
                break
        
        # Fallback search if market name key differs
        if quidax_spot == 0.0 and isinstance(markets_data, dict):
            usdt_dict = markets_data.get("usdtngn", {})
            ticker = usdt_dict.get("ticker", {})
            quidax_spot = float(ticker.get("last", ticker.get("sell", 0)))

        if quidax_spot <= 0:
            raise ValueError(f"Could not extract usdtngn from Quidax payload: {quidax_json}")

        # 3. Fetch global FX macro feeds via ExchangeRate-API
        fx_res = requests.get("https://open.er-api.com/v6/latest/USD", headers=HEADERS, timeout=7).json()
        rates = fx_res.get("rates", {})
        
        global_aed = float(rates.get("AED", 3.6725))
        global_cny = float(rates.get("CNY", 7.24))
        global_sar = float(rates.get("SAR", 3.75))

        # 4. Compute calibrated P2P channel execution rates
        bank_ngn = round(quidax_spot * (1 + BASE_P2P_MARGIN), 2)
        opay_ngn = round(quidax_spot * (1 + BASE_P2P_MARGIN + OPAY_SPREAD), 2)
        palmpay_ngn = round(quidax_spot * (1 + BASE_P2P_MARGIN + PALMPAY_SPREAD), 2)
        moniepoint_ngn = opay_ngn

        # 5. Modeled local OTC/corridor rates
        local_aed = round(global_aed * 1.003, 4)
        local_cny = round(global_cny * 1.012, 4)
        local_sar = round(global_sar * 1.004, 4)

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
        raise HTTPException(status_code=503, detail=f"Market data aggregation error: {str(e)}")

@app.get("/")
def health_check():
    return {"status": "Render P2P Cloud Bridge is Live"}
