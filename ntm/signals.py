# -*- coding: utf-8 -*-
"""
信号计算层：把原始数据变成"今天国家队有没有动、往哪个方向、多大规模、有多可信"。

核心思路
1. 份额变化 × 收盘价 ≈ 当日申购/赎回金额（ETF份额只能通过申赎改变，二级市场买卖不改变份额）
2. 用过去60个交易日的"稳健Z分数"判断今天的申赎是否异常（中位数+MAD，不怕极端值污染）
3. 国家队的特征是"多只核心宽基ETF同一天、同方向、大额"，散户和一般机构很少这样
4. 再叠加市场环境：恐慌时大额申购、过热时大额赎回，最符合国家队的逆周期行为
5. 用定期报告披露的持仓做"倒挂"校准：总份额跌破国家队上期持仓 → 至少减持了差额
"""
import numpy as np
import pandas as pd

import config as C


# ============================================================ 1. 资金流
def compute_flows(shares, prices, codes):
    """
    shares: date, code, shares
    prices: date, code, close
    返回宽表 dict：shares / close / dshares / flow_yi / z / split
    """
    s = shares.pivot_table(index="date", columns="code", values="shares", aggfunc="last").sort_index()
    p = prices.pivot_table(index="date", columns="code", values="close", aggfunc="last").sort_index()
    s = s.reindex(columns=[c for c in codes if c in s.columns])
    p = p.reindex(index=s.index, columns=s.columns)
    p = p.ffill()

    ds = s.diff()
    prev = s.shift(1)
    share_pct = ds / prev * 100
    price_ratio = p / p.shift(1)

    # 拆分/合并：份额大变、价格同比例反向变，乘积≈1
    split = (share_pct.abs() > C.SPLIT_JUMP_PCT) & (((1 + share_pct / 100) * price_ratio - 1).abs() < 0.1)
    flow = ds * p / 1e8                     # 亿元
    flow = flow.mask(split)
    ds = ds.mask(split)

    # 稳健Z分数：只用"今天之前"的窗口，避免今天的大单把自己稀释掉
    hist = flow.shift(1)
    med = hist.rolling(C.ROLLING_WINDOW, min_periods=20).median()
    mad = (hist - med).abs().rolling(C.ROLLING_WINDOW, min_periods=20).median()
    sigma = (1.4826 * mad).clip(lower=0.5)  # 下限0.5亿，防止长期零波动导致Z爆表
    z = (flow - med) / sigma

    return {"shares": s, "close": p, "dshares": ds, "flow_yi": flow, "z": z, "split": split}


def pool_daily(fl):
    """核心池按日汇总"""
    flow, z = fl["flow_yi"], fl["z"]
    out = pd.DataFrame(index=flow.index)
    out["pool_flow_yi"] = flow.sum(axis=1, min_count=1)
    out["n_etf"] = flow.notna().sum(axis=1)
    out["sync_up"] = (z >= C.Z_SINGLE).sum(axis=1)
    out["sync_down"] = (z <= -C.Z_SINGLE).sum(axis=1)
    out["breadth_up"] = (flow > 0).sum(axis=1)
    out["breadth_down"] = (flow < 0).sum(axis=1)
    out["z_available"] = z.notna().sum(axis=1)
    out["pool_shares_yi"] = fl["shares"].sum(axis=1, min_count=1) / 1e8
    out["pool_value_yi"] = (fl["shares"] * fl["close"]).sum(axis=1, min_count=1) / 1e8
    return out


# ============================================================ 2. 环境
def _rolling_pctl(x, window=250):
    return x.rolling(window, min_periods=60).apply(lambda a: (a[:-1] < a[-1]).mean() * 100, raw=True)


