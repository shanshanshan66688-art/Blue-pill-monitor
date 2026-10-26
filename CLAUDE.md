# 蓝片哥（Blue-pill-monitor）

用户把这个项目叫"蓝片哥"。它根据免费公开数据推断中国股市"国家队"的每日动向，并把日报发布成网页。

- 设计思路、判断规则和局限：先读 `项目思路.md`
- 网页：https://shanshanshan66688-art.github.io/Blue-pill-monitor/
- 每日运行：`.github/workflows/daily.yml`（工作日北京时间 18:40 和 21:10，GitHub Actions）
- 所有阈值和观察池都在 `config.py`；修改判断逻辑在 `ntm/signals.py`
- 本地不联网测试：`python run_daily.py --demo`
- 每次运行先跑 `diagnose.py` 测试各数据源，结果和运行日志都提交在 `logs/last_run.log`（GitHub 的日志下载在此环境被拦，看这个文件）
- GitHub 服务器在海外：深交所、东方财富K线常拒绝连接，`ntm/fetch.py` 里每类数据都有备用源
- 数据库 `data/monitor.db` 由工作流每天提交回仓库，不要手动删除
- 用户偏好：中文交流；讲解时一次讲一段，每段内容要充实
- 这是推断工具，不构成投资建议
