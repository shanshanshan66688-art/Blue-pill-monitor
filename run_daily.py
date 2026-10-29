# -*- coding: utf-8 -*-
"""
国家队观察系统 —— 主程序

常用命令
  python run_daily.py                      每日更新 + 生成报告（建议收盘后 18:00 以后运行）
  python run_daily.py --backfill 250       首次使用：回补上交所ETF份额、行情等约250个交易日的历史
  python run_daily.py --quarterly 2026-09-30   季报出来后：抓重点个股前十大流通股东里的国家队席位
  python run_daily.py --report-only        不联网，只用库里已有数据重新出报告
  python run_daily.py --demo               不联网，用模拟数据生成一份示例报告（看效果用）
"""
import argparse
import datetime as dt
import os
import sys
import time
import traceback

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

import config as C  # noqa: E402
from ntm import db, signals, report  # noqa: E402

CODES = [c[0] for c in C.CORE_ETFS]
SSE_CODES = [c for c in CODES if c.startswith("5")]
SZSE_CODES = [c for c in CODES if c.startswith("1")]
KEYWORD = {c[0]: c[3] for c in C.CORE_ETFS}


class Status(dict):
    def run(self, con, run_at, step, fn, *a, **k):
        try:
            res = fn(*a, **k)
            msg = res if isinstance(res, str) else ""
            self[step] = (True, msg)
            db.log(con, run_at, step, "ok", msg)
            return res
        except Exception as e:
            self[step] = (False, f"{type(e).__name__}: {e}")
            db.log(con, run_at, step, "fail", traceback.format_exc())
            print(f"  [失败] {step}: {e}")
            return None


# ============================================================ 抓取步骤
def step_index(con, start, end):
    from ntm import fetch
    syms = [C.BENCHMARK_INDEX] + C.TURNOVER_INDEXES + list(C.EXTRA_INDEXES)
    n, msgs = 0, []
    for s in dict.fromkeys(syms):
        try:
            df = fetch.index_daily(s, start, end)
            n += db.upsert(con, "index_daily", df)
            msgs.append(f"{s}←{df.attrs.get('source', '?')}")
        except Exception as e:
            msgs.append(f"{s} 失败")
            print(f"  [失败] 指数 {s}: {e}")
        time.sleep(0.3)
    if n == 0:
        raise RuntimeError("所有指数都没拿到：" + "，".join(msgs))
    return f"{n} 行（{'，'.join(msgs)}）"


def trading_days(con, start, end):
    t = db.read(con, "SELECT DISTINCT date FROM index_daily WHERE symbol=? AND date BETWEEN ? AND ? ORDER BY date",
                (C.BENCHMARK_INDEX, start, end))
    return list(t["date"])


def _check_names(df):
    bad = []
    for code, name in zip(df.get("code", []), df.get("name", [])):
        if KEYWORD.get(code) and KEYWORD[code] not in str(name):
            bad.append(f"{code}={name}")
    if bad:
        print("  [注意] 以下代码与预期产品不符，请检查 config.py：", ", ".join(bad))
    return bad


def step_sse(con, days):
    from ntm import fetch
    cnt = db.read(con, "SELECT date, COUNT(*) n FROM etf_shares WHERE source='sse' GROUP BY date")
    full = cnt["n"].max() if len(cnt) else 0
    have = set(cnt.loc[cnt["n"] >= full, "date"])   # 只跳过已抓全的日期
    todo = [d for d in days if d not in have]
    n, empty = 0, 0
    for i, d in enumerate(todo):
        df = fetch.sse_shares(d, SSE_CODES)
        if df.empty:
            empty += 1
        else:
            if i == len(todo) - 1:
                _check_names(df)
            n += db.upsert(con, "etf_shares", df.drop(columns=["name"]))
        if len(todo) > 5 and i % 20 == 0:
            print(f"    上交所份额 {i + 1}/{len(todo)} …")
        time.sleep(0.4)
    return f"新增 {n} 行，{empty} 天无数据（多为当天尚未发布）"


def step_szse(con, as_of):
    from ntm import fetch
    df = fetch.szse_shares(as_of, SZSE_CODES)
    _check_names(df)
    return f"{db.upsert(con, 'etf_shares', df.drop(columns=['name']))} 行（日期记为 {as_of}）"


