import csv
import json
import queue
import sqlite3
import threading
import time
import traceback
from collections import deque
from datetime import datetime, timedelta
from curl_cffi import requests as c_requests

# ==========================================
# 🛑 ENTERPRISE CONFIGURATION AREA
# ==========================================
TELEGRAM_BOT_TOKEN = "8878950927:AAGPChR866iii8us_MGQ9RngzJYa32yJYBg"
VIP_MEMBER_GROUP_ID = "-1004343394067"  # Unified VIP Client Group (Tier 1 & Tier 2 Merged)
TIER_1_CHAT_ID = VIP_MEMBER_GROUP_ID
TIER_2_CHAT_ID = VIP_MEMBER_GROUP_ID
TIER_3_CHAT_ID = "-1004406022772"  # Elite Corridors
DB_FILE = "enterprise_p2p.db"

CLOUD_BRIDGE_URL = "https://nimatic-currency-scanner.onrender.com/get-market-data"

db_queue = queue.Queue()
LAST_ALERT_TIME = {"tier1": 0, "tier2_session": 0, "btc_shock": 0, "tier3": {}}

CHANNEL_TARIFFS = {
    "Commercial Bank (SWIFT Wire)": {
        "fixed_fee_usd": 65.0,
        "percent_markup": 3.0,
        "settlement_days": 3,
        "max_limit_usd": float('inf'),
    },
    "Fintech Apps (Afriex/LemFi)": {
        "fixed_fee_usd": 0.0,
        "percent_markup": 1.0,
        "settlement_days": 0.5,
        "max_limit_usd": 5000,
    },
    "Exchange House (Al Ansari/MTO)": {
        "fixed_fee_usd": 15.0,
        "percent_markup": 1.2,
        "settlement_days": 1,
        "max_limit_usd": 25000,
    },
    "Procurement Broker (WeChat/Alipay)": {
        "fixed_fee_usd": 0.0,
        "percent_markup": 1.5,
        "settlement_days": 0.2,
        "max_limit_usd": 50000,
    },
}

VOLUME_BANDS = {
    "MICRO": (200, "MICRO ($50 - $499)"),
    "RETAIL": (2500, "RETAIL ($500 - $4,999)"),
    "COMMERCIAL": (15000, "COMMERCIAL ($5k - $24.9k)"),
    "INSTITUTIONAL": (50000, "INSTITUTIONAL ($25k - $90k+)"),
}

def calculate_channel_net(raw_arbitrage_spread, trade_amount_usd, channel_name):
    config = CHANNEL_TARIFFS.get(channel_name)
    if not config or trade_amount_usd > config["max_limit_usd"]:
        return None

    fixed_impact = (config["fixed_fee_usd"] / trade_amount_usd) * 100
    total_friction = fixed_impact + config["percent_markup"]
    net_spread = raw_arbitrage_spread - total_friction

    return {
        "viable": net_spread > 0,
        "net_spread": round(net_spread, 2),
        "speed": f"{config['settlement_days']} Days",
    }

def db_worker():
    while True:
        data = db_queue.get()
        if data is None:
            break
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        try:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS market_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    macro_spot_ngn REAL,
                    bank_ngn REAL,
                    opay_ngn REAL,
                    palmpay_ngn REAL,
                    moniepoint_ngn REAL,
                    aed_premium REAL,
                    cny_premium REAL,
                    sar_premium REAL
                )
            """)
            cursor.execute("CREATE TABLE IF NOT EXISTS bot_state (key TEXT PRIMARY KEY, value REAL)")
            cursor.execute("""
                INSERT INTO market_logs (timestamp, macro_spot_ngn, bank_ngn, opay_ngn, palmpay_ngn, moniepoint_ngn, aed_premium, cny_premium, sar_premium)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, data)
            conn.commit()
        except Exception as e:
            print(f"❌ Async DB Error: {e}")
        finally:
            conn.close()
            db_queue.task_done()

threading.Thread(target=db_worker, daemon=True).start()

