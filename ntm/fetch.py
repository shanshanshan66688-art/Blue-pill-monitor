# -*- coding: utf-8 -*-
"""
数据抓取层：全部使用 AKShare 的免费公开接口（数据来自上交所、深交所、东方财富、财联社）。
每个函数只做"抓取 + 统一字段"，不做判断。任何一个数据源失败都不会影响其他数据源。
"""
import socket
import time
import datetime as dt

socket.setdefaulttimeout(30)   # 部分接口没有设置超时，防止一直卡住

import pandas as pd

try:
    import akshare as ak
except ImportError:  # 允许在没装 akshare 的环境里跑 --demo
    ak = None


def _retry(fn, *args, tries=3, wait=2, **kwargs):
    last = None
    for i in range(tries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # 网络抖动、接口偶发返回空
            last = e
            time.sleep(wait * (i + 1))
    raise last


def _to_yi(series):
    """把金额统一成"亿元"。接口单位不统一，按量级自动判断。"""
    s = pd.to_numeric(series, errors="coerce")
    med = s.dropna().abs().median() if s.notna().any() else 0
    if med > 1e9:      # 单位是元
        return s / 1e8
    if med > 1e5:      # 单位是万元
        return s / 1e4
    return s           # 已经是亿元


def _d(x):
    return pd.to_datetime(x).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- 交易日历
def index_daily(symbol, start, end):
    """指数日线。返回 date, symbol, close, amount(亿元)"""
    df = _retry(ak.stock_zh_index_daily_em, symbol=symbol,
                start_date=start.replace("-", ""), end_date=end.replace("-", ""))
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "symbol", "close", "amount"])
    out = pd.DataFrame({
        "date": pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d"),
        "symbol": symbol,
        "close": pd.to_numeric(df["close"], errors="coerce"),
        "amount": _to_yi(df["amount"]),
    })
    return out


# ---------------------------------------------------------------- ETF 份额
def sse_shares(date, codes):
    """上交所ETF份额，可以查历史任意一天。date: YYYY-MM-DD"""
    df = _retry(ak.fund_etf_scale_sse, date=date.replace("-", ""))
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "code", "shares", "source", "name"])
    df["基金代码"] = df["基金代码"].astype(str).str.zfill(6)
    df = df[df["基金代码"].isin(codes)]
    return pd.DataFrame({
        "date": [_d(x) for x in df["统计日期"]],
        "code": df["基金代码"].values,
        "shares": df["基金份额"].values,
        "source": "sse",
        "name": df["基金简称"].values,
    })


def szse_shares(as_of, codes):
    """深交所ETF份额，只能拿到最新值，所以必须每天跑才能积累历史。"""
    df = _retry(ak.fund_etf_scale_szse)
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "code", "shares", "source", "name"])
    df["基金代码"] = df["基金代码"].astype(str).str.zfill(6)
    df = df[df["基金代码"].isin(codes)]
    return pd.DataFrame({
        "date": as_of,
        "code": df["基金代码"].values,
        "shares": df["基金份额"].values,
        "source": "szse",
        "name": df["基金简称"].values,
    })


def em_spot_shares(as_of, codes):
    """东方财富ETF实时行情里的"最新份额"，作为交易所数据缺失时的备用。"""
    df = _retry(ak.fund_etf_spot_em)
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "code", "shares", "source", "name"])
    df["代码"] = df["代码"].astype(str).str.zfill(6)
    df = df[df["代码"].isin(codes)]
    return pd.DataFrame({
        "date": as_of,
        "code": df["代码"].values,
        "shares": pd.to_numeric(df["最新份额"], errors="coerce").values,
        "source": "em_spot",
        "name": df["名称"].values,
    })


# ---------------------------------------------------------------- ETF 行情
def etf_prices(code, start, end):
    df = _retry(ak.fund_etf_hist_em, symbol=code, period="daily",
                start_date=start.replace("-", ""), end_date=end.replace("-", ""), adjust="")
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "code", "close", "amount", "pct"])
    return pd.DataFrame({
        "date": pd.to_datetime(df["日期"]).dt.strftime("%Y-%m-%d"),
        "code": code,
        "close": pd.to_numeric(df["收盘"], errors="coerce"),
        "amount": _to_yi(df["成交额"]),
        "pct": pd.to_numeric(df["涨跌幅"], errors="coerce"),
    })


# ---------------------------------------------------------------- 两融
def margin_balance():
    """全市场融资余额（东方财富两融账户统计，约近两年日度数据）。"""
    df = _retry(ak.stock_margin_account_info)
    return pd.DataFrame({
        "date": pd.to_datetime(df["日期"]).dt.strftime("%Y-%m-%d"),
        "fin_balance_yi": _to_yi(df["融资余额"]),
    }).dropna()


# ---------------------------------------------------------------- 事件
def notices(date, keywords, action_words):
    """当日沪深京A股公告，按关键词过滤出与国家队相关的。"""
    df = _retry(ak.stock_notice_report, symbol="全部", date=date.replace("-", ""))
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "source", "code", "name", "title", "url"])
    t = df["公告标题"].astype(str)
    hit_kw = t.apply(lambda s: any(k in s for k in keywords))
    hit_act = t.apply(lambda s: any(k in s for k in action_words))
    df = df[hit_kw & hit_act]
    return pd.DataFrame({
        "date": date,
        "source": "公告",
        "code": df["代码"].astype(str).values,
        "name": df["名称"].values,
        "title": df["公告标题"].values,
        "url": df["网址"].values if "网址" in df.columns else "",
    })


def cls_news(keywords):
    """财联社电报（最近若干条），过滤国家队相关表态。"""
    df = _retry(ak.stock_info_global_cls, symbol="全部")
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "source", "code", "name", "title", "url"])
    text = (df["标题"].astype(str) + " " + df["内容"].astype(str))
    df = df[text.apply(lambda s: any(k in s for k in keywords))]
    titles = [(a if a and a != "nan" else b[:80]) for a, b in
              zip(df["标题"].astype(str), df["内容"].astype(str))]
    return pd.DataFrame({
        "date": [_d(x) for x in df["发布日期"]],
        "source": "财联社",
        "code": "",
        "name": "",
        "title": titles,
        "url": "",
    })


# ---------------------------------------------------------------- 季报
def top10_free_holders(stock, report_date, keywords):
    """个股十大流通股东，筛出国家队席位。stock 形如 SH601398，report_date 形如 2026-09-30"""
    df = _retry(ak.stock_gdfx_free_top_10_em, symbol=stock.lower(),
                date=report_date.replace("-", ""))
    if df is None or df.empty:
        return pd.DataFrame()
    df = df[df["股东名称"].astype(str).apply(lambda s: any(k in s for k in keywords))]
    return pd.DataFrame({
        "report_date": report_date,
        "stock": stock,
        "holder": df["股东名称"].values,
        "holder_type": df["股东性质"].values,
        "shares": pd.to_numeric(df["持股数"], errors="coerce").values,
        "ratio": pd.to_numeric(df["占总流通股本持股比例"], errors="coerce").values,
        "change": df["增减"].astype(str).values,
    })


def today_str():
    return dt.date.today().strftime("%Y-%m-%d")
