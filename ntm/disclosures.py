# -*- coding: utf-8 -*-
"""
定期报告披露：自动下载各ETF最新一期定期报告（季报/中报/年报）PDF，读出
  1) 报告期末基金份额总额
  2) "报告期内单一投资者持有基金份额比例达到或超过20%的情况"表
  3) 中报/年报"前十名持有人"中点名的中央汇金持有份额（季报不点名）
用来给国家队ETF持仓算一个严格的上限。

公告列表：东方财富 天天基金 F10（AKShare fund_announcement_report_em）
PDF：pdf.dfcfw.com/pdf/H2_{报告ID}_1.pdf
"""
import io
import json
import os
import re
import socket

import pandas as pd
import requests

socket.setdefaulttimeout(60)

REPORT_RE = r"(?:季度报告|中期报告|年度报告|年报)"
EXCLUDE_RE = r"(?:摘要|英文|提示|更正|补充|延期)"
TEXT_DIR = "data/report_text"   # 报告全文缓存（便于离线调试解析规则）
NUM = r"-?[\d,]+\.\d+"


def _num(s):
    return float(s.replace(",", ""))


def period_end_from_title(title):
    y = re.search(r"(20\d{2})", title)
    if not y:
        return None
    y = y.group(1)
    if "第1季度" in title or "第一季度" in title:
        return f"{y}-03-31"
    if "第2季度" in title or "第二季度" in title or "中期" in title or "半年" in title:
        return f"{y}-06-30"
    if "第3季度" in title or "第三季度" in title:
        return f"{y}-09-30"
    if "第4季度" in title or "第四季度" in title or "年度报告" in title or "年报" in title:
        return f"{y}-12-31"
    return None


def latest_report(code):
    import akshare as ak
    df = ak.fund_announcement_report_em(symbol=code)
    t = df["公告标题"].astype(str)
    df = df[t.str.contains(REPORT_RE) & ~t.str.contains(EXCLUDE_RE)].copy()
    if df.empty:
        return None
    df["period_end"] = df["公告标题"].apply(period_end_from_title)
    df = df.dropna(subset=["period_end"])
    # 同一报告期有季报又有年报/中报时，优先年报/中报（会点名前十名持有人）
    df["rank"] = df["公告标题"].str.contains("年度|中期|年报").astype(int)
    df = df.sort_values(["period_end", "rank", "公告日期"], ascending=False)
    if df.empty:
        return None
    r = df.iloc[0]
    return {"report_id": str(r["报告ID"]), "title": str(r["公告标题"]),
            "pub_date": str(r["公告日期"]), "period_end": r["period_end"]}


