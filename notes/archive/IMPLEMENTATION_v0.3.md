# v0.3 实施与验收记录

日期：2026年11月5日。按REFACTOR_SPEC_v0.3.md的P0、P1、P2顺序实施。

## 已采用的契约解释

- 全部来源失败：failed，退出码4。内容已存库但所有sink失败：按测试计划使用degraded，退出码1。
- dry-run完全离线，保留未绑定的环境引用并明确说明；不把离线成功当作真实配置或来源检查通过。真实run与check严格检查变量。
- 旧Web与agent代码保留。内置调度停用；旧Web只接受手动任务，每个任务也释放连接。
- 旧密钥文件保留在忽略目录中，但程序不再从中读取密钥。新配置只引用环境变量。

## P0 验收

已完成CLI、YAML配置、单次编排、网络生命周期、状态契约、SQLite增量迁移、飞书与markdown输出及离线样本。

验收命令与结果：

- `pytest -q`：78项通过。
- `ruff check .`：通过。
- `pulse demo`：退出码0，输出markdown。
- `pulse run --dry-run --config tests/fixtures/pulse.yaml`：退出码0。

测试不读取真实.env，真实HTTP传输被禁止；demo和dry-run另有客户端构造禁用断言。旧57项测试保留，调整了接口变化导致的断言：interrupted改为aborted、候选总预算改为20、旧Web拒绝提交明文密钥。

## P1 状态

P0通过后开始，已完成以下本地实现：

- 按来源归一化的排序、稳定排序、20条候选预算；高分HN与多源交叉信息的回归测试。
- RSS、HN、TLDR归档和只读IMAP适配器，沿用原解析逻辑及旧导入路径。
- 已投递条目的7天去重、增量数据库迁移、失败sink重试和已确认sink的幂等跳过。
- 默认单次模型生成、严格中文JSON及证据ID校验；结构错误最多纠正一次，总请求最多两次；旧多轮路径保留作对照。
- Markdown和飞书交互卡片、来源覆盖与失败详情、结构化CLI退出结果。
- 默认手动触发的Actions工作流、SQLite缓存保存和状态输出、README及部署文档。卡片图片由离线样本渲染，不是真实机器人投递截图。

最终本地验收：

- `pytest -q`：117项通过。测试隔离真实凭据，禁止真实HTTP与IMAP连接。
- `ruff check .`：无错误。
- `pulse demo`、`pulse run --dry-run --config tests/fixtures/pulse.yaml`、`pulse run --dry-run`：均退出0。
- `run --dry-run --json`与`status --json`：stdout可解析为单个JSON对象；缺环境变量的`check --json`明确退出2。
- 构建v0.3.0 wheel，在源码目录外安装并执行demo、init和dry-run，验证随包样本与提示词可用。
- 对照改造前备份：原31个测试函数（含参数化共57项）全部保留；`agent.py`字节未变；模板与静态资源未变。

可复核的命令结果在 [acceptance-v0.3.json](acceptance-v0.3.json)。改造前代码和SQLite备份位于本地忽略目录 `data/backups/before-v0.3-20261105-012056`。

**P1线上验收待完成**：连续三天Actions实测需要首次投递与真实运行时间。2026年11月5日已按用户授权初始化Git，创建公开仓库[ NeverBeforr/ai-pulse-agent](https://github.com/NeverBeforr/ai-pulse-agent)，并配置origin；仓库首发使用HN、TLDR公开归档和官方RSS。模型配置通过Actions Secret/Variables提供，个人配置、密钥和状态库不提交。用户尚未准备飞书Webhook，本轮只做代码发布和离线CI，不触发每日简报、不启用定时。未登录真实邮箱、未向真实飞书机器人投递；本地验证不替代该验收。

2026年11月5日用户追加授权后，已完成一次真实模型简报生成：10条真实候选，最终4条摘要保存至Markdown；首轮长度校验失败后纠正一次，共2次模型调用、91.828秒。因不足5条，运行正确返回degraded/1。该结果不满足正常一次调用、40秒内生成的目标；详情见 [模型接入记录](MODEL_INTEGRATION.md)。原离线验收JSON保持为当时的验收快照。

后续验收按部署文档在目标仓库进行：配置环境与Secrets → 首次手动运行并核对投递和`status --json` → 启用外部定时 → 连续三天检查覆盖率、状态、去重、模型调用次数和耗时。失败应保留非0退出和状态缓存。

## P2 状态

可选阶段；P1连续三天Actions验收完成前不进入。SMTP输出、Web依赖拆分与Python3.11兼容性暂未实现或承诺。
