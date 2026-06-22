import asyncio
import time
import logging
import os
import json
logging.disable(logging.CRITICAL)
from pyquotex.stable_api import Quotex

EMAIL    = os.environ.get("QUOTEX_EMAIL", "")
PASSWORD = os.environ.get("QUOTEX_PASSWORD", "")
TOKEN    = os.environ.get("QUOTEX_TOKEN", "")
AMOUNT   = float(os.environ.get("TRADE_AMOUNT", "1.0"))
DURATION = 60
MAX_LOSS_PCT = 5.0
MAX_WIN_PCT  = 10.0

OTC_PAIRS = [
    "USDINR_otc",
    "EURUSD_otc",
    "EURGBP_otc",
    "AUDCAD_otc",
    "GBPUSD_otc",
    "EURCAD_otc",
    "AUDCHF_otc",
]

def inject_session():
    """Session file inject karo taaki Render pe login bypass ho"""
    if not TOKEN or not EMAIL:
        return
    session_data = {
        EMAIL: {
            "cookies": os.environ.get("QUOTEX_COOKIES", ""),
            "token": TOKEN,
            "user_agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/119.0"
        }
    }
    os.makedirs(".", exist_ok=True)
    with open("session.json", "w") as f:
        json.dump(session_data, f)
    print("✅ Session injected!")

def calc_ema(closes, period=20):
    if len(closes) < period: return None
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for p in closes[period:]: ema = p * k + ema * (1 - k)
    return ema

def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return None
    gains, losses = [], []
    for i in range(1, period + 1):
        d = closes[-i] - closes[-i-1]
        gains.append(max(d, 0)); losses.append(max(-d, 0))
    ag = sum(gains)/period; al = sum(losses)/period
    if al == 0: return 100
    return 100 - (100 / (1 + ag/al))

def score_signal(candles, ema20, rsi):
    if len(candles) < 3: return 0, "none"
    score = 0
    curr  = candles[-1]
    prev  = candles[-2]
    prev2 = candles[-3]
    price = curr["close"]
    bodies = [abs(c["close"]-c["open"]) for c in candles[-10:]]
    avg_body = sum(bodies)/len(bodies) if bodies else 0
    bullish_trend = price > ema20
    bearish_trend = price < ema20
    if bullish_trend or bearish_trend: score += 25
    if bullish_trend and rsi > 50: score += 25
    elif bearish_trend and rsi < 50: score += 25
    curr_body = abs(curr["close"] - curr["open"])
    if curr_body > avg_body * 0.8 and curr_body < avg_body * 2.5: score += 20
    if bullish_trend and 50 < rsi < 75: score += 15
    elif bearish_trend and 25 < rsi < 50: score += 15
    prev_green  = prev["close"] > prev["open"]
    prev_red    = prev["close"] < prev["open"]
    curr_green  = curr["close"] > curr["open"]
    curr_red    = curr["close"] < curr["open"]
    prev2_green = prev2["close"] > prev2["open"]
    prev2_red   = prev2["close"] < prev2["open"]
    if bullish_trend and prev2_green and prev_green and curr_green: score += 15
    elif bearish_trend and prev2_red and prev_red and curr_red: score += 15
    if bullish_trend and rsi > 50: direction = "call"
    elif bearish_trend and rsi < 50: direction = "put"
    else: direction = "none"
    return score, direction

async def analyze_pair(client, raw_asset):
    try:
        asset, asset_info = await client.get_available_asset(raw_asset, force_open=True)
        if not asset_info or not asset_info[0]: return None
        payout = client.get_payout_by_asset(asset)
        if not payout or payout < 75: return None
        candles = await client.get_historical_candles(asset, 40*60, 60)
        if not candles or len(candles) < 22: return None
        closes = [float(c["close"]) for c in candles]
        ema20  = calc_ema(closes, 20)
        rsi    = calc_rsi(closes, 14)
        if not ema20 or not rsi: return None
        score, direction = score_signal(candles, ema20, rsi)
        if score < 80 or direction == "none": return None
        return {"asset": asset, "raw": raw_asset, "direction": direction,
                "score": score, "payout": payout, "rsi": rsi}
    except Exception as e:
        print(f"  ⚠️ {raw_asset}: {e}")
        return None

async def main():
    # Session inject karo pehle
    inject_session()

    print("🤖 Quotex Multi-Pair Bot Starting...")
    print(f"📊 Pairs: {', '.join(OTC_PAIRS)}")

    client = Quotex(email=EMAIL, password=PASSWORD, lang="en")
    connected, _ = await client.connect()
    if not connected:
        print("❌ Connection failed!"); return

    await client.change_account("PRACTICE")
    await asyncio.sleep(2)

    start = await client.get_balance()
    trades = wins = losses = win_streak = 0
    pause_until = 0
    print(f"✅ Connected! Balance: ${start:.2f}\n")

    while True:
        try:
            bal = await client.get_balance()
            pnl = ((bal - start) / start) * 100

            if pnl <= -MAX_LOSS_PCT:
                print(f"🛑 Loss limit! {pnl:.1f}%"); break
            if pnl >= MAX_WIN_PCT:
                print(f"🎉 Target hit! {pnl:.1f}%"); break

            if time.time() < pause_until:
                print(f"⏸️ Paused — {int(pause_until-time.time())}s left")
                await asyncio.sleep(15); continue

            print(f"💰 ${bal:.2f} ({pnl:+.1f}%) | W:{wins} L:{losses}")
            print(f"🔍 Scanning pairs...")

            tasks = [analyze_pair(client, pair) for pair in OTC_PAIRS]
            results = await asyncio.gather(*tasks)
            signals = [r for r in results if r is not None]

            if not signals:
                print("⏳ No signals, waiting 20s...\n")
                await asyncio.sleep(20); continue

            best = max(signals, key=lambda x: x["score"] * x["payout"])
            print(f"🚀 {best['direction'].upper()} | {best['asset']} | Score:{best['score']} | Payout:{best['payout']}%")

            asset, _ = await client.get_available_asset(best["raw"], force_open=True)
            status, buy_info = await client.buy(AMOUNT, asset, best["direction"], DURATION)

            if not status:
                print(f"❌ Failed: {buy_info}\n")
                await asyncio.sleep(10); continue

            trade_id = buy_info.get("id")
            trades += 1
            print(f"✅ Trade #{trades} | {trade_id}")

            win_status, profit = await client.check_win(trade_id)
            if win_status == "win":
                wins += 1; win_streak += 1
                print(f"🟢 WIN +${profit:.2f} | Streak:{win_streak}")
                if win_streak >= 3:
                    pause_until = time.time() + 600
                    win_streak = 0
                    print("⏸️ 3W streak! Pause 10min")
            else:
                losses += 1; win_streak = 0
                print(f"🔴 LOSS -${AMOUNT:.2f}")

            wr = wins/trades*100 if trades > 0 else 0
            print(f"📈 WR:{wr:.0f}% | {wins}W {losses}L\n")
            await asyncio.sleep(10)

        except KeyboardInterrupt:
            print("\n🛑 Stopped"); break
        except Exception as e:
            print(f"⚠️ {e}")
            await asyncio.sleep(5)

    final = await client.get_balance()
    print(f"💰 Final: ${final:.2f} | P&L: ${final-start:+.2f}")
    await client.close()

asyncio.run(main())