def download_text(report_id):
    path = os.path.join(TEXT_DIR, f"{report_id}.txt")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    from pypdf import PdfReader
    url = f"https://pdf.dfcfw.com/pdf/H2_{report_id}_1.pdf"
    r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    reader = PdfReader(io.BytesIO(r.content))
    text = "\n".join((p.extract_text() or "") for p in reader.pages)
    os.makedirs(TEXT_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return text


NONE_RE = re.compile(r"(未有|无需要披露的|未出现|不存在|没有|未发生)[^。；]{0,22}达到或超过20%")
SEC_RE = re.compile(r"单一投资者持有基金份额比例达到或超过20%的情况")
STOP_RE = re.compile(r"(产品特有风险|8\.2|§9|备查文件)")
M = r"\d{1,3}(?:,\d{3})*\.\d{2}"
# 一行 = 期初份额 申购份额 赎回份额 持有份额 份额占比。PDF常把数字拆行，去掉空白后首尾相连；
# 金额一律两位小数，所以按小数点就能切开。第一个金额前面可能粘着日期数字，它的值不使用。
ROW_RE = re.compile(r"[\d,]*?\.\d{2}(" + M + r")(" + M + r")(" + M + r")(\d{1,3}\.\d{1,4})%?")


def parse_report(text):
    """返回 end_shares, none_ge20, ge20(国家队候选行), ge20_other(联接基金等), huijin, section"""
    compact = re.sub(r"\s+", "", text)
    out = {"end_shares": None, "none_ge20": None, "ge20": [], "ge20_other": [], "huijin": {}, "section": ""}

    m = re.search(r"期末基金份额总额(?:（份）|\(份\))?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)", compact)
    if m:
        out["end_shares"] = _num(m.group(1))

    none_flag = bool(NONE_RE.search(compact))

    rows = []
    for sm in SEC_RE.finditer(compact):
        sec = compact[sm.end():sm.end() + 2000]
        stop = STOP_RE.search(sec)
        if stop:
            sec = sec[:stop.start()]
        if "期初" not in sec:
            continue
        out["section"] = sec[:600]
        prev = 0
        for rm in ROW_RE.finditer(sec):
            who = sec[prev:rm.start()]
            prev = rm.end()
            for h in ("份额占比", "（%）", "(%)", "时间区间", "持有基金情况"):   # 去掉表头文字
                if h in who:
                    who = who[who.rfind(h) + len(h):]
            # 汇金在季报里以"机构"出现；联接基金、其他基金产品、个人不可能是汇金。
            # 认不出类别的行按"可能是国家队"处理，保证上限不会被低估。
            other = bool(re.search(r"联接|基金联|ETF联|证券投资基金|个人|产品", who)) and "机构" not in who
            rows.append({"shares": _num(rm.group(3)), "pct": float(rm.group(4)), "holder": who[:60],
                         "candidate": not other})
        if rows:
            break
    if not rows and not none_flag:
        i = compact.find("达到或超过20%")
        out["section"] = compact[max(0, i - 200):i + 400] if i >= 0 else compact[:300]

    out["ge20"] = [r for r in rows if r["candidate"]]
    out["ge20_other"] = [r for r in rows if not r["candidate"]]
    if rows:
        out["none_ge20"] = False
    elif none_flag:
        out["none_ge20"] = True

    for name in ("中央汇金资产管理有限责任公司", "中央汇金投资有限责任公司"):
        for hm in re.finditer(name + r"(\d{1,3}(?:,\d{3})+(?:\.\d{2})?)", compact):
            v = _num(hm.group(1))
            if v > 1e6:
                out["huijin"][name] = max(out["huijin"].get(name, 0), v)
    return out


def update(con, codes, log=print):
    """每只ETF检查最新定期报告；新报告才下载解析。返回说明文字。"""
    have = pd.read_sql_query("SELECT code, report_id FROM disclosures", con)
    have = set(zip(have["code"], have["report_id"]))
    new, fail, skip = [], [], 0
    for code in codes:
        try:
            meta = latest_report(code)
            if not meta:
                fail.append(f"{code}无报告")
                continue
            if (code, meta["report_id"]) in have:
                skip += 1
            text = download_text(meta["report_id"])
            p = parse_report(text)
            # 联接基金持有的份额不是国家队，但只要有机构/联接基金超过20%，"无人超过20%"就不成立；
            # 国家队候选只看非联接基金的那些行
            if p["none_ge20"] is False and not p["ge20"]:
                p["none_ge20"] = True   # 超过20%的只有联接基金 → 国家队两家各自一定低于20%
            con.execute(
                "INSERT OR REPLACE INTO disclosures VALUES (?,?,?,?,?,?,?,?,?)",
                (code, meta["report_id"], meta["title"], meta["pub_date"], meta["period_end"],
                 p["end_shares"], None if p["none_ge20"] is None else int(bool(p["none_ge20"])),
                 json.dumps(p["ge20"] + p["ge20_other"], ensure_ascii=False), json.dumps(p["huijin"], ensure_ascii=False)))
            new.append(code)
            log(f"    {code} {meta['title']}：期末份额 {p['end_shares']}，"
                f"国家队候选无人≥20%={p['none_ge20']}，≥20%机构={[(round(r['shares']/1e8,2), r['pct']) for r in p['ge20']]}，"
                f"≥20%其他产品={[(round(r['shares']/1e8,2), r['pct']) for r in p.get('ge20_other', [])]}，汇金点名={p['huijin']}")
            if p["none_ge20"] is None:
                log(f"      [未识别20%表] 片段：{p['section'][:300]!r}")
        except Exception as e:
            fail.append(f"{code}:{type(e).__name__}:{str(e)[:60]}")
    return f"解析 {len(new)} 份（其中 {skip} 份为已缓存报告）" + (f"，失败 {'、'.join(fail)}" if fail else "")