def get_persistent_state(key, default_val):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS bot_state (key TEXT PRIMARY KEY, value REAL)")
        cursor.execute("SELECT value FROM bot_state WHERE key = ?", (key,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else default_val
    except Exception:
        return default_val

def set_persistent_state(key, val):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO bot_state (key, value) VALUES (?, ?)", (key, val))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ State Save Error: {e}")

def fetch_cloud_market_data():
    try:
        res = c_requests.get(CLOUD_BRIDGE_URL, timeout=20).json()
        meta = res.get("meta", {})
        return (
            res.get("macro_spot_baseline"), res.get("current_local_rate"), res.get("bank"), res.get("opay"),
            res.get("palm"), res.get("monie"), res.get("global_aed"),
            res.get("local_aed"), res.get("global_cny"), res.get("local_cny"),
            res.get("global_sar"), res.get("local_sar"),
            meta.get("velocity_10m", 0.0), meta.get("velocity_60m", 0.0),
            meta.get("spot_delta_30m", 0.0), meta.get("p2p_delta_30m", 0.0),
            meta.get("btc_delta_15m", 0.0), meta.get("is_btc_shock", False),
            meta.get("velocity_10m", 0.0),  # Padding safety buffer mapping
            meta.get("is_sustained_surge", False), meta.get("is_sustained_crash", False),
            meta.get("is_global_spot_lag", False), meta.get("is_local_liquidity_shock", False)
        )
    except Exception as err:
        print(f"❌ Cloud Bridge Error: {err}")
        return (None,) * 23

def send_alert(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        c_requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}, timeout=4)
    except Exception:
        pass

