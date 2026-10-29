# -*- coding: utf-8 -*-
"""SQLite 存储：所有抓到的数据都落库，重复运行会覆盖同一天的数据，不会重复。"""
import os
import sqlite3
from contextlib import contextmanager

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS etf_shares (
    date TEXT, code TEXT, shares REAL, source TEXT,
    PRIMARY KEY (date, code)
);
CREATE TABLE IF NOT EXISTS etf_price (
    date TEXT, code TEXT, close REAL, amount REAL, pct REAL,
    PRIMARY KEY (date, code)
);
CREATE TABLE IF NOT EXISTS index_daily (
    date TEXT, symbol TEXT, close REAL, amount REAL,
    PRIMARY KEY (date, symbol)
);
CREATE TABLE IF NOT EXISTS margin (
    date TEXT PRIMARY KEY, fin_balance_yi REAL
);
CREATE TABLE IF NOT EXISTS events (
    date TEXT, source TEXT, code TEXT, name TEXT, title TEXT, url TEXT,
    PRIMARY KEY (date, title)
);
CREATE TABLE IF NOT EXISTS holders (
    report_date TEXT, stock TEXT, holder TEXT, holder_type TEXT,
    shares REAL, ratio REAL, change TEXT,
    PRIMARY KEY (report_date, stock, holder)
);
CREATE TABLE IF NOT EXISTS disclosures (
    code TEXT, report_id TEXT, title TEXT, pub_date TEXT, period_end TEXT,
    end_shares REAL, none_ge20 INTEGER, ge20_json TEXT, huijin_json TEXT,
    PRIMARY KEY (code, report_id)
);
CREATE TABLE IF NOT EXISTS run_log (
    run_at TEXT, step TEXT, status TEXT, message TEXT
);
"""


@contextmanager
def connect(path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.executescript(SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


def upsert(con, table, df):
    """按主键覆盖写入。"""
    if df is None or len(df) == 0:
        return 0
    cols = list(df.columns)
    placeholders = ",".join("?" * len(cols))
    sql = f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    rows = [tuple(None if pd.isna(v) else v for v in r) for r in df.itertuples(index=False)]
    con.executemany(sql, rows)
    return len(rows)


def read(con, sql, params=()):
    return pd.read_sql_query(sql, con, params=params)


def log(con, run_at, step, status, message=""):
    con.execute("INSERT INTO run_log VALUES (?,?,?,?)", (run_at, step, status, str(message)[:500]))
