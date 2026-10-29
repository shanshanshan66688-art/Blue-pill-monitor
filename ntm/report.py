# -*- coding: utf-8 -*-
"""生成每日HTML报告（单文件，浏览器直接打开；图表库从CDN加载）。"""
import html
import json
import math
import os

import pandas as pd

import config as C


def _num(x, nd=1):
    if x is None or (isinstance(x, float) and math.isnan(x)) or pd.isna(x):
        return None
    return round(float(x), nd)


def _fmt(x, nd=1, sign=False):
    v = _num(x, nd)
    if v is None:
        return "—"
    return (f"{v:+,.{nd}f}" if sign else f"{v:,.{nd}f}")


def _esc(s):
    return html.escape("" if s is None or (isinstance(s, float) and math.isnan(s)) else str(s))


LEVEL_CLASS = {3: "lv3", 2: "lv2", 1: "lv1", 0: "lv0"}


def build(daily, fl, cap, events, holders, status, out_path, lookback=250, title_suffix=""):
    d = daily.dropna(subset=["pool_flow_yi"])
    # 只用数据覆盖过半的日期（例如上交所当天份额尚未发布时，那天先不作判断，下次运行补上）
    d = d[d["n_etf"] >= 0.5 * d["n_etf"].max()].tail(lookback)
    if d.empty:
        raise RuntimeError("没有可用的份额数据，无法生成报告")
    last_date = d.index[-1]
    r = d.iloc[-1]

    series = {
        "dates": list(d.index),
        "flow": [_num(v) for v in d["pool_flow_yi"]],
        "level": [int(v) for v in d["level"]],
        "label": list(d["label"]),
        "bench": [_num(v, 2) for v in d.get("bench_close", pd.Series(index=d.index, dtype=float))],
        "pool_shares": [_num(v) for v in d["pool_shares_yi"]],
        "turnover": [_num(v, 0) for v in d.get("turnover_yi", pd.Series(index=d.index, dtype=float))],
        "margin": [_num(v, 0) for v in d.get("margin_yi", pd.Series(index=d.index, dtype=float))],
    }

    # ---------- 今日各ETF明细
    flow_t = fl["flow_yi"].loc[last_date] if last_date in fl["flow_yi"].index else pd.Series(dtype=float)
    z_t = fl["z"].loc[last_date] if last_date in fl["z"].index else pd.Series(dtype=float)
    ds_t = fl["dshares"].loc[last_date] if last_date in fl["dshares"].index else pd.Series(dtype=float)
    name_map = {c[0]: c[1] for c in C.CORE_ETFS}
    cp = cap.set_index("code") if len(cap) else pd.DataFrame()
    etf_rows = []
    for code in fl["shares"].columns:
        z = z_t.get(code)
        flag = ""
        if pd.notna(z) and abs(z) >= C.Z_SINGLE:
            flag = "▲ 异常申购" if z > 0 else "▼ 异常赎回"
        c = cp.loc[code] if code in cp.index else pd.Series(dtype=object)
        etf_rows.append(f"""
        <tr>
          <td><span class="code">{code}</span> {_esc(name_map.get(code, ''))}</td>
          <td class="n">{_fmt(c.get('total_now_yi'))}</td>
          <td class="n">{_fmt(ds_t.get(code) / 1e8 if pd.notna(ds_t.get(code)) else None, 2, True)}</td>
          <td class="n">{_fmt(flow_t.get(code), 1, True)}</td>
          <td class="n">{_fmt(z, 1, True)}</td>
          <td>{flag}</td>
          <td>{_esc(c.get('period_end', ''))} {_esc(c.get('basis', '—'))}</td>
          <td class="n">{_fmt(c.get('cap_q_yi'))}</td>
          <td class="n">{_fmt(c.get('cap_now_yi'))}</td>
          <td class="n">{_fmt(c.get('cap_now_value_yi'), 0)}</td>
        </tr>""")

    # ---------- 存量上限汇总
    if len(cap):
        known = cap[~cap["basis"].isin(["无报告", "未识别"])]
        cap_total = cap["cap_now_value_yi"].sum()
        pool_total = (cap["total_now_yi"] * cap["close"]).sum()
        periods = sorted(set(known["period_end"])) if len(known) else []
        cap_txt = (f"按定期报告的20%披露规则，国家队（中央汇金投资+汇金资管）在这 {len(cap)} 只核心ETF上的持仓"
                   f"<b>最多约 {_fmt(cap_total, 0)} 亿元</b>，占这些ETF当前总规模（{_fmt(pool_total, 0)} 亿元）的 "
                   f"{_fmt(cap_total / pool_total * 100 if pool_total else None, 0)}%。"
                   f"其中 {len(known)} 只有可用的报告披露（报告期：{'、'.join(periods) or '—'}），"
                   f"其余 {len(cap) - len(known)} 只暂无可用披露，按总份额计入。")
        hero_cap = _fmt(cap_total, 0)
        hero_cap_sub = f"报告期 {periods[-1] if periods else '—'} 起算 · 核心池总规模 {_fmt(pool_total, 0)} 亿元"
    else:
        cap_txt, hero_cap, hero_cap_sub = "尚无定期报告披露数据。", "—", ""

    # ---------- 近期信号日
    sig = d[d["level"] >= 2].tail(15).iloc[::-1]
    sig_rows = "".join(
        f"<tr><td>{i}</td><td class='{LEVEL_CLASS[int(x.level)]}'>{_esc(x.label)}</td>"
        f"<td class='n'>{_fmt(x.pool_flow_yi, 1, True)}</td><td>{_esc(x.regime)}</td><td>{_esc(x.note)}</td></tr>"
        for i, x in sig.iterrows()) or "<tr><td colspan=5 class='muted'>回看期内没有达到“疑似”级别的日子</td></tr>"

    # ---------- 事件
    ev = events.sort_values("date", ascending=False).head(30) if len(events) else events
    ev_rows = "".join(
        f"<tr><td>{_esc(e.date)}</td><td>{_esc(e.source)}</td><td>{_esc(e.name)}</td>"
        f"<td>{('<a href=' + chr(34) + _esc(e.url) + chr(34) + ' target=_blank>' + _esc(e.title) + '</a>') if e.url else _esc(e.title)}</td></tr>"
        for e in ev.itertuples()) or "<tr><td colspan=4 class='muted'>暂无匹配关键词的公告或快讯</td></tr>"

    # ---------- 季报持股
    if len(holders):
        hd = holders.sort_values(["report_date", "stock"], ascending=[False, True]).head(60)
        hd_rows = "".join(
            f"<tr><td>{_esc(h.report_date)}</td><td>{_esc(h.stock)}</td><td>{_esc(h.holder)}</td>"
            f"<td class='n'>{_fmt((h.shares or 0) / 1e8, 2)}</td><td class='n'>{_fmt(h.ratio, 2)}</td><td>{_esc(h.change)}</td></tr>"
            for h in hd.itertuples())
    else:
        hd_rows = "<tr><td colspan=6 class='muted'>还没有运行季报校准（python run_daily.py --quarterly 2026-09-30）</td></tr>"

    st_rows = "".join(f"<tr><td style='white-space:nowrap'>{_esc(k)}</td><td style='white-space:nowrap' class='{'ok' if v[0] else 'bad'}'>{'正常' if v[0] else '失败'}</td><td class='muted' style='word-break:break-all'>{_esc(str(v[1])[:160])}</td></tr>"
                      for k, v in status.items())

    lv = int(r["level"])
    html_doc = TEMPLATE.format(
        last_date=last_date, suffix=_esc(title_suffix),
        verdict=_esc(r["label"]), lvclass=LEVEL_CLASS[lv],
        flow=_fmt(r["pool_flow_yi"], 1, True),
        sync=f"{int(r['sync_up'])} 只异常申购 / {int(r['sync_down'])} 只异常赎回（共 {int(r['n_etf'])} 只有数据）",
        regime=_esc(r.get("regime", "—")),
        note=_esc(r.get("note", "")) or "—",
        bench=_fmt(r.get("bench_close"), 2), bench_pct=_fmt(r.get("bench_pct"), 2, True),
        dd=_fmt(r.get("drawdown60_pct"), 1), r5=_fmt(r.get("ret5_pct"), 1, True),
        turnover=_fmt(r.get("turnover_yi"), 0), tp=_fmt(r.get("turnover_pctl"), 0),
        margin=_fmt(r.get("margin_yi"), 0), mp=_fmt(r.get("margin_pctl"), 0),
        bms=_fmt(r.get("big_minus_small"), 2, True),
        pool_shares=_fmt(r["pool_shares_yi"], 0), pool_value=_fmt(r["pool_value_yi"], 0),
        cap_txt=cap_txt, hero_cap=hero_cap, hero_cap_sub=_esc(hero_cap_sub), etf_rows="".join(etf_rows), sig_rows=sig_rows,
        ev_rows=ev_rows, hd_rows=hd_rows, st_rows=st_rows,
        data=json.dumps(series, ensure_ascii=False),
        z=C.Z_SINGLE, strong=C.STRONG_POOL_FLOW_YI, medium=C.MEDIUM_POOL_FLOW_YI, nsync=C.MIN_SYNC_ETFS,
    )
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_doc)
    return out_path