def main():
    print("🚀 Cloud Background Worker Active (3-Hour Cadence & -0.1% Buy-Side Markup)...")
    
    session_premium_buffer = deque(maxlen=240)

    while True:
        try:
            current_time = time.time()
            (
                macro_spot_baseline, current_local_rate, bank, opay, palm, monie,
                global_aed, local_aed, global_cny, local_cny,
                global_sar, local_sar, velocity_10m, velocity_60m,
                spot_delta_30m, p2p_delta_30m, btc_delta_15m, is_btc_shock,
                dummy_padding, is_sustained_surge, is_sustained_crash,
                is_global_spot_lag, is_local_liquidity_shock
            ) = fetch_cloud_market_data()

            if macro_spot_baseline is None:
                time.sleep(15)
                continue

            base_p2p_premium = ((bank - macro_spot_baseline) / macro_spot_baseline) * 100
            fintech_avg = (opay + palm + monie) / 3

            prem_aed = ((local_aed - global_aed) / global_aed) * 100
            prem_cny = ((local_cny - global_cny) / global_cny) * 100
            prem_sar = ((local_sar - global_sar) / global_sar) * 100

            db_queue.put((
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                macro_spot_baseline, bank, opay, palm, monie,
                prem_aed, prem_cny, prem_sar
            ))

            session_premium_buffer.append(base_p2p_premium)

            print(f"📊 Cloud Cycle: Macro Spot Baseline: {macro_spot_baseline:,.2f} | Local Rate: {current_local_rate:,.2f} | Premium: {base_p2p_premium:.2f}% | 30m P2P Delta: {p2p_delta_30m:+.2f}pts")

            # --- TIER 1: BTC VOLATILITY SHOCK RADAR ---
            if is_btc_shock and (current_time - LAST_ALERT_TIME["btc_shock"] > 1800):
                if btc_delta_15m > 0:
                    btc_title = "🚀 *GLOBAL BTC SURGE SPIKE (FOMO CATALYST)*"
                    btc_directive = (
                        "• **Market State:** BTC broke upward rapidly (`+1.5%`+ in 15 mins).\n"
                        "• *Operational Directive:* **HIGH RETAIL DEMAND INCOMING.** Expect a wave of buyers rushing to acquire USDT. Keep your neobank sell ads active and ready to capture wider spreads."
                    )
                else:
                    btc_title = "🩸 *GLOBAL BTC DUMP CRASH (PANIC CATALYST)*"
                    btc_directive = (
                        "• **Market State:** BTC dropped sharply (`-1.5%`+ in 15 mins).\n"
                        "• *Operational Directive:* **PANIC EXIT RUSH.** Expect a rush of sellers dumping crypto to cash out fiat. Tighten your buy-side bids and manage your inventory exposure."
                    )

                btc_msg = (
                    f"⚡ *VIP DESK: GLOBAL BTC VOLATILITY CATALYST* ⚡\n\n"
                    f"{btc_title}\n"
                    f"• **BTC 15-Minute Delta:** `{btc_delta_15m:+.2f}%`\n"
                    f"• **Current Local Rate:** `{current_local_rate:,.2f} NGN`\n\n"
                    f"🛡️ *Operational Directive:*\n"
                    f"{btc_directive}"
                )
                send_alert(TIER_1_CHAT_ID, btc_msg)
                LAST_ALERT_TIME["btc_shock"] = current_time

            # --- TIER 1: P2P VELOCITY & SHOCK RADAR ---
            last_t1_alert = LAST_ALERT_TIME["tier1"]
            if abs(p2p_delta_30m) >= 0.45 and (current_time - last_t1_alert > 1800):
                if is_global_spot_lag:
                    directive_title = "⚠️ *GLOBAL SPOT LAG DETECTED*"
                    safety_advice = (
                        "• **Macro Spot Baseline:** Spiked while local P2P sits flat.\n"
                        "• *Operational Directive:* **HOLD YOUR LINE.** Retail buyers will catch up shortly."
                    )
                elif is_local_liquidity_shock:
                    if p2p_delta_30m > 0:
                        directive_title = "🚨 *LOCAL LIQUIDITY SURGE (EXPANSION)*"
                        safety_advice = (
                            "• **Local P2P Premium:** Expanded rapidly upward (Shift: " + f"`{p2p_delta_30m:+.2f} pts`" + ").\n"
                            "• *Operational Directive:* **RE-PRICE UPWARD IMMEDIATELY.** Lift your sell ad floors to capture the wider spread before takers clear your books."
                        )
                    else:
                        directive_title = "🔴 *LOCAL LIQUIDITY COMPRESSION (DROP)*"
                        safety_advice = (
                            "• **Local P2P Premium / Rate:** Compressed downward rapidly (Shift: " + f"`{p2p_delta_30m:+.2f} pts`" + ").\n"
                            "• *Operational Directive:* **TIGHTEN / LOWER SELL FLOORS IMMEDIATELY.** Avoid holding over-priced inventory as the market drops."
                        )
                elif is_sustained_surge or p2p_delta_30m >= 0.45:
                    directive_title = "⚡ *AGGRESSIVE BULLISH SPREAD SURGE*"
                    safety_advice = (
                        "• *Operational Directive:* Takers sweeping books. Lift sell ad floors upward."
                    )
                else:
                    directive_title = "🔴 *RAPID SPREAD COMPRESSION / CRASH*"
                    safety_advice = (
                        "• *Operational Directive:* Premium collapsing toward baseline. Tighten buy-side bids."
                    )

                msg = (
                    f"🚨 *VIP DESK: MARKET VELOCITY & SHOCK RADAR* 🚨\n\n"
                    f"{directive_title}\n"
                    f"📊 *Base P2P Premium:* `+{base_p2p_premium:.2f}%` (30m Shift: `{p2p_delta_30m:+.2f} pts`)\n\n"
                    f"💵 *Telemetry:*\n"
                    f"• Macro Spot Baseline: `{macro_spot_baseline:,.2f} NGN`\n"
                    f"• Current Local Rate: `{current_local_rate:,.2f} NGN`\n\n"
                    f"🛡️ {safety_advice}"
                )
                send_alert(TIER_1_CHAT_ID, msg)
                LAST_ALERT_TIME["tier1"] = current_time

            # --- TIER 2: 3-HOUR SESSION AD PRICING ADVISORY ---
            last_session_time = get_persistent_state("last_session_reset", 0.0)
            three_hours_sec = 3 * 3600

            if (current_time - last_session_time) >= three_hours_sec:
                avg_session_premium = sum(session_premium_buffer) / len(session_premium_buffer) if session_premium_buffer else base_p2p_premium
                
                recommended_bank_ad = current_local_rate * (1 + 0.0010 + (avg_session_premium / 200.0))
                recommended_neobank_ad = current_local_rate * (1 + 0.0020 + (avg_session_premium / 200.0))
                
                # Calibrated sweet-spot buy ad (-0.1% below current local rate)
                recommended_buy_ad = current_local_rate * (1 - 0.0010)

                session_msg = (
                    "🏛️ *VIP DESK: 3-HOUR AD REBALANCING ADVISORY* 🏛️\n\n"
                    f"📊 *3-Hour Session Market Summary:*\n"
                    f"• Average Market Premium: `+{avg_session_premium:.2f}%`\n"
                    f"• Current Local Rate: `{current_local_rate:,.2f} NGN`\n"
                    f"• Macro Spot Baseline: `{macro_spot_baseline:,.2f} NGN`\n\n"
                    f"🎯 *Recommended Session Ad Prices (Next 3 Hours):*\n"
                    f"• **Target Neobank Sell Ad (OPay/Monie/Palm):** `{recommended_neobank_ad:,.2f} NGN`\n"
                    f"• **Target Local Bank Sell Ad:** `{recommended_bank_ad:,.2f} NGN`\n"
                    f"• **Target Buy Ad Price:** `{recommended_buy_ad:,.2f} NGN`\n\n"
                    f"🛡️ *Operational Directive:*\n"
                    f"• Market conditions evaluated. Update your active Bybit ads ONCE to these channel-specific figures for the next 3-hour block."
                )
                send_alert(TIER_2_CHAT_ID, session_msg)
                set_persistent_state("last_session_reset", current_time)

            # --- TIER 3: ELITE CROSS-BORDER CORRIDOR MATRIX ---
            corridor_data = [
                ("Dubai (AED)", prem_aed, global_aed, local_aed),
                ("China (CNY)", prem_cny, global_cny, local_cny),
                ("Saudi Arabia (SAR)", prem_sar, global_sar, local_sar)
            ]

            for label, p_foreign, g_rate, l_rate in corridor_data:
                net_spread = p_foreign - base_p2p_premium
                if net_spread >= 2.0:
                    last_time = LAST_ALERT_TIME["tier3"].get(label, 0)
                    if current_time - last_time > 3600:
                        matrix_text = ""
                        for tier_key in ["RETAIL", "COMMERCIAL", "INSTITUTIONAL"]:
                            usd_val, tier_name = VOLUME_BANDS[tier_key]
                            matrix_text += f"\n🔹 *{tier_name}:*\n"
                            for ch_name in CHANNEL_TARIFFS.keys():
                                net_data = calculate_channel_net(net_spread, usd_val, ch_name)
                                if net_data and net_data.get("viable"):
                                    matrix_text += f"  • {ch_name}: *+{net_data['net_spread']}% Net after fees* (Speed: {net_data['speed']})\n"

                        currency_code = label.split("(")[1].replace(")", "")
                        msg = (
                            f"🌏 *TIER 3 ELITE: {label} IMPORT CORRIDOR MATRIX* 🌏\n\n"
                            f"🎯 *Raw Gross Arbitrage Spread:* *{net_spread:.2f}%*\n\n"
                            f"💱 *Live Rate Snapshot:*\n"
                            f"• Current Local Rate: `{current_local_rate:,.2f} NGN`\n"
                            f"• Global USD/{currency_code}: `{g_rate:,.4f}`\n"
                            f"• Local P2P USD/{currency_code}: `{l_rate:,.4f}`\n"
                            "------------------------------------\n"
                            f"{matrix_text}"
                        )
                        send_alert(TIER_3_CHAT_ID, msg)
                        LAST_ALERT_TIME["tier3"][label] = current_time

        except Exception:
            print(f"💀 Main Thread Fault:\n{traceback.format_exc()}")

        time.sleep(60)

if __name__ == "__main__":
    main()
