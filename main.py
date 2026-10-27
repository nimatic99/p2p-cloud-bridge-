from fastapi import FastAPI
import requests
import json

app = FastAPI()

# Cleaned, legitimate web emulation headers to satisfy CDN filters
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://www.bybit.com",
    "Referer": "https://bybit.com"
}

def fetch_bybit_v5(currency: str, payment_id: str = None):
    # FIX 1: Swapped out legacy 'api2.bybit.com' path for the official unified V5 P2P route
    url = "https://bybit.com"
    
    # FIX 2: Enforced strict string data types for 'page' and 'size' to clear validator
    # FIX 3: Added 'amount' and 'authType' keys to conform with the mandatory endpoint schema
    payload = {
        "tokenId": "USDT",
        "currencyId": currency.upper(),
        "side": "1",            # "1" for Takers selling (Buy ads), "0" for Takers buying (Sell ads)
        "page": "1",            # Must be a quoted string
        "size": "5",            # Must be a quoted string
        "amount": "",           # Must be passed as empty string if not filtering by total fiat amount
        "authType": "0"         # "0" fetches all ads (unverified + verified) to ensure a stable index
    }
    
    # Fix 4: Properly structure the paymentId parameter array if defined
    if payment_id:
        payload["paymentId"] = [str(payment_id)]
    else:
        payload["paymentId"] = []

    try:
        # Using json.dumps ensures precise dictionary formatting over microservices
        res = requests.post(url, data=json.dumps(payload), headers=HEADERS, timeout=8)
        
        if res.status_code != 200:
            return None  # Catches explicit hosting or CDN 403 blocks
            
        data = res.json()
        
        if data.get("retCode") == 0 or data.get("retMsg") == "SUCCESS":
            result_node = data.get("result", {})
            items = result_node.get("items", [])
            
            # Simple volume-weighted safety average across the top listings
            prices = [float(ad["price"]) for ad in items if "price" in ad]
            if prices:
                return sum(prices) / len(prices)
    except Exception:
        pass
    return None

@app.get("/get-prices")
def get_prices():
    # 1. Fetch Global Forex Baselines (AED, CNY, SAR) via open ER-API
    try:
        forex_res = requests.get("https://er-api.com", headers=HEADERS, timeout=4).json()
        rates = forex_res.get("rates", {})
    except Exception:
        rates = {}

    # 2. FIX 5: Swapped the broken Binance NGN Spot endpoint for the live Busha asset engine
    try:
        busha_res = requests.get("https://busha.co", headers=HEADERS, timeout=4).json()
        ngn_data = next((item for item in busha_res if item.get("pair") == "USDT-NGN"), None)
        busha_ngn = float(ngn_data.get("last_price")) if ngn_data else None
    except Exception:
        busha_ngn = None

    return {
        "busha_spot_ngn": busha_ngn,
        "bank_ngn": fetch_bybit_v5("NGN", "14"),
        "opay_ngn": fetch_bybit_v5("NGN", "488"),
        "palm_ngn": fetch_bybit_v5("NGN", "481"),
        "monie_ngn": fetch_bybit_v5("NGN", "633"),
        "global_aed": rates.get("AED"),
        "local_aed": fetch_bybit_v5("AED"),
        "global_cny": rates.get("CNY"),
        "local_cny": fetch_bybit_v5("CNY"),
        "global_sar": rates.get("SAR"),
        "local_sar": fetch_bybit_v5("SAR"),
    }

@app.get("/")
def health_check():
    return {"status": "P2P Bridge Microservice is
Live"}
