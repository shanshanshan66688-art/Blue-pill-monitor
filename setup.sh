#!/bin/bash
# 一次性安装：建虚拟环境、装依赖、回补约一年的历史数据、生成第一份报告
set -e
cd "$(dirname "$0")"
PY=$(command -v python3 || true)
if [ -z "$PY" ]; then
  echo "没有找到 python3。macOS 可在终端运行 xcode-select --install，或到 python.org 下载安装。"; exit 1
fi
echo "使用 $($PY --version)"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt
echo "依赖安装完成，开始回补历史数据（上交所份额逐日查询，约需 5-10 分钟）…"
.venv/bin/python run_daily.py --backfill 250
echo
echo "完成。报告：$(pwd)/reports/latest.html"
echo "下一步：bash install_schedule.sh  设置工作日自动运行"
