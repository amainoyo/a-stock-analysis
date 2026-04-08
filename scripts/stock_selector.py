#!/usr/bin/env python3
"""
A 股选股器 - 5条件评分模型 v3（bug修复版）

修复内容(v3):
  1. MACD绿柱判断增加DIF<0检查，避免红柱收缩误判
  2. KDJ低位拐头要求前一根J值<20，严格拐头确认
  3. MA20使用真实20日均线，非估算值
  4. 数据源从Sina更换为腾讯财经API（稳定无456限流）

条件：
  1. RSI(14) 处于 30-45 区间（超卖反弹区）
  2. MACD 绿柱连续收缩 ≥ 3天，且 DIF<0（空方动能衰竭）
  3. 近3日均量 > 20日均量 × 1.3（放量确认）
  4. KDJ 的 J 值从 < 20 位置转头向上（辅助确认）
  5. 收盘价 > MA20（趋势不破位）

用法:
  python3 stock_selector.py [output.json]
"""

import json
import sys
import ssl
import time
import math
import re
import urllib.request
from typing import Dict, List, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================================
# 工具函数
# ============================================================================

def calc_ema(data: List[float], period: int) -> float:
    """计算EMA"""
    if not data or period <= 0:
        return 0.0
    k = 2.0 / (period + 1)
    ema = data[0]
    for price in data[1:]:
        ema = price * k + ema * (1 - k)
    return ema

def calc_ma(data: List[float], period: int) -> float:
    """计算MA"""
    if len(data) < period:
        return sum(data) / len(data) if data else 0.0
    return sum(data[-period:]) / period

def calc_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """计算RSI"""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d for d in deltas[-period:] if d > 0]
    losses = [-d for d in deltas[-period:] if d < 0]
    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.0
    if avg_loss == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))

def calc_kdj(highs: List[float], lows: List[float], closes: List[float],
              n: int = 9) -> Tuple[float, float, float]:
    """计算KDJ，返回(K, D, J)"""
    if len(closes) < n:
        return 50.0, 50.0, 50.0
    k = 50.0
    d = 50.0
    m = 2.0 / (n + 1)
    for i in range(n - 1, len(closes)):
        low_min = min(lows[i-n+1:i+1])
        high_max = max(highs[i-n+1:i+1])
        if high_max == low_min:
            rsv = 50.0
        else:
            rsv = (closes[i] - low_min) / (high_max - low_min) * 100
        k = k * (1 - m) + rsv * m
        d = d * (1 - m) + k * m
    j = 3 * k - 2 * d
    return k, d, j

def get_macd_bar(closes: List[float], bar_idx: int) -> Tuple[float, float]:
    """
    计算指定索引位置的MACD柱值（DIF和DEA用EMA递推计算）
    返回 (dif, macd_bar)
    """
    if len(closes) <= bar_idx or bar_idx < 26:
        return 0.0, 0.0
    # 用前bar_idx个数据计算
    data = closes[:bar_idx+1]
    e12 = calc_ema(data, 12)
    e26 = calc_ema(data, 26)
    dif = e12 - e26
    # signal用dif序列的EMA(9)
    dif_series = []
    for j in range(26, len(data)):
        e12j = calc_ema(data[:j], 12)
        e26j = calc_ema(data[:j], 26)
        dif_series.append(e12j - e26j)
    if len(dif_series) < 9:
        signal = dif_series[-1] if dif_series else dif
    else:
        signal = calc_ema(dif_series, 9)
    macd_bar = (dif - signal) * 2
    return dif, macd_bar

def get_macd_bar_fast(closes: List[float]) -> List[float]:
    """
    快速计算所有MACD柱（优化版，跳过26日前的初始数据）
    返回从索引26开始的bar列表
    """
    bars = []
    dif_list = []
    for j in range(26, len(closes)):
        e12 = calc_ema(closes[:j], 12)
        e26 = calc_ema(closes[:j], 26)
        dif_list.append(e12 - e26)
    if len(dif_list) < 9:
        for d in dif_list:
            bars.append(d * 2)
        return bars
    # 计算signal序列
    for idx in range(8, len(dif_list)):
        sig = calc_ema(dif_list[idx-8:idx+1], 9)
        bars.append((dif_list[idx] - sig) * 2)
    return bars

def check_macd_green_shrink(closes: List[float]) -> Tuple[int, float]:
    """
    检查MACD绿柱是否连续收缩>=3天，且DIF<0
    返回 (收缩天数, 当前DIF值)
    """
    if len(closes) < 35:
        return 0, 0.0
    bars = get_macd_bar_fast(closes)
    if len(bars) < 4:
        return 0, 0.0
    dif_list = []
    for j in range(26, len(closes)):
        e12 = calc_ema(closes[:j], 12)
        e26 = calc_ema(closes[:j], 26)
        dif_list.append(e12 - e26)
    current_dif = dif_list[-1] if dif_list else 0.0
    # 从最近往前数连续绿柱收缩
    shrink_days = 0
    for i in range(-1, -min(6, len(bars)), -1):
        idx = len(bars) + i
        if bars[idx] < 0 and (i == -1 or bars[idx] > bars[idx+1]):
            shrink_days += 1
        else:
            break
    return shrink_days, current_dif

