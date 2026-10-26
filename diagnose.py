# -*- coding: utf-8 -*-
"""逐个测试数据源是否可用，每个最多等30秒。用法：python diagnose.py"""
import datetime as dt
import socket
import time
import traceback

socket.setdefaulttimeout(30)
import akshare as ak  # noqa: E402

today = dt.date.today()
d = (today - dt.timedelta(days=3)).strftime("%Y%m%d")
start = (today - dt.timedelta(days=30)).strftime("%Y%m%d")
end = today.strftime("%Y%m%d")

TESTS = [
    ("指数日线(东财)", lambda: ak.stock_zh_index_daily_em(symbol="sh000300", start_date=start, end_date=end)),
    ("中证指数日线", lambda: ak.stock_zh_index_hist_csindex(symbol="000985", start_date=start, end_date=end)),
    ("新浪指数日线", lambda: ak.stock_zh_index_daily(symbol="sz399006")),
    ("新浪ETF日线", lambda: ak.fund_etf_hist_sina(symbol="sh510300")),
    ("新浪ETF日线(深)", lambda: ak.fund_etf_hist_sina(symbol="sz159919")),
    ("上交所ETF份额", lambda: ak.fund_etf_scale_sse(date=d)),
    ("深交所ETF份额", lambda: ak.fund_etf_scale_szse()),
    ("东财ETF实时", lambda: ak.fund_etf_spot_em()),
    ("东财ETF日线", lambda: ak.fund_etf_hist_em(symbol="510300", period="daily", start_date=start, end_date=end, adjust="")),
    ("东财两融", lambda: ak.stock_margin_account_info()),
    ("东财公告", lambda: ak.stock_notice_report(symbol="全部", date=d)),
    ("财联社快讯", lambda: ak.stock_info_global_cls(symbol="全部")),
    ("东财十大流通股东", lambda: ak.stock_gdfx_free_top_10_em(symbol="sh601398", date="20260630")),
]

print("akshare", ak.__version__)
for name, fn in TESTS:
    t = time.time()
    try:
        df = fn()
        n = 0 if df is None else len(df)
        cols = list(df.columns)[:6] if n else []
        print(f"[OK]   {name}: {n} 行, {time.time() - t:.1f}s, 列 {cols}")
    except Exception as e:
        print(f"[FAIL] {name}: {type(e).__name__}: {str(e)[:200]} ({time.time() - t:.1f}s)")
        traceback.print_exc(limit=2)
