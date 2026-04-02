#!/usr/bin/env python3
"""
A股量化分析 - 纯分析引擎（不联网）
接收 JSON 数据文件路径，计算 20+ 项指标，输出 Markdown 报告

用法:
  python3 analyzer.py <kline.json>

数据格式 (JSON):
{
  "name": "比亚迪",
  "code": "002594", 
  "klines": [
    {"date": "2025-01-02", "open": 93.01, "close": 89.04, "high": 93.04, "low": 88.48, "volume": 168784, "amount": 4624073917.86},
    ...
  ]
}
"""

import sys
import json
import math


def ma(data, period):
    r = [None] * (period - 1)
    for i in range(period - 1, len(data)):
        r.append(sum(data[i - period + 1:i + 1]) / period)
    return r


def ema(data, period):
    k = 2.0 / (period + 1)
    r = [data[0]]
    for v in data[1:]:
        r.append(v * k + r[-1] * (1 - k))
    return r


def rsi(data, period=14):
    r = [None] * period
    for i in range(period, len(data)):
        gains = [max(data[j] - data[j - 1], 0) for j in range(i - period + 1, i + 1)]
        losses = [max(data[j - 1] - data[j], 0) for j in range(i - period + 1, i + 1)]
        ag = sum(gains) / period
        al = sum(losses) / period
        r.append(100 - 100 / (1 + ag / al) if al else 100)
    return r


def macd_calc(closes, fp=12, sp=26, signal_p=9):
    e12 = ema(closes, fp)
    e26 = ema(closes, sp)
    macd_l = [a - b for a, b in zip(e12, e26)]
    sig = ema(macd_l, signal_p)
    hist = [m - s for m, s in zip(macd_l, sig)]
    return macd_l, sig, hist


def bollinger(closes, period=20, k=2):
    mid = ma(closes, period)
    std = [None] * (period - 1)
    for i in range(period - 1, len(closes)):
        vals = closes[i - period + 1:i + 1]
        m = sum(vals) / period
        std.append(math.sqrt(sum((x - m) ** 2 for x in vals) / period))
    upper = [None if v is None else round(v + k * s, 4) for v, s in zip(mid, std)]
    lower = [None if v is None else round(v - k * s, 4) for v, s in zip(mid, std)]
    return upper, mid, lower


def atr(highs, lows, closes, period=14):
    tr = [highs[0] - lows[0]]
    for i in range(1, len(highs)):
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    atrs = [None] * (period - 1)
    for i in range(period - 1, len(tr)):
        atrs.append(sum(tr[i - period + 1:i + 1]) / period)
    return atrs


def kdj(highs, lows, closes, n=9, m1=3, m2=3):
    k = [50.0]
    d = [50.0]
    for i in range(1, len(closes)):
        ll = min(lows[max(0, i - n + 1):i + 1])
        hh = max(highs[max(0, i - n + 1):i + 1])
        rsv = (closes[i] - ll) / (hh - ll) * 100 if hh != ll else 50
        k.append((2 / 3) * k[-1] + (1 / 3) * rsv)
        d.append((2 / 3) * d[-1] + (1 / 3) * k[-1])
    j = [None if (v is None or w is None) else 3 * v - 2 * w for v, w in zip(k, d)]
    return k, d, j


def volatility_calc(data, period=20):
    r = [None] * (period - 1)
    for i in range(period - 1, len(data)):
        vals = data[i - period + 1:i + 1]
        m = sum(vals) / period
        r.append(math.sqrt(sum((x - m) ** 2 for x in vals) / period))
    return r


