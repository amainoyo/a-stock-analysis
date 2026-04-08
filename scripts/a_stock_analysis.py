#!/usr/bin/env python3
"""
A股量化分析工具 - 全面版
支持：MA/RSI/MACD/布林带/波动率/均量/支撑压力/PE-PB等20+指标
"""

import sys
import json
import urllib.request
import urllib.parse
import math

# ─── 1. 股票搜索 ───────────────────────────────────────────────
def search_stock(keyword):
    """通过东方财富搜索股票，返回 secid 和名称"""
    url = f"https://searchapi.eastmoney.com/api/suggest/get"
    params = {
        "input": keyword,
        "type": "14",
        "token": "D43BF722C8E33BDC906FB84D85E326E58",
        "count": "5",
        "market": "沪深京A股"
    }
    url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
    items = data.get("QuotationCodeTable", {}).get("Data", [])
    if not items:
        raise ValueError(f"未找到股票: {keyword}")
    # 优先选择 A 股
    for item in items:
        if item.get("SecurityTypeName") in ("A股", "沪深A股"):
            return item["Code"], item["Name"], item.get("MktNum", "1")
    # fallback 取第一个
    item = items[0]
    return item["Code"], item["Name"], item.get("MktNum", "1")


def get_secid(code, mktnum):
    """根据市场编号构造 secid
    市场编号: 0=深圳A, 1=上海A, 100=北京, 2=深圳B
    secid格式: 市场代码.股票代码
    """
    mktmap = {
        "0": "0",   # 深圳A
        "1": "1",   # 上海A
        "100": "2", # 北京
        "2": "0",   # 深圳B -> 归到深圳A
    }
    mkt = mktmap.get(str(mktnum), "0")
    # 确保code是6位，前面补0
    code = code.zfill(6)
    return f"{mkt}.{code}"


# ─── 2. K线数据 ───────────────────────────────────────────────
def fetch_kline(secid, beg="20250101", end="20991231", retries=3):
    """从腾讯财经拉日K线数据（前复权），支持重试"""
    # secid格式: 1.600519(上海) / 0.002567(深圳) / 2.8xxxxx(北京)
    # 转换为腾讯格式: sh600519 / sz002567
    parts = secid.split(".")
    if len(parts) != 2:
        raise ValueError(f"Invalid secid: {secid}")
    mkt, code = parts[0], parts[1].zfill(6)
    if mkt == "1":
        sym = f"sh{code}"
    elif mkt == "0":
        sym = f"sz{code}"
    elif mkt == "2":
        # 北京市场用 bj 前缀
        sym = f"bj{code}"
    else:
        sym = f"sz{code}"

    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?_var=kline_dayhfq&param={sym},day,,,{300},qfq")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://finance.qq.com/",
    }
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read().decode()
                if not raw:
                    raise ValueError("empty response")
                data_str = raw[raw.index('=') + 1:]
                data = json.loads(data_str)
            qfqday = (data.get("data", {})
                        .get(sym, {})
                        .get("qfqday")
                       or data.get("data", {}).get(sym, {}).get("day") or [])
            result = []
            for item in qfqday:
                try:
                    o, c, h, l, v = float(item[1]), float(item[2]), float(item[3]), float(item[4]), float(item[5])
                    amount = v * (o + c) / 2  # 估算成交额
                    result.append({
                        "date":   item[0],
                        "open":   o,
                        "close":  c,
                        "high":   h,
                        "low":    l,
                        "volume": int(v),
                        "amount": amount,
                        "chg":    0.0,
                    })
                except:
                    continue
            if result:
                return result
            raise ValueError(f"K线数据为空: {sym}")
        except Exception as e:
            if attempt < retries - 1:
                import time
                time.sleep(2 ** attempt)
                continue
            raise ValueError(f"获取K线失败：{e}")


# ─── 3. 技术指标计算 ───────────────────────────────────────────
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
        ag, al = sum(gains) / period, sum(losses) / period
        r.append(100 - 100 / (1 + ag / al) if al else 100)
    return r


def macd(closes, fp=12, sp=26, signal_p=9):
    e12 = ema(closes, fp)
    e26 = ema(closes, sp)
    macd_line = [a - b for a, b in zip(e12, e26)]
    sig = ema(macd_line, signal_p)
    hist = [m - s for m, s in zip(macd_line, sig)]
    return macd_line, sig, hist