def step_em_backup(con, as_of):
    """交易所没拿到的代码，用东方财富的最新份额补上"""
    from ntm import fetch
    have = set(db.read(con, "SELECT code FROM etf_shares WHERE date=?", (as_of,))["code"])
    missing = [c for c in CODES if c not in have]
    if not missing:
        return "无需补充"
    df = fetch.em_spot_shares(as_of, missing)
    return f"补充 {db.upsert(con, 'etf_shares', df.drop(columns=['name']))} 只：{','.join(df['code'])}"


def step_prices(con, start, end):
    from ntm import fetch
    n, bad = 0, []
    for c in CODES:
        try:
            n += db.upsert(con, "etf_price", fetch.etf_prices(c, start, end))
        except Exception as e:
            bad.append(c)
            print(f"  [失败] ETF行情 {c}: {e}")
        time.sleep(0.3)
    if n == 0:
        raise RuntimeError("所有ETF行情都没拿到")
    return f"{n} 行" + (f"，失败：{','.join(bad)}" if bad else "")


def step_margin(con):
    from ntm import fetch
    return f"{db.upsert(con, 'margin', fetch.margin_balance())} 行"


def step_events(con, days):
    from ntm import fetch
    n = 0
    for d in days:
        n += db.upsert(con, "events", fetch.notices(d, C.EVENT_KEYWORDS, C.NOTICE_ACTION_WORDS))
        time.sleep(0.5)
    try:
        n += db.upsert(con, "events", fetch.cls_news(C.EVENT_KEYWORDS))
    except Exception as e:
        print("  [提示] 财联社快讯获取失败：", e)
    return f"命中 {n} 条"


def step_disclosures(con):
    from ntm import disclosures
    return disclosures.update(con, CODES)


def step_quarterly(con, report_date):
    from ntm import fetch
    n, pending = 0, []
    for s in C.QUARTERLY_STOCKS:
        try:
            n += db.upsert(con, "holders", fetch.top10_free_holders(s, report_date, C.NATIONAL_TEAM_HOLDER_KEYWORDS))
        except Exception:
            pending.append(s)   # 多半是该公司这一期还没披露
        time.sleep(0.5)
    return f"{n} 条国家队席位" + (f"；尚未披露：{','.join(pending)}" if pending else "")


def recent_quarter_ends(n=2):
    """最近 n 个已结束的季度末日期（未披露的会返回空，下次运行再补）"""
    t = dt.date.today()
    q = [dt.date(t.year, m, d) for m, d in ((3, 31), (6, 30), (9, 30), (12, 31))]
    q = [x for x in q if x < t] + [dt.date(t.year - 1, 12, 31), dt.date(t.year - 1, 9, 30)]
    return [x.strftime("%Y-%m-%d") for x in sorted(set(q), reverse=True)[:n]]


# ============================================================ 分析与报告
def analyze_and_report(con, status, out_dir=C.REPORT_DIR, suffix="", csv_dir="data"):
    shares = db.read(con, "SELECT date, code, shares FROM etf_shares")
    prices = db.read(con, "SELECT date, code, close FROM etf_price")
    idx = db.read(con, "SELECT * FROM index_daily")
    mg = db.read(con, "SELECT * FROM margin")
    ev = db.read(con, "SELECT * FROM events")
    hd = db.read(con, "SELECT * FROM holders")
    disc = db.read(con, "SELECT * FROM disclosures")
    if shares.empty:
        raise RuntimeError("库里还没有ETF份额数据。首次使用请先运行：python run_daily.py --backfill 250")

    fl = signals.compute_flows(shares, prices, CODES)
    pool = signals.pool_daily(fl)
    env = signals.environment(idx, mg) if len(idx) else pd.DataFrame(index=pool.index)
    daily = signals.classify(pool, env)
    cap = signals.holding_cap(fl, disc)

    os.makedirs(csv_dir, exist_ok=True)
    daily.to_csv(os.path.join(csv_dir, "signals.csv"), encoding="utf-8-sig")
    fl["flow_yi"].to_csv(os.path.join(csv_dir, "etf_flows_yi.csv"), encoding="utf-8-sig")
    if len(cap):
        cap.to_csv(os.path.join(csv_dir, "holding_cap.csv"), encoding="utf-8-sig", index=False)

    ok = daily.dropna(subset=["pool_flow_yi"])
    last = ok[ok["n_etf"] >= 0.5 * ok["n_etf"].max()].index[-1]
    path = report.build(daily, fl, cap, ev, hd, status, os.path.join(out_dir, f"{last}.html"), title_suffix=suffix)
    report.build(daily, fl, cap, ev, hd, status, os.path.join(out_dir, "latest.html"), title_suffix=suffix)
    r = daily.loc[last]
    print(f"\n== {last}  {r['label']}  核心池净申赎 {r['pool_flow_yi']:+.1f} 亿元  环境：{r.get('regime', '—')}")
    if r.get("note"):
        print("   ", r["note"])
    print("报告：", os.path.abspath(path))
    return path


