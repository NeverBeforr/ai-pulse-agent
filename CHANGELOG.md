# 变更记录

## 0.3.0

- 主入口改为pulse run，运行一次即退出。时间由cron或GitHub Actions决定，移除进程内自动调度。
- 配置改为pulse.yaml及环境变量引用；未知字段、缺失变量和明文凭据报配置错误。旧settings.json不再提供运行密钥，需显式迁移。
- 新增飞书与markdown输出、投递状态和显式已投递去重记录。全源失败、部分来源失败和投递失败均具有非0退出码。
- 以源内百分位和多源信号替代HN原始热度主导公式，候选池最多20条。
- 默认一次模型生成，结构校验失败最多纠正一次。旧agent.py保留作为显式对照路径，旧Web通过serve降级保留。
- 新增TLDR IMAP只读采集、离线demo、严格离线dry-run、状态命令和部署模板。
- SQLite迁移保留原有表与历史数据，增加投递相关表与列；旧interrupted状态映射为aborted。
- SMTP、Cloudflare实现、Web可选依赖拆分和Python3.11兼容性仍属P2，尚未验收。