def bollinger(closes, period=20, k=2):
    mid = ma(closes, period)
    std = [None] * (period - 1)
    for i in range(period - 1, len(closes)):
        vals = closes[i - period + 1:i + 1]
        m = sum(vals) / period
        std.append(math.sqrt(sum((x - m) ** 2 for x in vals) / period))
    upper = [None if v is None else v + k * s for v, s in zip(mid, std)]
    lower = [None if v is None else v - k * s for v, s in zip(mid, std)]
    return upper, mid, lower


def volatility(data, period=20):
    r = [None] * (period - 1)
    for i in range(period - 1, len(data)):
        vals = data[i - period + 1:i + 1]
        m = sum(vals) / period
        r.append(math.sqrt(sum((x - m) ** 2 for x in vals) / period))
    return r


def atr(highs, lows, closes, period=14):
    tr = [0]
    for i in range(1, len(highs)):
        tr.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        ))
    return ma(tr, period)


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


# ─── 4. 格式化输出 ──────────────────────────────────────────────
def format_num(v, fmt=".2f"):
    if v is None: return "N/A"
    return format(v, fmt)


def trend_icon(v, cur):
    if v is None: return "N/A"
    return "↑" if cur > v else "↓"


def signal_str(condition):
    if condition == 1: return "✅ 买入信号"
    if condition == -1: return "🔴 卖出信号"
    return "⚪ 中性"