# ============================================================================
# 数据获取
# ============================================================================

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def fetch_kline_tx(sym: str, datalen: int = 80) -> Tuple[str, Optional[List[Dict]]]:
    """
    腾讯财经前复权日K线
    sym格式: sh600519 或 sz002567
    返回 (sym, klines)
    """
    market = 'sh' if sym.startswith('sh') else 'sz'
    code = sym[2:]
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?_var=kline_dayhfq&param={market}{code},day,,,{datalen},qfq")
    try:
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.qq.com/'}
        )
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            raw = resp.read().decode()
            data_str = raw[raw.index('=') + 1:]
            data = json.loads(data_str)
        qfqday = (data.get('data', {})
                   .get(market + code, {})
                   .get('qfqday') or data.get('data', {}).get(market + code, {}).get('day') or [])
        klines = []
        for item in qfqday:
            try:
                klines.append({
                    'date':   item[0],
                    'open':   float(item[1]),
                    'close':  float(item[2]),
                    'high':   float(item[3]),
                    'low':    float(item[4]),
                    'volume': float(item[5]),
                })
            except:
                continue
        return sym, klines if len(klines) >= 30 else None
    except:
        return sym, None

def fetch_quotes_sina(symbols: List[str]) -> Dict[str, Dict]:
    """批量获取实时报价（Sina批量接口）"""
    if not symbols:
        return {}
    results = {}
    batch_size = 50
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i+batch_size]
        url = f'https://hq.sinajs.cn/list={",".join(batch)}'
        try:
            req = urllib.request.Request(
                url,
                headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'}
            )
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                raw = resp.read().decode('gbk', errors='replace')
            for line in raw.strip().split('\n'):
                if '=' not in line:
                    continue
                parts = line.strip().split('=')
                if len(parts) < 2:
                    continue
                sym = parts[0].split('_')[-1].strip()
                try:
                    vals = parts[1].strip().strip('"; \n').split(',')
                    if len(vals) < 10:
                        continue
                    curr = float(vals[3]) if vals[3] != '' else 0
                    prev = float(vals[2]) if vals[2] != '' else 0
                    results[sym] = {
                        'name': vals[0],
                        'curr': curr if curr > 0 else prev,
                        'prev': prev,
                        'high': float(vals[4]) if vals[4] != '' else 0,
                        'low':  float(vals[5]) if vals[5] != '' else 0,
                        'vol':  float(vals[8]) if vals[8] != '' else 0,
                    }
                except:
                    continue
        except:
            pass
        time.sleep(0.05)
    return results

# ============================================================================
# 选股分析
# ============================================================================

def analyze_stock(sym: str, klines: List[Dict], quote: Dict) -> Optional[Dict[str, Any]]:
    """分析单只股票，5条件评分"""
    if not klines or len(klines) < 30:
        return None
    closes = [k['close'] for k in klines]
    highs  = [k['high']  for k in klines]
    lows   = [k['low']   for k in klines]
    vols   = [k['volume'] for k in klines]

    curr = quote.get('curr', closes[-1])
    prev = quote.get('prev', closes[-2]) if len(closes) >= 2 else closes[-1]
    change = (curr - prev) / prev * 100 if prev > 0 else 0.0

    score = 0
    reasons = []
    details = {}

    # === 条件1: RSI(14) 处于 30-45 区间 ===
    rsi14 = calc_rsi(closes, 14)
    if rsi14 and 30 <= rsi14 <= 45:
        score += 1
        reasons.append(f"RSI14={rsi14:.1f}")
    details['rsi14'] = rsi14

    # === 条件2: MACD绿柱连续收缩>=3天 + DIF<0 ===
    green_shrink, dif = check_macd_green_shrink(closes)
    details['macd_dif'] = dif
    details['macd_green_shrink'] = green_shrink
    if green_shrink >= 3 and dif < 0:
        score += 1
        reasons.append(f"MACD绿柱收缩{green_shrink}天")

    # === 条件3: 近3日均量 > 20日均量 * 1.3 ===
    vol3  = sum(vols[-3:]) / 3
    vol20 = sum(vols[-20:]) / 20
    vol_ratio = vol3 / vol20 if vol20 > 0 else 0
    details['vol_ratio'] = vol_ratio
    if vol20 > 0 and vol3 > vol20 * 1.3:
        score += 1
        reasons.append(f"量能放大{vol_ratio:.2f}x")

    # === 条件4: KDJ的J值从<20位置转头向上 ===
    K, D, J = calc_kdj(highs, lows, closes)
    details['kdj_j'] = J
    kdj_turnup = False
    if len(closes) >= 25:
        for t in range(max(9, len(closes)-8), len(closes)-1):
            k_prev, d_prev, j_prev = calc_kdj(highs[:t], lows[:t], closes[:t])
            k_curr, d_curr, j_curr = calc_kdj(highs[:t+1], lows[:t+1], closes[:t+1])
            if j_prev < 20 and j_curr > j_prev:
                kdj_turnup = True
                break
    if kdj_turnup:
        score += 1
        reasons.append(f"KDJ低位拐头(J={J:.1f})")

    # === 条件5: 收盘价 > MA20 ===
    ma20 = calc_ma(closes, 20)
    details['ma20'] = ma20
    if closes[-1] > ma20:
        score += 1
        reasons.append(f"MA20支撑({ma20:.2f})")

    ma5  = calc_ma(closes, 5)
    ma10 = calc_ma(closes, 10)
    ma60 = calc_ma(closes, 60) if len(closes) >= 60 else None

    return {
        'name':    quote.get('name', sym),
        'code':    sym[2:],
        'sym':     sym,
        'score':   score,
        'reasons': reasons,
        'price':   curr,
        'change':  round(change, 2),
        'details': details,
        'ma5':     round(ma5, 2),
        'ma10':    round(ma10, 2),
        'ma20':    round(ma20, 2),
        'ma60':    round(ma60, 2) if ma60 else None,
    }

