# -*- coding: utf-8 -*-
"""
模拟数据：不联网也能看到系统效果，同时用来测试计算逻辑。
构造了三段场景：一次"恐慌托底"（大跌 + 多只宽基ETF同步大额申购）、
一次"过热降温"（成交额与融资余额处于高位 + 多只ETF同步大额赎回）、
以及一次份额拆分（应被识别并剔除，而不是误报为申购）。
"""
import numpy as np
import pandas as pd

import config as C

from . import db


def populate(con, n=260, seed=7):
    rng = np.random.default_rng(seed)
    for t in ["etf_shares", "etf_price", "index_daily", "margin", "events", "holders"]:
        con.execute(f"DELETE FROM {t}")

    dates = pd.bdate_range(end=pd.Timestamp("2026-10-23"), periods=n).strftime("%Y-%m-%d").tolist()
    panic = range(70, 76)      # 恐慌托底
    hot = range(180, 186)      # 过热降温
    split_day = 120            # 份额拆分

    # ---- 指数：沪深300
    ret = rng.normal(0.0004, 0.009, n)
    ret[64:72] = [-0.025, -0.03, -0.018, -0.035, -0.02, -0.028, -0.01, 0.006]
    ret[72:78] = [0.03, 0.022, 0.015, 0.01, 0.008, 0.012]
    ret[160:180] += 0.006
    bench = 3800 * np.cumprod(1 + ret)
    small = 6000 * np.cumprod(1 + ret * 1.3 + rng.normal(0, 0.004, n) - np.where(np.isin(np.arange(n), list(panic)), 0.012, 0))
    turnover = 12000 + rng.normal(0, 900, n)
    turnover[150:190] += np.linspace(2000, 14000, 40)
    turnover[64:72] += 6000
    margin = 18000 + np.cumsum(rng.normal(3, 25, n))
    margin[140:186] += np.linspace(0, 2600, 46)

    idx_rows = []
    for i, d in enumerate(dates):
        idx_rows.append((d, C.BENCHMARK_INDEX, bench[i], turnover[i] * 0.4))
        idx_rows.append((d, "csi000985", bench[i] * 1.4, turnover[i]))
        idx_rows.append((d, "sh000852", small[i], turnover[i] * 0.2))
        idx_rows.append((d, "sz399006", small[i] * 0.35, turnover[i] * 0.15))
    db.upsert(con, "index_daily", pd.DataFrame(idx_rows, columns=["date", "symbol", "close", "amount"]))
    db.upsert(con, "margin", pd.DataFrame({"date": dates, "fin_balance_yi": margin}))

    # ---- ETF：份额与价格
    share_rows, price_rows = [], []
    for k, (code, name, index, _kw, base, _bd) in enumerate(C.CORE_ETFS):
        px0 = {"沪深300": 4.2, "上证50": 3.1, "中证500": 6.5, "中证1000": 2.6, "创业板指": 2.3, "科创50": 1.1}[index]
        price = px0 * bench / bench[0] * (1 + rng.normal(0, 0.002, n))
        sh = (base or 120) * 1e8 * 1.25
        for i, d in enumerate(dates):
            flow_shares = rng.normal(0, sh * 0.004)
            if i in panic and k < 10:
                flow_shares += sh * rng.uniform(0.03, 0.06)
            if i in hot and k < 10:
                flow_shares -= sh * rng.uniform(0.05, 0.09)
            sh = max(sh + flow_shares, 1e8)
            p = price[i]
            if code == "512100" and i >= split_day:
                if i == split_day:
                    sh *= 2
                p = p / 2
            share_rows.append((d, code, sh, "demo"))
            price_rows.append((d, code, p, None, None))
    db.upsert(con, "etf_shares", pd.DataFrame(share_rows, columns=["date", "code", "shares", "source"]))
    db.upsert(con, "etf_price", pd.DataFrame(price_rows, columns=["date", "code", "close", "amount", "pct"]))

    ev = [
        (dates[71], "公告", "601398", "工商银行", "关于控股股东中央汇金增持本行A股股份的公告（示例）", ""),
        (dates[72], "财联社", "", "", "中央汇金：已再次增持ETF，未来将继续增持（示例）", ""),
        (dates[181], "财联社", "", "", "监管层上调融资保证金比例（示例）", ""),
    ]
    db.upsert(con, "events", pd.DataFrame(ev, columns=["date", "source", "code", "name", "title", "url"]))
    hd = [("2026-06-30", "SH601398", "中央汇金投资有限责任公司", "其它", 1.24e11, 34.79, "不变"),
          ("2026-06-30", "SH601398", "中国证券金融股份有限公司", "其它", 8.6e9, 2.41, "不变")]
    db.upsert(con, "holders", pd.DataFrame(hd, columns=["report_date", "stock", "holder", "holder_type", "shares", "ratio", "change"]))