def analyze(data):
    name = data.get("name", "未知")
    code = data.get("code", "?")
    klines = data["klines"]

    dates   = [k["date"] for k in klines]
    opens   = [k["open"] for k in klines]
    closes  = [k["close"] for k in klines]
    highs   = [k["high"] for k in klines]
    lows    = [k["low"] for k in klines]
    volumes = [k["volume"] for k in klines]

    n = len(closes)
    lc = closes[-1]
    ld = dates[-1]

    ma5   = ma(closes, 5)
    ma10  = ma(closes, 10)
    ma20  = ma(closes, 20)
    ma60  = ma(closes, 60)
    ma120 = ma(closes, 120)

    ma5v  = ma5[-1]
    ma10v = ma10[-1]
    ma20v = ma20[-1]
    ma60v = ma60[-1]
    ma120v = ma120[-1]

    rsi14 = rsi(closes, 14)
    rsi28 = rsi(closes, 28)
    rsi14v = rsi14[-1]
    rsi28v = rsi28[-1]

    macd_l, sig_line, macd_hist = macd_calc(closes)
    macd_v = macd_hist[-1]
    macd_prev = macd_hist[-2]

    boll_up, boll_mid, boll_low = bollinger(closes)
    boll_up_v = boll_up[-1]
    boll_low_v = boll_low[-1]

    atr_v = atr(highs, lows, closes, 14)[-1]

    vol20 = volatility_calc(closes, 20)
    vol60 = volatility_calc(closes, 60)
    vol20v = vol20[-1]
    vol60v = vol60[-1]

    vol_ma5  = sum(volumes[-5:]) / 5
    vol_ma20 = sum(volumes[-20:]) / 20
    vol_r = volumes[-1] / vol_ma20

    kdj_k, kdj_d, kdj_j = kdj(highs, lows, closes)
    kk, kd, kj = kdj_k[-1], kdj_d[-1], kdj_j[-1]

    # 历史高低
    h_all, l_all = max(highs), min(lows)
    h_all_i = highs.index(h_all)
    l_all_i = lows.index(l_all)

    hi120 = max(closes[-120:]) if n >= 120 else max(closes)
    lo120 = min(closes[-120:]) if n >= 120 else min(closes)
    hi60  = max(closes[-60:]) if n >= 60 else max(closes)
    lo60  = min(closes[-60:]) if n >= 60 else min(closes)
    hi20  = max(closes[-20:])
    lo20  = min(closes[-20:])

    ytd  = (lc - closes[0]) / closes[0] * 100
    chg  = closes[-1] - closes[-2]
    chg_pct = chg / closes[-2] * 100
    above_ma5 = sum(1 for i in range(max(0, n - 20), n) if closes[i] > ma5[i])

    # 趋势判断
    if ma5v > ma10v > ma20v: ma_trend = "多头排列（强势）"
    elif ma5v < ma10v < ma20v: ma_trend = "空头排列（弱势）"
    elif lc > ma20v: ma_trend = "均线收敛，向上突破"
    elif lc < ma20v: ma_trend = "均线收敛，向下破位"
    else: ma_trend = "震荡整理"

    gold_cross = ma5v > ma10v and ma10v > ma20v
    death_cross = ma5v < ma10v and ma10v < ma20v

    boll_pos = (lc - boll_low_v) / (boll_up_v - boll_low_v) * 100

    # 综合评分
    score = sum([
        lc > ma5v,
        ma5v > ma10v,
        ma10v > ma20v,
        macd_v > 0,
        rsi14v > 50,
        chg > 0,
    ])
    overall = "🟢 偏强" if score >= 5 else "🔴 偏弱" if score <= 2 else "🟡 中性"

    def f(v): return f"{v:.2f}" if v is not None else "N/A"
    def fi(v): return f"{v:.4f}" if v is not None else "N/A"

    print()
    print("=" * 58)
    print(f"  📊 {name}（{code}）A股量化分析报告")
    print(f"  数据：{dates[0]} → {ld}  共{n}个交易日")
    print("=" * 58)
    print()
    print("【一、价格定位】")
    print(f"  今日收盘：{f(lc)}  {'+' if chg>=0 else ''}{f(chg)}（{'+' if chg_pct>=0 else ''}{chg_pct:.2f}%）")
    print(f"  年初至今：{'+' if ytd>=0 else ''}{ytd:.2f}%  (年初{f(closes[0])})")
    print(f"  20日低→高：{f(lo20)} → {f(hi20)}")
    print(f"  60日低→高：{f(lo60)} → {f(hi60)}")
    print(f"  关键支撑：{f(lo20)} / {f(lo60)}")
    print(f"  关键压力：{f(hi20)} / {f(hi60)}")
    print()
    print("【二、均线系统】")
    print(f"  MA5：  {f(ma5v)}  {'↑价在均线上' if lc>ma5v else'↓价在均线下'}")
    print(f"  MA10： {f(ma10v)}  {'↑' if lc>ma10v else'↓'}")
    print(f"  MA20： {f(ma20v)}  {'↑' if lc>ma20v else'↓'}")
    print(f"  MA60： {f(ma60v)}  {'↑' if lc>ma60v else'↓'}")
    print(f"  MA120：{f(ma120v)}  {'↑' if lc>ma120v else'↓'}")
    print(f"  态势：{ma_trend}")
    print(f"  {'✅ 金叉（MA5>MA10>MA20）' if gold_cross else '🔴 死叉（MA5<MA10<MA20）' if death_cross else '⚪ 混乱排列'}")
    print(f"  近20日收盘>MA5天数：{above_ma5}/20")
    print()
    print("【三、RSI】")
    r14_sig = "🔴超买" if rsi14v>=70 else "🔵超卖" if rsi14v<=30 else "偏强" if rsi14v>=60 else "偏弱" if rsi14v<=40 else "中性"
    r28_sig = "偏强" if rsi28v>55 else "偏弱" if rsi28v<45 else "中性"
    print(f"  RSI(14)={f(rsi14v)}  {r14_sig}")
    print(f"  RSI(28)={f(rsi28v)}  {r28_sig}")
    print()
    print("【四、MACD】")
    macd_sig = "红柱↑" if macd_v>0 else "绿柱↓"
    macd_chg = "收窄中" if abs(macd_v)<abs(macd_prev) else "放大中"
    print(f"  DIF：{f(macd_l[-1])}  DEA：{f(sig_line[-1])}")
    print(f"  MACD柱：{fi(macd_v)}  {macd_sig}，{macd_chg}")
    print()
    print("【五、KDJ】")
    kdj_sig = "超买区" if kj>80 else "超卖区" if kj<20 else "中性"
    kdj_cross = "✅ K>D（金叉）" if kk>kd else "🔴 K<D（死叉）"
    print(f"  K={f(kk)}  D={f(kd)}  J={f(kj)}  {kdj_sig}")
    print(f"  {kdj_cross}")
    print()
    print("【六、布林带】")
    boll_pos_txt = "碰上轨⚠️" if lc>=boll_up_v else "穿下轨⚠️" if lc<=boll_low_v else f"中轨附近({boll_pos:.0f}%)"
    print(f"  上轨：{f(boll_up_v)}  中轨：{f(boll_mid[-1])}  下轨：{f(boll_low_v)}")
    print(f"  当前位置：{boll_pos_txt}")
    print()
    print("【七、ATR】")
    atr_sig = "高波动" if atr_v>lc*0.03 else "正常" if atr_v>lc*0.015 else "低波动"
    print(f"  ATR(14)={f(atr_v)}  {atr_sig}")
    print()
    print("【八、波动率】")
    print(f"  20日：{f(vol20v)}  {'高' if vol20v>lc*0.025 else '正常' if vol20v>lc*0.015 else '低'}")
    print(f"  60日：{f(vol60v)}")
    print()
    print("【九、成交量】")
    vol_txt = "🔴放量" if vol_r>1.2 else "🔵缩量" if vol_r<0.8 else "⚪正常量"
    print(f"  今日：{volumes[-1]/10000:.1f}万手  5日均量：{vol_ma5/10000:.1f}万手  20日均量：{vol_ma20/10000:.1f}万手")
    print(f"  量比：{vol_r:.2f}倍  {vol_txt}")
    print()
    print("【十、历史区间】")
    print(f"  全周期最高：{f(h_all)}（{dates[h_all_i]}）  最低：{f(l_all)}（{dates[l_all_i]}）")
    print(f"  距高点：{((lc-h_all)/h_all*100):.1f}%  距低点：{((lc-l_all)/l_all*100):+.1f}%")
    print()
    print("【十一、综合研判】")
    print(f"  综合评分：{overall}（{score}/6）")
    print(f"  {'✅ 短线强势' if gold_cross else '🔴 短线弱势' if death_cross else '⚪ 短线中性'}")
    print(f"  MACD：{macd_sig}")
    print(f"  量能：{vol_txt}（{vol_r:.2f}x）")
    print()
    print(f"  ▶ 关键支撑：{f(lo20)} / {f(lo60)}")
    print(f"  ▶ 关键压力：{f(ma5v)} / {f(ma20v)}")
    print(f"  ▶ 当前位置：{boll_pos_txt}，RSI(14)={f(rsi14v)}({r14_sig})")
    print()
    print("⚠️ 免责声明：以上为量化模型客观分析，不构成投资建议。")
    print("=" * 58)

    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 analyzer.py <data.json>", file=sys.stderr)
        sys.exit(1)
    path = sys.argv[1]
    try:
        with open(path) as f:
            data = json.load(f)
        sys.exit(analyze(data))
    except Exception as e:
        print(f"❌ 分析失败: {e}", file=sys.stderr)
        sys.exit(1)