def environment(index_df, margin_df):
    idx = index_df.pivot_table(index="date", columns="symbol", values="close").sort_index()
    amt = index_df.pivot_table(index="date", columns="symbol", values="amount").sort_index()
    env = pd.DataFrame(index=idx.index)
    b = idx.get(C.BENCHMARK_INDEX)
    if b is not None:
        env["bench_close"] = b
        env["bench_pct"] = b.pct_change() * 100
        env["ret5_pct"] = (b / b.shift(5) - 1) * 100
        env["drawdown60_pct"] = (b / b.rolling(60, min_periods=10).max() - 1) * 100
    for sym, name in C.EXTRA_INDEXES.items():
        if sym in idx.columns:
            env[f"pct_{name}"] = idx[sym].pct_change() * 100
    # 托大不托小：沪深300涨幅 - 中证1000涨幅
    if "bench_pct" in env and "pct_中证1000" in env:
        env["big_minus_small"] = env["bench_pct"] - env["pct_中证1000"]
    t_cols = [c for c in C.TURNOVER_INDEXES if c in amt.columns]
    if t_cols:
        env["turnover_yi"] = amt[t_cols].sum(axis=1, min_count=len(t_cols))
        env["turnover_pctl"] = _rolling_pctl(env["turnover_yi"])
    if margin_df is not None and len(margin_df):
        m = margin_df.set_index("date")["fin_balance_yi"].sort_index()
        env = env.join(m.rename("margin_yi"), how="left")
        env["margin_yi"] = env["margin_yi"].ffill()
        env["margin_pctl"] = _rolling_pctl(env["margin_yi"])
        env["margin_chg20_pct"] = (env["margin_yi"] / env["margin_yi"].shift(20) - 1) * 100

    def regime(r):
        dd, r5 = r.get("drawdown60_pct"), r.get("ret5_pct")
        if (pd.notna(dd) and dd <= C.PANIC_DRAWDOWN_PCT) or (pd.notna(r5) and r5 <= C.PANIC_5D_PCT):
            return "恐慌区"
        tp, mp = r.get("turnover_pctl"), r.get("margin_pctl")
        if pd.notna(tp) and pd.notna(mp) and tp >= C.HOT_TURNOVER_PCTL and mp >= C.HOT_MARGIN_PCTL:
            return "过热区"
        return "常态"

    env["regime"] = env.apply(regime, axis=1)
    return env


# ============================================================ 3. 判定
def classify(pool, env):
    df = pool.join(env, how="left")
    levels, labels, notes = [], [], []
    for d, r in df.iterrows():
        f = r["pool_flow_yi"]
        if pd.isna(f):
            levels.append(0); labels.append("无数据"); notes.append(""); continue
        up = f > 0
        sync = r["sync_up"] if up else r["sync_down"]
        no_hist = r["z_available"] == 0
        if no_hist:  # 历史不足时，用"金额 + 同方向家数"代替Z分数
            sync = r["breadth_up"] if up else r["breadth_down"]
        a = abs(f)
        if a >= C.STRONG_POOL_FLOW_YI and sync >= C.MIN_SYNC_ETFS:
            lv = 3
        elif a >= C.MEDIUM_POOL_FLOW_YI and sync >= max(2, C.MIN_SYNC_ETFS - 1):
            lv = 2
        elif a >= C.MEDIUM_POOL_FLOW_YI or sync >= C.MIN_SYNC_ETFS:
            lv = 1
        else:
            lv = 0
        if no_hist and lv == 2:   # 没有历史基准时，中等金额容易误报，只记为轻微异动
            lv = 1
        reg = r.get("regime", "常态")
        n = []
        if lv >= 2:
            if up and reg == "恐慌区":
                n.append("恐慌中大额申购，符合托底特征"); lv = min(lv + 1, 3) if lv == 2 else lv
            elif (not up) and reg == "过热区":
                n.append("过热中大额赎回，符合降温特征"); lv = min(lv + 1, 3) if lv == 2 else lv
            elif up and reg == "过热区":
                n.append("过热期申购，更可能是其他资金")
            elif (not up) and reg == "恐慌区":
                n.append("恐慌期赎回，更可能是其他资金")
            bms = r.get("big_minus_small")
            if up and pd.notna(bms) and bms > 1:
                n.append(f"大盘强于小盘{bms:.1f}个百分点")
        if no_hist:
            n.append("历史样本不足60天，按金额判断")
        direction = "买入" if up else "卖出"
        label = {3: f"强烈疑似国家队{direction}", 2: f"疑似国家队{direction}",
                 1: f"轻微异动（{direction}方向）", 0: "正常"}[lv]
        levels.append(lv); labels.append(label); notes.append("；".join(n))
    df["level"] = levels
    df["label"] = labels
    df["note"] = notes
    return df


