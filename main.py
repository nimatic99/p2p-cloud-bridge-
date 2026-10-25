from fastapi import FastAPI
from curl_cffi import requests as c_requests

app = FastAPI()


def fetch_bybit_market(currency, method_id=None):
  url = "https://api2.bybit.com/fiat/otc/item/online"
  payload = {
      "tokenId": "USDT",
      "currencyId": currency,
      "side": "1",
      "size": "5",
      "page": "1",
  }
  if method_id:
    payload["paymentId"] = [str(method_id)]

  try:
    # Use curl_cffi to bypass Cloudflare TLS fingerprinting securely from the cloud
    response = c_requests.post(
        url, json=payload, impersonate="chrome120", timeout=8
    )
    if response.status_code == 200:
      res = response.json()
      if res.get("retCode") in ["0", 0]:
        items = res.get("result", {}).get("items", [])
        prices = [float(ad["price"]) for ad in items if "price" in ad]
        if prices:
          return sum(prices) / len(prices)
  except Exception as e:
    print(f"Cloud fetch error for {currency}: {e}")

  return None


@app.get("/get-market-data")
def get_market_data():
  # 1. Fetch Global Forex Rates against USD
  forex_rates = {}
  try:
    forex_res = c_requests.get(
        "https://open.er-api.com/v6/latest/USD",
        impersonate="chrome120",
        timeout=5,
    ).json()
    forex_rates = forex_res.get("rates", {})
  except Exception:
    pass

  # 2. Fetch Binance Spot Baseline
  binance_ngn = None
  try:
    bn_res = c_requests.get(
        "https://api.binance.com/api/v3/ticker/price?symbol=USDTNGN",
        impersonate="chrome120",
        timeout=4,
    ).json()
    binance_ngn = float(bn_res.get("price", 0))
  except Exception:
    pass

  return {
      "binance_ngn": binance_ngn,
      "bank_ngn": fetch_bybit_market("NGN", "14"),
      "opay_ngn": fetch_bybit_market("NGN", "488"),
      "palm_ngn": fetch_bybit_market("NGN", "481"),
      "monie_ngn": fetch_bybit_market("NGN", "633"),
      "global_aed": forex_rates.get("AED"),
      "local_aed": fetch_bybit_market("AED"),
      "global_cny": forex_rates.get("CNY"),
      "local_cny": fetch_bybit_market("CNY"),
      "global_sar": forex_rates.get("SAR"),
      "local_sar": fetch_bybit_market("SAR"),
  }


@app.get("/")
def home():
  return {"status": "P2P Cloud Ingestion Bridge Active"}