# ============================================================================
# 主流程
# ============================================================================

def main():
    output_file = sys.argv[1] if len(sys.argv) > 1 else None

    # 读取股票池
    import os
    skill_dir = os.path.dirname(os.path.abspath(__file__))
    hs300_path = os.path.join(skill_dir, 'fetch_hs300.py')
    with open(hs300_path) as f:
        content = f.read()
    codes = re.findall(r'"(sh\d{6}|sz\d{6})"', content)
    print(f"股票池: {len(codes)} 只")

    # 批量获取报价
    print("获取实时报价...")
    quotes = fetch_quotes_sina(codes)
    print(f"报价: {len(quotes)} 只")

    # 并发获取K线
    print("并发获取K线...")
    kline_data = {}
    failed = 0
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(fetch_kline_tx, c): c for c in codes}
        done = 0
        for future in as_completed(futures):
            sym, data = future.result()
            if data and len(data) >= 30:
                kline_data[sym] = data
            else:
                failed += 1
            done += 1
            if done % 300 == 0:
                print(f"  {done}/{len(codes)}", flush=True)
    print(f"K线获取完成: {len(kline_data)} 只, 失败 {failed} 只")

    # 分析筛选
    results = []
    for sym, klines in kline_data.items():
        q = quotes.get(sym, {})
        r = analyze_stock(sym, klines, q)
        if r and r['score'] >= 2:
            results.append(r)

    results.sort(key=lambda x: x['score'], reverse=True)
    print(f"\n{'='*65}")
    print(f"  A股智能选股器 v3 修复版")
    print(f"  筛选条件: RSI反弹 + MACD绿柱收缩 + 量能放大 + KDJ拐头 + MA20支撑")
    print(f"  共扫描 {len(kline_data)} 只，符合条件 {len(results)} 只（评分≥2）")
    print(f"{'='*65}")

    if not results:
        print("\n⚠️  未找到符合条件标的（当前市场偏弱或偏强）")
        return

    print(f"\n{'排名':<4} {'股票':<10} {'代码':<8} {'评分':<5} {'信号'}")
    print("-" * 65)
    for i, r in enumerate(results[:20], 1):
        signals_str = " / ".join(r['reasons'])
        print(f"#{i:<3} {r['name']:<8} {r['code']:<8} {r['score']}/5   {signals_str}")
        print(f"     现价:{r['price']}  涨跌:{r['change']:+.2f}%  |  "
              f"RSI14:{r['details'].get('rsi14') or 'N/A'}  "
              f"MACD绿柱:{r['details'].get('macd_green_shrink') or 0}天  "
              f"量比:{r['details'].get('vol_ratio') or 0:.2f}x  "
              f"KDJ-J:{r['details'].get('kdj_j') or 0:.1f}")

    print(f"\n{'='*65}")
    print("  Top5 详细数据")
    print(f"{'='*65}")
    for i, r in enumerate(results[:5], 1):
        d = r['details']
        print(f"\n#{i} {r['name']}（{r['code']}）评分:{r['score']}/5")
        print(f"   现价: {r['price']}  涨跌: {r['change']:+.2f}%")
        print(f"   RSI(14): {d.get('rsi14', 'N/A')} | MACD-DIF: {d.get('macd_dif', 0):.4f} | KDJ-J: {d.get('kdj_j', 0):.1f}")
        print(f"   MA5: {r['ma5']}  MA10: {r['ma10']}  MA20: {r['ma20']}  MA60: {r['ma60']}")
        print(f"   量比: {d.get('vol_ratio', 0):.2f}x | MACD绿柱收缩: {d.get('macd_green_shrink', 0)}天")
        print(f"   满足条件: {' + '.join(r['reasons'])}")

    print("\n⚠️  仅供参考，不构成投资建议。🦐")
    print(f"{'='*65}")

    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n结果已保存: {output_file}")

if __name__ == '__main__':
    main()