TEMPLATE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>蓝片哥 {last_date}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/echarts/5.5.0/echarts.min.js"></script>
<style>
:root{{color-scheme:light;--bg:#fcfcfb;--card:#ffffff;--line:#e6e5e0;--t1:#0b0b0b;--t2:#52514e;--t3:#8a8984;
--in:#2a78d6;--out:#e34948;--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--grid:#eeede8;
--lv3:#d03b3b;--lv2:#ec835a;--lv1:#8a8984;--ok:#0ca30c}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{color-scheme:dark;--bg:#1a1a19;--card:#222220;--line:#383835;
--t1:#f0efec;--t2:#c3c2b7;--t3:#8f8e87;--in:#3987e5;--out:#e66767;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--grid:#2c2c2a}}}}
:root[data-theme="dark"]{{color-scheme:dark;--bg:#1a1a19;--card:#222220;--line:#383835;
--t1:#f0efec;--t2:#c3c2b7;--t3:#8f8e87;--in:#3987e5;--out:#e66767;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--grid:#2c2c2a}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--t1);font:14px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}}
.wrap{{max-width:1180px;margin:0 auto;padding:24px 16px 60px}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:16px;margin:32px 0 10px}}
.muted{{color:var(--t3)}} .sub{{color:var(--t2);margin:0 0 20px}}
.hero{{display:grid;grid-template-columns:1.4fr 1fr 1fr 1fr;gap:12px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}}
.k{{color:var(--t2);font-size:12px}} .v{{font-size:24px;font-weight:600;margin-top:2px}} .s{{color:var(--t2);font-size:12px;margin-top:4px}}
.verdict .v{{font-size:22px}}
.lv3{{color:var(--lv3);font-weight:600}} .lv2{{color:var(--lv2);font-weight:600}} .lv1{{color:var(--lv1)}} .lv0{{color:var(--t2)}}
.lv3::before,.lv2::before{{content:"● "}}
.ok{{color:var(--ok)}} .bad{{color:var(--lv3)}}
.chart{{height:260px}} .chart.small{{height:170px}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{color:var(--t2);font-weight:500;white-space:nowrap}} td.n{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.code{{font-family:ui-monospace,Menlo,monospace;color:var(--t2)}}
.tw{{overflow-x:auto}} a{{color:var(--in)}}
.legend{{display:flex;gap:16px;color:var(--t2);font-size:12px;margin:4px 0 0}}
.sw{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}}
.foot{{color:var(--t3);font-size:12px;margin-top:36px;line-height:1.8}}
@media (max-width:820px){{.hero{{grid-template-columns:1fr 1fr}}.grid2{{grid-template-columns:1fr}}}}
</style></head>
<body><div class="wrap">
<h1>蓝片哥 · 国家队观察日报 · {last_date}{suffix}</h1>
<p class="sub">基于ETF份额变化、市场环境、公告事件与定期报告校准的推断，不是官方数据，不构成投资建议。</p>

<div class="hero">
  <div class="card verdict"><div class="k">今日判定</div><div class="v {lvclass}">{verdict}</div><div class="s">{note}</div></div>
  <div class="card"><div class="k">核心池净申赎</div><div class="v">{flow} 亿</div><div class="s">{sync}</div></div>
  <div class="card"><div class="k">市场环境</div><div class="v">{regime}</div><div class="s">沪深300 {bench}（{bench_pct}%）· 60日回撤 {dd}% · 5日 {r5}%</div></div>
  <div class="card"><div class="k">国家队ETF存量上限</div><div class="v">≤ {hero_cap} 亿元</div><div class="s">{hero_cap_sub}</div></div>
</div>

<h2>核心池每日净申赎（亿元）</h2>
<div class="card"><div id="c_flow" class="chart"></div>
<div class="legend"><span><i class="sw" style="background:var(--in)"></i>净申购</span><span><i class="sw" style="background:var(--out)"></i>净赎回</span><span>● 标记 = “疑似”及以上级别</span></div></div>

<div class="grid2" style="margin-top:12px">
  <div class="card"><div class="k">沪深300</div><div id="c_bench" class="chart small"></div></div>
  <div class="card"><div class="k">核心池总份额（亿份）</div><div id="c_shares" class="chart small"></div></div>
  <div class="card"><div class="k">全市场成交额（亿元，中证全指）· 当前分位 {tp}</div><div id="c_turn" class="chart small"></div></div>
  <div class="card"><div class="k">融资余额（亿元）· 当前分位 {mp}</div><div id="c_margin" class="chart small"></div></div>
</div>

<h2>今日各ETF明细</h2>
<div class="card tw"><table>
<tr><th>ETF</th><th>总份额(亿份)</th><th>份额变化(亿份)</th><th>申赎(亿元)</th><th>Z分数</th><th>异动</th><th>最新披露</th><th>季末上限(亿份)</th><th>当前上限(亿份)</th><th>上限市值(亿元)</th></tr>
{etf_rows}
</table></div>

<h2>存量上限</h2>
<div class="card">{cap_txt}<div class="s">原理：基金定期报告必须披露持有份额≥20%的单一投资者。汇金投资和汇金资管是两个独立投资者，若报告写“无人超过20%”，两家合计一定低于40%；若有人超过20%，取披露的份额；中报、年报点名中央汇金时用准确值。报告期之后：上限 = 季末上限 + 之后份额净增加，不超过当前总份额（假设其他持有人同期没有大额赎回）。证金、国新、诚通在这些ETF中份额历来很小，未计入。</div></div>

<h2>近期信号日</h2>
<div class="card tw"><table><tr><th>日期</th><th>判定</th><th>净申赎(亿元)</th><th>环境</th><th>说明</th></tr>{sig_rows}</table></div>

<h2>公告与快讯（关键词命中）</h2>
<div class="card tw"><table><tr><th>日期</th><th>来源</th><th>公司</th><th>标题</th></tr>{ev_rows}</table></div>

<h2>重点个股前十大流通股东中的国家队席位</h2>
<div class="card tw"><table><tr><th>报告期</th><th>股票</th><th>股东</th><th>持股(亿股)</th><th>占流通股%</th><th>增减</th></tr>{hd_rows}</table></div>

<h2>数据源状态</h2>
<div class="card tw"><table><tr><th>步骤</th><th>状态</th><th>信息</th></tr>{st_rows}</table></div>

<div class="foot">
判定规则：单只ETF申赎金额的稳健Z分数 ≥ {z} 记为异动；核心池净申赎 ≥ {strong} 亿且 ≥ {nsync} 只同向异动 → 强烈疑似；≥ {medium} 亿且多只同向 → 疑似；
恐慌区大额申购 / 过热区大额赎回会上调一级。阈值可在 config.py 修改。<br>
局限：份额变化无法区分是国家队还是险资等其他大机构；国家队已大幅退出ETF，现在信号噪音比2025年更高；国新、诚通更多直接买个股，主要靠公告层观察。
</div>
</div>
<script>
const D = {data};
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
function base(){{
  return {{animation:false,grid:{{left:56,right:16,top:16,bottom:28}},
    textStyle:{{color:css('--t2')}},
    tooltip:{{trigger:'axis',backgroundColor:css('--card'),borderColor:css('--line'),textStyle:{{color:css('--t1')}}}},
    xAxis:{{type:'category',data:D.dates,axisLine:{{lineStyle:{{color:css('--line')}}}},axisTick:{{show:false}},axisLabel:{{color:css('--t3')}}}},
    yAxis:{{type:'value',scale:true,splitLine:{{lineStyle:{{color:css('--grid')}}}},axisLabel:{{color:css('--t3')}}}}}};
}}
const charts=[];
function line(id,data,color,unit){{
  const el=document.getElementById(id); if(!el) return;
  const c=echarts.init(el); const o=base();
  o.series=[{{type:'line',data,showSymbol:false,lineStyle:{{width:2,color}},itemStyle:{{color}},connectNulls:true}}];
  o.tooltip.valueFormatter=v=>v==null?'—':v.toLocaleString()+unit;
  c.setOption(o); c.group='g'; charts.push(c);
}}
function draw(){{
  charts.forEach(c=>c.dispose()); charts.length=0;
  const c=echarts.init(document.getElementById('c_flow')); const o=base();
  o.yAxis.scale=false;
  o.tooltip.formatter=ps=>{{const i=ps[0].dataIndex; const v=D.flow[i];
    return D.dates[i]+'<br>净申赎 <b>'+(v==null?'—':(v>0?'+':'')+v.toLocaleString()+' 亿元')+'</b><br>'+D.label[i];}};
  o.series=[{{type:'bar',barMaxWidth:8,
    data:D.flow.map((v,i)=>({{value:v,itemStyle:{{color:v>=0?css('--in'):css('--out'),borderRadius:v>=0?[2,2,0,0]:[0,0,2,2]}}}})),
    markPoint:{{symbol:'circle',symbolSize:8,label:{{show:false}},
      data:D.flow.map((v,i)=>D.level[i]>=2?{{coord:[i,v],itemStyle:{{color:D.level[i]>=3?css('--lv3'):css('--lv2'),borderColor:css('--card'),borderWidth:2}}}}:null).filter(Boolean)}}}}];
  c.setOption(o); c.group='g'; charts.push(c);
  line('c_bench',D.bench,css('--s1'),'');
  line('c_shares',D.pool_shares,css('--s3'),' 亿份');
  line('c_turn',D.turnover,css('--s2'),' 亿元');
  line('c_margin',D.margin,css('--s1'),' 亿元');
  echarts.connect('g');
}}
if(window.echarts){{draw(); matchMedia('(prefers-color-scheme: dark)').addEventListener('change',draw);
  addEventListener('resize',()=>charts.forEach(c=>c.resize()));}}
else document.querySelectorAll('.chart').forEach(e=>e.innerHTML='<p class="muted">图表库加载失败（需要联网），表格数据不受影响。</p>');
</script>
</body></html>"""