# ============================================================ 主流程
def main():
    ap = argparse.ArgumentParser(description="国家队观察系统")
    ap.add_argument("--backfill", type=int, default=0, help="回补最近N个交易日")
    ap.add_argument("--quarterly", type=str, help="报告期末日，如 2026-09-30")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--event-days", type=int, default=3, help="扫描最近几个交易日的公告")
    a = ap.parse_args()

    if a.demo:
        from ntm import demo
        with db.connect("data/demo.db") as con:
            demo.populate(con)
            analyze_and_report(con, {"模拟数据": (True, "本报告全部为模拟数据，仅用于展示系统效果")},
                               out_dir=os.path.join(C.REPORT_DIR, "demo"), suffix="（模拟数据示例）",
                               csv_dir=os.path.join("data", "demo"))
        return

    run_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    st = Status()
    with db.connect(C.DB_PATH) as con:
        if not a.report_only:
            try:
                import akshare  # noqa: F401
            except ImportError:
                sys.exit("缺少 akshare，请先运行：python3 -m pip install -r requirements.txt")
            today = dt.date.today()
            end = today.strftime("%Y-%m-%d")
            hist_start = (today - dt.timedelta(days=max(420, int(a.backfill * 1.6) + 120))).strftime("%Y-%m-%d")
            print("1/7 指数行情（交易日历、成交额）")
            st.run(con, run_at, "指数行情", step_index, con, hist_start, end)
            days = trading_days(con, hist_start, end)
            if not days:
                sys.exit("没拿到交易日历，检查网络后重试。")
            as_of = days[-1]
            n_days = max(a.backfill, 10)
            recent = days[-n_days:]
            print(f"2/7 上交所ETF份额（{len(recent)} 个交易日）")
            st.run(con, run_at, "上交所ETF份额", step_sse, con, recent)
            print("3/7 深交所ETF份额（仅最新）")
            st.run(con, run_at, "深交所ETF份额", step_szse, con, as_of)
            print("4/7 东方财富份额（补缺）")
            st.run(con, run_at, "东财份额补缺", step_em_backup, con, as_of)
            print("5/7 ETF行情")
            st.run(con, run_at, "ETF行情", step_prices, con, hist_start, end)
            print("6/7 融资余额")
            st.run(con, run_at, "融资余额", step_margin, con)
            print("7/7 公告与快讯")
            st.run(con, run_at, "公告与快讯", step_events, con, days[-a.event_days:])
            print("ETF定期报告（20%披露）")
            st.run(con, run_at, "ETF定期报告", step_disclosures, con)
        periods = [a.quarterly] if a.quarterly else ([] if a.report_only else recent_quarter_ends())
        for q in periods:
            have = db.read(con, "SELECT COUNT(*) n FROM holders WHERE report_date=?", (q,))["n"][0]
            if a.quarterly or have < 3 * len(C.QUARTERLY_STOCKS) // 2:   # 没抓全的季度每次都补
                print(f"季报校准：重点个股十大流通股东（{q}）")
                st.run(con, run_at, f"十大流通股东 {q}", step_quarterly, con, q)
        analyze_and_report(con, st)


if __name__ == "__main__":
    main()