# ============================================================ 4. 季报校准（倒挂）
def calibration(fl, daily, etf_meta):
    """
    etf_meta: list of (code, name, index, keyword, baseline_yi, baseline_date)
    对每只有披露基数的ETF：
      最少减持 = max(0, 基数 - 当前总份额)   —— 硬推断，确定性最高
      持有上限 = min(基数, 当前总份额)       —— 假设基数日后没有再买
      信号累计 = 基数日之后、被判为疑似国家队的日子里该ETF的份额变化合计
      估算持有 = clip(基数 + 信号累计, 0, 当前总份额)
    """
    s, close, ds = fl["shares"], fl["close"], fl["dshares"]
    if s.empty:
        return pd.DataFrame()
    last = s.index.max()
    sig_days = set(daily.index[daily["level"] >= 2])
    rows = []
    for code, name, _idx, _kw, base_yi, base_date in etf_meta:
        if code not in s.columns:
            continue
        cur = s[code].dropna()
        if cur.empty:
            continue
        cur_sh = cur.iloc[-1]
        px = close[code].dropna().iloc[-1] if close[code].notna().any() else np.nan
        row = {"code": code, "name": name, "total_shares_yi": cur_sh / 1e8, "close": px,
               "total_value_yi": cur_sh * px / 1e8 if pd.notna(px) else np.nan,
               "baseline_yi": base_yi, "baseline_date": base_date}
        if base_yi is not None:
            b = base_yi * 1e8
            row["inverted"] = bool(cur_sh < b)
            row["min_reduction_yi"] = max(0.0, b - cur_sh) / 1e8
            row["ceiling_yi"] = min(b, cur_sh) / 1e8
            mask = (ds.index > str(base_date)) & ds.index.isin(list(sig_days))
            acc = ds.loc[mask, code].sum(skipna=True)
            row["signal_acc_yi"] = acc / 1e8
            row["est_hold_yi"] = float(np.clip(b + acc, 0, cur_sh)) / 1e8
            row["est_hold_value_yi"] = row["est_hold_yi"] * px if pd.notna(px) else np.nan
            row["ceiling_value_yi"] = row["ceiling_yi"] * px if pd.notna(px) else np.nan
        rows.append(row)
    out = pd.DataFrame(rows)
    out.attrs["as_of"] = last
    return out


# ============================================================ 5. 存量上限（定期报告 20% 规则）
def holding_cap(fl, disc):
    """
    国家队（中央汇金投资 + 中央汇金资管 两个主体）在每只核心ETF上的持仓上限。

    季末上限：
      - 中报/年报点名了中央汇金 → 用点名的准确份额
      - 联接基金等其他产品超过20%的份额，国家队不可能持有，从总份额中扣除
      - 否则：两个主体各自要么出现在"≥20%持有人"表里，要么一定低于20%，
        所以上限 = {≥20%持有人的份额} ∪ {20%×期末份额, 20%×期末份额} 中最大的两个之和
      - 没有报告或表格未识别 → 上限 = 期末总份额（等于没有信息）
    季末之后：上限 = 季末上限 + max(0, 当前总份额 − 季末总份额)，并且不超过当前总份额
      （假设其他持有人同期没有大额赎回；若有，真实值可能略高于此上限）
    证金、国新、诚通、汇金旗下资管计划在这些宽基ETF中的份额历来很小，未计入。
    """
    import json
    s, close = fl["shares"], fl["close"]
    if s.empty:
        return pd.DataFrame()
    rows = []
    disc = disc.sort_values(["period_end", "pub_date"]).groupby("code").tail(1).set_index("code") if len(disc) else pd.DataFrame()
    for code in s.columns:
        cur = s[code].dropna()
        if cur.empty:
            continue
        total_now = cur.iloc[-1]
        px = close[code].dropna().iloc[-1] if close[code].notna().any() else np.nan
        row = {"code": code, "total_now_yi": total_now / 1e8, "close": px}
        if code in disc.index:
            d = disc.loc[code]
            pe = d["period_end"]
            total_q = d["end_shares"]
            if not total_q or pd.isna(total_q):
                hist = cur[cur.index <= pe]
                total_q = hist.iloc[-1] if len(hist) else np.nan
            hj = json.loads(d["huijin_json"] or "{}")
            rows_ = json.loads(d["ge20_json"] or "[]")
            ge20 = [x["shares"] for x in rows_ if x.get("candidate", True)]
            others = sum(x["shares"] for x in rows_ if not x.get("candidate", True))
            if hj:
                cap_q, basis = sum(hj.values()), "报告点名汇金"
            elif d["none_ge20"] == 1 or ge20:
                cand = sorted(ge20 + [0.2 * total_q] * 2, reverse=True)[:2]
                cap_q = min(sum(cand), total_q - others)
                basis = ("无机构超20%" if not ge20 else f"{len(ge20)}个机构超20%") + ("（另有联接基金超20%）" if others else "")
            else:
                cap_q, basis = total_q, "未识别"
            cap_now = min(total_now, cap_q + max(0.0, total_now - total_q))
            row.update({"report": d["title"], "period_end": pe, "basis": basis,
                        "total_q_yi": total_q / 1e8, "cap_q_yi": cap_q / 1e8,
                        "cap_now_yi": cap_now / 1e8})
        else:
            row.update({"report": "", "period_end": "", "basis": "无报告",
                        "total_q_yi": np.nan, "cap_q_yi": np.nan, "cap_now_yi": total_now / 1e8})
        row["cap_now_value_yi"] = row["cap_now_yi"] * px if pd.notna(px) else np.nan
        rows.append(row)
    return pd.DataFrame(rows)