# ─── 5. 主分析函数 ───────────────────────────────────────────────
def analyze(stock_keyword):
    # 1. 搜索
    print(f"🔍 搜索股票：{stock_keyword}")
    code, name, mktnum = search_stock(stock_keyword)
    secid = get_secid(code, mktnum)
    print(f"📌 找到：{name}（{code}）secid={secid}")

    # 2. 拉数据
    print(f"📡 正在获取K线数据...")
    klines = fetch_kline(secid)
    if len(klines) < 60:
        raise ValueError(f"数据不足，仅{len(klines)}条")
    print(f"   共获取 {len(klines)} 个交易日")

    # 3. 解析
    dates   = [k["date"] for k in klines]
    opens   = [k["open"] for k in klines]
    closes  = [k["close"] for k in klines]
    highs   = [k["high"] for k in klines]
    lows    = [k["low"] for k in klines]
    volumes = [k["volume"] for k in klines]

    n = len(closes)
    lc = closes[-1]
    ld = dates[-1]

    # 4. 计算指标
    ma5   = ma(closes, 5)
    ma10  = ma(closes, 10)
    ma20  = ma(closes, 20)
    ma60  = ma(closes, 60)
    ma120 = ma(closes, 120)
    ma5v, ma10v, ma20v, ma60v, ma120v = ma5[-1], ma10[-1], ma20[-1], ma60[-1], ma120[-1]

    rsi14 = rsi(closes, 14)
    rsi28 = rsi(closes, 28)
    rsi14v, rsi28v = rsi14[-1], rsi28[-1]

    macd_l, signal_line, macd_hist = macd(closes)
    macd_v = macd_hist[-1]
    macd_prev = macd_hist[-2]

    boll_up, boll_mid, boll_low = bollinger(closes)
    boll_up_v, boll_mid_v, boll_low_v = boll_up[-1], boll_mid[-1], boll_low[-1]

    vol20 = volatility(closes, 20)
    vol60 = volatility(closes, 60)
    vol20v, vol60v = vol20[-1], vol60[-1]

    atr_v = atr(highs, lows, closes, 14)[-1]

    vol_ma5 = sum(volumes[-5:]) / 5
    vol_ma20 = sum(volumes[-20:]) / 20
    vol_r = volumes[-1] / vol_ma20

    kdj_k, kdj_d, kdj_j = kdj(highs, lows, closes)
    kdv_k, kdv_d, kdv_j = kdj_k[-1], kdj_d[-1], kdj_j[-1]

    # 历史高低
    h_all = max(highs)
    l_all = min(lows)
    h_all_i = highs.index(h_all)
    l_all_i = lows.index(l_all)
    h_all_d = dates[h_all_i]
    l_all_d = dates[l_all_i]

    hi120 = max(closes[-120:])
    lo120 = min(closes[-120:])
    hi60  = max(closes[-60:])
    lo60  = min(closes[-60:])
    hi20  = max(closes[-20:])
    lo20  = min(closes[-20:])

    # 年初至今
    ytd = (lc - closes[0]) / closes[0] * 100

    # 近期超过MA5天数
    above_ma5 = sum(1 for i in range(max(0, n - 20), n) if closes[i] > ma5[i])

    # 今日涨跌
    chg = closes[-1] - closes[-2]
    chg_pct = chg / closes[-2] * 100

    # 均线交叉状态
    gold_cross = ma5v > ma10v and ma10v > ma20v
    death_cross = ma5v < ma10v and ma10v < ma20v

    # 均线排列
    if ma5v > ma10v > ma20v: ma_trend = "多头排列（强势）"
    elif ma5v < ma10v < ma20v: ma_trend = "空头排列（弱势）"
    elif lc > ma20v: ma_trend = "偏强整理"
    else: ma_trend = "偏弱整理"

    # MACD信号
    macd_signal = 1 if macd_v > 0 else -1 if macd_v < -0.05 else 0

    # RSI信号
    rsi14_signal = 1 if rsi14v > 70 else -1 if rsi14v < 30 else 0

    # KDJ信号
    kdj_signal = 1 if kdv_j > 80 else -1 if kdv_j < 20 else 0
    kdj_gold = kdv_k > kdv_d

    # 布林带位置
    boll_pos = (lc - boll_low_v) / (boll_up_v - boll_low_v) * 100 if boll_up_v != boll_low_v else 50

    # 综合评分（简单打分）
    score = 0
    if lc > ma5v: score += 1
    if ma5v > ma10v: score += 1
    if ma10v > ma20v: score += 1
    if macd_v > 0: score += 1
    if rsi14v > 50: score += 1
    if chg > 0: score += 1
    if score >= 5: overall = "🟢 偏强"
    elif score >= 3: overall = "🟡 中性"
    else: overall = "🔴 偏弱"

    # PE/PB（如果有）
    pe = None
    pb = None

    # ─── 输出报告 ───────────────────────────────────────────────
    print()
    print("=" * 60)
    print(f"  📊 {name}（{code}）A股量化分析报告")
    print(f"  数据区间：{dates[0]} → {ld}  共{n}个交易日")
    print("=" * 60)
    print()
    print("【一、当前价格】")
    print(f"  今日收盘：{lc:.2f}  {'+' if chg>=0 else ''}{chg:.2f}（{'+' if chg_pct>=0 else ''}{chg_pct:.2f}%）")
    print(f"  年初至今：{'+' if ytd>=0 else ''}{ytd:.2f}%  （年初{closes[0]:.2f}）")
    print(f"  关键支撑：{lo20:.2f}（20日低）/ {lo60:.2f}（60日低）")
    print(f"  关键压力：{hi20:.2f}（20日高）/ {hi60:.2f}（60日高）")
    print()
    print("【二、均线系统】")
    print(f"  MA5：  {format_num(ma5v)}  {trend_icon(ma5v, lc)}")
    print(f"  MA10： {format_num(ma10v)}  {trend_icon(ma10v, lc)}")
    print(f"  MA20： {format_num(ma20v)}  {trend_icon(ma20v, lc)}")
    print(f"  MA60： {format_num(ma60v)}  {trend_icon(ma60v, lc)}")
    print(f"  MA120：{format_num(ma120v)}  {trend_icon(ma120v, lc)}")
    print(f"  ─────")
    print(f"  均线态势：{ma_trend}")
    print(f"  金叉/死叉：{'✅ MA5>MA10>MA20 多头' if gold_cross else '🔴 MA5<MA10<MA20 空头' if death_cross else '⚪ 混乱排列'}")
    print(f"  近20日价格在MA5上方天数：{above_ma5}/20")
    print()
    print("【三、RSI 指标】")
    print(f"  RSI(14)：{format_num(rsi14v)}  {'🔴 超买' if rsi14v>=70 else '🔵 超卖' if rsi14v<=30 else '偏强' if rsi14v>=60 else '偏弱' if rsi14v<=40 else '中性'}")
    print(f"  RSI(28)：{format_num(rsi28v)}  {'偏强' if rsi28v>=55 else '偏弱' if rsi28v<=45 else '中性'}")
    print()
    print("【四、MACD 指标】")
    macd_txt = "红柱↑（多头）" if macd_v > 0 else "绿柱↓（空头）"
    macd_chg = "收窄" if abs(macd_v) < abs(macd_prev) else "放大"
    print(f"  MACD柱：{format_num(macd_v)}  {macd_txt}，{macd_chg}")
    print(f"  信号线：{format_num(signal_line[-1])}  DEA：{format_num(signal_line[-1])}")
    print()
    print("【五、KDJ 随机指标】")
    kdj_txt = "超买区" if kdv_j > 80 else "超卖区" if kdv_j < 20 else "中性"
    print(f"  K={format_num(kdv_k)}  D={format_num(kdv_d)}  J={format_num(kdv_j)}  {kdj_txt}")
    print(f"  K>D：{'✅ 是' if kdv_k > kdv_d else '🔴 否'}")
    print()
    print("【六、布林带】")
    print(f"  上轨：{format_num(boll_up_v)}")
    print(f"  中轨：{format_num(boll_mid_v)}")
    print(f"  下轨：{format_num(boll_low_v)}")
    print(f"  当前位置：{boll_pos:.0f}%（0%=碰下轨，100%=碰上轨）")
    if lc > boll_up_v: print("  ⚠️ 价格突破布林上轨，留意回调风险")
    elif lc < boll_low_v: print("  ⚠️ 价格跌破布林下轨，留意反弹机会")
    print()
    print("【七、ATR 真实波幅】")
    print(f"  ATR(14)：{format_num(atr_v)}  {'高波动' if atr_v > lc*0.03 else '正常' if atr_v > lc*0.015 else '低波动'}")
    print()
    print("【八、波动率】")
    print(f"  20日波动率：{format_num(vol20v)}  {'高波动' if vol20v > lc*0.025 else '正常' if vol20v > lc*0.015 else '低波动'}")
    print(f"  60日波动率：{format_num(vol60v)}")
    print()
    print("【九、成交量】")
    vol_txt = "🔴 放量" if vol_r > 1.2 else "🔵 缩量" if vol_r < 0.8 else "⚪ 正常量"
    print(f"  今日量：{volumes[-1]/10000:.1f}万手")
    print(f"  5日均量：{vol_ma5/10000:.1f}万手")
    print(f"  20日均量：{vol_ma20/10000:.1f}万手")
    print(f"  量比：{vol_r:.2f}倍  {vol_txt}")
    print()
    print("【十、历史高低】")
    print(f"  全周期最高：{h_all:.2f}（{h_all_d}）")
    print(f"  全周期最低：{l_all:.2f}（{l_all_d}）")
    print(f"  近120日区间：{lo120:.2f} ~ {hi120:.2f}")
    print(f"  近60日区间：{lo60:.2f} ~ {hi60:.2f}")
    print(f"  近20日区间：{lo20:.2f} ~ {hi20:.2f}")
    print()
    print("【十一、综合研判】")
    print(f"  综合评分：{overall}（{score}/6分）")
    print(f"  短期：{'↑ 均线多头，短线强势' if gold_cross else '↓ 均线空头，短线弱势' if death_cross else '→ 震荡整理'}")
    print(f"  中期：RSI(28)={format_num(rsi28v)} {'强势' if rsi28v>55 else '弱势' if rsi28v<45 else '中性'}")
    print(f"  MACD：{macd_txt}")
    print(f"  量能：{vol_txt}（{vol_r:.2f}倍）")
    print()
    print(f"  ▶ 关键支撑：{lo20:.2f} / {lo60:.2f}")
    print(f"  ▶ 关键压力：{ma5v:.2f} / {ma20v:.2f}")
    print()
    print("⚠️  免责声明：以上为量化模型客观分析，不构成投资建议。")
    print("=" * 60)


# ─── 入口 ─────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：python3 a_stock_analysis.py <股票名称或代码>")
        sys.exit(1)
    keyword = " ".join(sys.argv[1:])
    try:
        analyze(keyword)
    except Exception as e:
        print(f"❌ 分析失败：{e}")
        sys.exit(1)
