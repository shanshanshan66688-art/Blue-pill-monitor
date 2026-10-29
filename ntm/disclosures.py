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
import re
import socket

import pandas as pd
import requests

socket.setdefaulttimeout(60)

REPORT_RE = re.compile(r"(季度报告|中期报告|年度报告|年报)")
EXCLUDE_RE = re.compile(r"(摘要|英文|提示|更正|补充|延期)")
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
    r = df.iloc[0]
    return {"report_id": str(r["报告ID"]), "title": str(r["公告标题"]),
            "pub_date": str(r["公告日期"]), "period_end": r["period_end"]}


def download_text(report_id):
    from pypdf import PdfReader
    url = f"https://pdf.dfcfw.com/pdf/H2_{report_id}_1.pdf"
    r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    reader = PdfReader(io.BytesIO(r.content))
    return "\n".join((p.extract_text() or "") for p in reader.pages)


def parse_report(text):
    """返回 end_shares, none_ge20, ge20(list of {shares,pct}), huijin({名称:份额}), section(调试用片段)"""
    flat = re.sub(r"[ \t　]+", " ", text)
    out = {"end_shares": None, "none_ge20": None, "ge20": [], "huijin": {}, "section": ""}

    m = re.search(r"期末基金份额总额\s*(" + NUM + ")", flat)
    if m:
        out["end_shares"] = _num(m.group(1))

    i = flat.find("达到或超过20%")
    if i < 0:
        i = flat.find("达到或超过 20%")
    if i >= 0:
        sec = flat[i:i + 2500]
        # 截到下一节标题为止
        stop = re.search(r"(产品特有风险|影响投资者决策的其他重要信息|§\s*9|§\s*10|备查文件)", sec[30:])
        if stop:
            sec = sec[:30 + stop.start()]
        out["section"] = sec[:1200]
        head = sec[:200]
        if re.search(r"(未有|没有|不存在|无单一投资者)", head):
            out["none_ge20"] = True
        else:
            rows = re.split(r"(?=机构\s*\d+\s)", sec)
            for row in rows[1:]:
                row = re.split(r"(个人|产品特有风险)", row)[0]
                pcts = re.findall(r"([\d.]+)\s*%", row)
                body = re.sub(r"[\d.]+\s*%", " ", row)
                nums = [x for x in re.findall(NUM, body)]
                if nums and pcts:
                    out["ge20"].append({"shares": _num(nums[-1]), "pct": float(pcts[-1])})
            out["none_ge20"] = False if out["ge20"] else None   # 没解析出行 = 未识别

    # 中报/年报点名的中央汇金（前十名持有人表：名称 后面跟 持有份额 和 占比）
    for name in ("中央汇金资产管理有限责任公司", "中央汇金投资有限责任公司"):
        for m in re.finditer(name + r"\s*(" + r"[\d,]+(?:\.\d+)?" + r")\s*([\d.]+)", flat):
            v = _num(m.group(1)) if "." in m.group(1) else float(m.group(1).replace(",", ""))
            if v > 1e6:  # 过滤掉页码之类的小数字
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
                continue
            text = download_text(meta["report_id"])
            p = parse_report(text)
            con.execute(
                "INSERT OR REPLACE INTO disclosures VALUES (?,?,?,?,?,?,?,?,?)",
                (code, meta["report_id"], meta["title"], meta["pub_date"], meta["period_end"],
                 p["end_shares"], None if p["none_ge20"] is None else int(bool(p["none_ge20"])),
                 json.dumps(p["ge20"], ensure_ascii=False), json.dumps(p["huijin"], ensure_ascii=False)))
            new.append(code)
            log(f"    {code} {meta['title']}：期末份额 {p['end_shares']}，"
                f"无≥20%={p['none_ge20']}，≥20%持有人={p['ge20']}，汇金点名={p['huijin']}")
            if p["none_ge20"] is None:
                log(f"      [未识别20%表] 片段：{p['section'][:300]!r}")
        except Exception as e:
            fail.append(f"{code}:{type(e).__name__}")
    return f"新解析 {len(new)} 份，已有 {skip} 份" + (f"，失败 {'、'.join(fail)}" if fail else "")
