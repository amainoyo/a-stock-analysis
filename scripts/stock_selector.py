#!/usr/bin/env python3
"""
A 股选股器 - 沪深300过滤器
条件：
  1. RSI(14) 处于 30-45 区间（超卖反弹区）
  2. MACD 绿柱连续收缩 ≥ 3天（空方动能衰竭）
  3. 近3日均量 > 20日均量 × 1.3（放量确认）
  4. KDJ 的 J 值从 < 20 位置转头向上（辅助确认）
  5. 收盘价 > MA20（趋势不破位）
"""

import json
import sys
from typing import Dict, List, Any

def calc_ma(closes: List[float], period: int) -> float:
    if len(closes) < period:
        return sum(closes) / len(closes)
    return sum(closes[-period:]) / period

def calc_ema(closes: List[float], period: int) -> float:
    if not closes:
        return 0.0
    ema = closes[0]
    k = 2.0 / (period + 1)
    for price in closes[1:]:
        ema = price * k + ema * (1 - k)
    return ema

def calc_rsi(closes: List[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d for d in deltas[-period:] if d > 0]
    losses = [-d for d in deltas[-period:] if d < 0]
    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def calc_macd(closes: List[float]):
    if len(closes) < 26:
        return 0.0, 0.0, 0.0
    ema12 = calc_ema(closes, 12)
    ema26 = calc_ema(closes, 26)
    dif = ema12 - ema26
    signal = calc_ema([dif] * len(closes[-9:]), 9) if len(closes) >= 9 else dif * 0.9
    macd_bar = (dif - signal) * 2
    return dif, signal, macd_bar

def calc_kdj(highs: List[float], lows: List[float], closes: List[float], period: int = 9):
    if len(closes) < period:
        return 50.0, 50.0, 50.0
    k = 50.0
    d = 50.0
    m = 2.0 / (period + 1)
    for i in range(period - 1, len(closes)):
        low_min = min(lows[i-period+1:i+1])
        high_max = max(highs[i-period+1:i+1])
        if high_max == low_min:
            rsv = 50.0
        else:
            rsv = (closes[i] - low_min) / (high_max - low_min) * 100
        k = k * (1 - m) + rsv * m
        d = d * (1 - m) + k * m
    j = 3 * k - 2 * d
    return k, d, j

def analyze_stock(data: Dict) -> Dict[str, Any]:
    """分析单只股票，返回评分和信号"""
    klines = data.get("klines", [])
    if len(klines) < 30:
        return {"name": data.get("name", "?"), "code": data.get("code", "?"), "score": 0, "signals": [], "reason": "数据不足"}

    closes = [k["close"] for k in klines]
    highs = [k["high"] for k in klines]
    lows = [k["low"] for k in klines]
    volumes = [k["volume"] for k in klines]

    n = len(closes)
    latest = closes[-1]

    # === 条件1: RSI(14) 处于 30-45 区间 ===
    rsi14 = calc_rsi(closes, 14)
    rsi28 = calc_rsi(closes, 28)
    cond1 = 30 <= rsi14 <= 45 and rsi28 < 50

    # === 条件2: MACD 绿柱连续收缩 ≥ 3天 ===
    macd_bars = []
    for i in range(max(26, n - 20), n):
        sub_closes = closes[:i+1]
        _, _, bar = calc_macd(sub_closes)
        macd_bars.append(bar)
    if len(macd_bars) >= 4:
        # 检查最近4天是否都是绿柱且逐日收缩
        recent = macd_bars[-4:]
        cond2 = all(b < 0 for b in recent) and recent[-1] > recent[-2] > recent[-3]
    elif len(macd_bars) >= 2:
        recent = macd_bars[-2:]
        cond2 = all(b < 0 for b in recent) and recent[-1] > recent[-2]
    else:
        cond2 = False

    # === 条件3: 近3日均量 > 20日均量 × 1.3 ===
    vol_ma3 = sum(volumes[-3:]) / 3
    vol_ma20 = sum(volumes[-20:]) / min(20, len(volumes))
    cond3 = vol_ma3 >= vol_ma20 * 1.3

    # === 条件4: KDJ 的 J 值从 < 20 转头向上 ===
    k, d, j = calc_kdj(highs, lows, closes)
    j_history = [3 * kk - 2 * dd for kk, dd in zip(
        [calc_kdj(highs[:i+1], lows[:i+1], closes[:i+1])[0] for i in range(9, n)],
        [calc_kdj(highs[:i+1], lows[:i+1], closes[:i+1])[1] for i in range(9, n)]
    )] if n > 9 else [j]
    j_below_20 = sum(1 for jj in j_history[-5:] if jj < 20)
    cond4 = j_below_20 >= 2 and j > j_history[-2] if len(j_history) >= 2 else False

    # === 条件5: 收盘价 > MA20 ===
    ma20 = calc_ma(closes, 20)
    cond5 = latest > ma20

    # === 综合评分 ===
    score = sum([cond1, cond2, cond3, cond4, cond5])
    ma5 = calc_ma(closes, 5)
    ma10 = calc_ma(closes, 10)
    _, _, macd_bar_latest = calc_macd(closes)

    signals = []
    if cond1: signals.append("RSI反弹区")
    if cond2: signals.append("MACD收缩蓄力")
    if cond3: signals.append("量能放大")
    if cond4: signals.append("KDJ低位拐头")
    if cond5: signals.append("站稳MA20")

    return {
        "name": data.get("name", "?"),
        "code": data.get("code", "?"),
        "score": score,
        "signals": signals,
        "close": latest,
        "rsi14": round(rsi14, 1),
        "rsi28": round(rsi28, 1),
        "macd_bar": round(macd_bar_latest, 4),
        "j": round(j, 1),
        "ma5": round(ma5, 2),
        "ma10": round(ma10, 2),
        "ma20": round(ma20, 2),
        "vol_ratio": round(vol_ma3 / vol_ma20, 2) if vol_ma20 > 0 else 0,
        "cond1": cond1,
        "cond2": cond2,
        "cond3": cond3,
        "cond4": cond4,
        "cond5": cond5,
        "reason": " | ".join(signals) if signals else "条件不足"
    }

def main():
    if len(sys.argv) < 2:
        print("用法: python3 stock_selector.py <stock_data.json>")
        sys.exit(1)

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        stocks = json.load(f)

    results = []
    for stock in stocks:
        result = analyze_stock(stock)
        if result["score"] >= 3:  # 至少满足3个条件
            results.append(result)

    # 按分数降序排列
    results.sort(key=lambda x: x["score"], reverse=True)

    print("\n" + "="*60)
    print(f"  🎯 A股智能选股器 — 沪深300")
    print(f"  筛选条件：RSI反弹 + MACD收缩 + 量能放大 + KDJ拐头 + MA20支撑")
    print(f"  共扫描 {len(stocks)} 只股票，符合条件 {len(results)} 只")
    print("="*60)

    if not results:
        print("\n⚠️  未找到符合全部条件的标的")
        print("   建议：当前市场偏强势，可适当降低阈值重筛")
        return

    print(f"\n{'排名':<4} {'股票':<8} {'代码':<8} {'评分':<5} {'信号'}")
    print("-"*60)
    for i, r in enumerate(results[:20], 1):
        signals_str = " / ".join(r["signals"])
        print(f"#{i:<3} {r['name']:<6} {r['code']:<8} {r['score']}/5   {signals_str}")

    print("\n" + "="*60)
    print("  Top5 详细数据")
    print("="*60)
    for i, r in enumerate(results[:5], 1):
        print(f"\n#{i} {r['name']}（{r['code']}）")
        print(f"   现价: {r['close']}  |  RSI(14): {r['rsi14']}  |  RSI(28): {r['rsi28']}")
        print(f"   MACD柱: {r['macd_bar']}  |  KDJ-J: {r['j']}")
        print(f"   MA5: {r['ma5']}  MA10: {r['ma10']}  MA20: {r['ma20']}")
        print(f"   量比: {r['vol_ratio']}x  |  满足条件: {r['reason']}")

    print("\n⚠️  仅供参考，不构成投资建议。🦐")
    print("="*60)

if __name__ == "__main__":
    main()
