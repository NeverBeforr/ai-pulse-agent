# Pulse v0.3 改造规格（给 Codex 的执行文档）

> 2026年11月5日后续实施补充：用户另行授权首发展示与协作整理。demo使用历史材料录制回放（见demo.md），模块入口统一CLI；Actions状态改用14天制品恢复；Python3.11与3.12分别通过CI后提供支持。本文其余内容保留原设计背景，与这些明确更新冲突时以当前用户文档和对应测试为准。真实飞书与连续三天验收仍待完成。

> 对象：`E:\ai-pulse-agent` 现有代码
> 性质：**在现有实现上改造，不是重写**
> 目标：把它从「本地常驻看板」改成「可自托管的每日简报引擎」，具备开源项目该有的契约
> 版本：v0.3（2026-11）

---

## 0. 给 Codex 的执行约定

1. **按第 16 节的 P0 → P1 → P2 顺序做**，每个阶段结束必须通过该阶段的验收命令；不通过不要进入下一阶段。
2. **不要跨阶段重命名**。包名保持 `ai_pulse`，只新增 `pulse` 命令行入口；README 里可以叫 "Pulse"。
3. **不要删除现有 Web 代码**。`serve` 降级为可选入口，不新增任何 Web 功能。
4. 每个改动都要有对应测试；测试必须是**离线可跑**的（用 fixture，不依赖网络和 API Key）。
5. 有疑问时，选择「小改动 + 保留旧路径」而不是「大重构」。
6. 所有新增文案、注释、文档用中文；代码标识符用英文。

---

## 1. 为什么要改（结论先行）

现有实现能跑，但存在四个会直接毁掉产品价值的问题：

| # | 问题 | 证据 | 后果 |
|---|---|---|---|
| 1 | 采集客户端级故障 | 10-31 连续 14 次、9 路来源全部 `PoolTimeout`，`last_success` 全停在 01:50 | 服务静默停摆，用户以为没新闻 |
| 2 | 状态语义错误 | 0 个来源成功时 `run.state` 仍为 `success`（24/59 次） | 故障不可见，无法自愈判断 |
| 3 | 排序公式让 HN 垄断 | `score = log1p(hn_score) + 0.2*multi + ...`，无 HN 分条目上限 ≈1，有 HN 分动辄 5+ | 119 条候选选出 5 条全来自 HN，多源采集形同虚设 |
| 4 | 常驻进程 + 内置调度 | `Service.start()` + `AsyncIOScheduler` + FastAPI | 无法部署到 serverless；连接池跨小时复用；关机即停 |

改造后的目标形态：

> **`pulse run --once`：读配置 → 抓源 → 去重 → 一次模型调用 → 推送 → 退出。**
> 谁来定时调用它，由用户决定（GitHub Actions / cron / Cloudflare）。

---

## 2. 目标与非目标

### 目标

- 单次执行、无状态、幂等；同一份输入可重复运行。
- 无密钥也能跑通 `pulse demo`（内置 fixture）。
- 飞书 / markdown / 邮件三种输出，飞书是首选但不是唯一。
- 失败可见：每个源的成功/失败、上次成功时间、本次覆盖率，都要出现在简报和 `status` 里。
- 跨天去重：已推送过的条目不重复推。
- 一份代码支持三种部署：本地 cron、GitHub Actions、可选 Cloudflare。

### 非目标（明确不做）

- 多用户、账号、计费、权限。
- 微信公众号抓取（只留 `sources/` 扩展点与文档）。
- Web 新功能、新的可视化。
- 向量检索、个性化推荐、多 Agent 协作。
- 更换 License（保持现状）。

---

## 3. 目标架构

```
ai-pulse-agent/
├─ pulse.yaml                  # 新增：主配置（唯一入口）
├─ src/ai_pulse/
│  ├─ cli.py                   # 新增：argparse CLI，唯一主入口
│  ├─ pipeline.py              # 新增：run_once 编排（替代 Service 的调度职责）
│  ├─ config.py                # 改：yaml + env 覆盖 + 校验 + 旧配置迁移
│  ├─ models.py                # 改：新增 Config / DeliveryRecord 等模型
│  ├─ network.py               # 改：生命周期、连接池、错误分类
│  ├─ storage.py               # 改：新增 deliveries、digest.status；状态语义
│  ├─ digest.py                # 改：排序公式；preview 保留给 dry-run
│  ├─ generate.py              # 新增：单次调用生成（默认路径）
│  ├─ agent.py                 # 保留：作为 --agent-mode 的对照实现
│  ├─ sources/                 # 新增包（从 collectors.py 拆出）
│  │  ├─ base.py               #   Source 协议 + 注册表
│  │  ├─ rss.py                #   RSS/Atom（含中文媒体站）
│  │  ├─ hn.py                 #   Hacker News
│  │  ├─ tldr.py               #   TLDR 网页 archive（保留）
│  │  └─ imap.py               #   新增：读邮箱里的 TLDR 推送
│  ├─ sinks/                   # 新增包
│  │  ├─ base.py               #   Sink 协议
│  │  ├─ feishu.py             #   交互式卡片
│  │  ├─ markdown.py           #   本地文件
│  │  └─ email.py              #   SMTP
│  ├─ service.py               # 保留：仅 serve 模式使用（冻结）
│  ├─ app.py                   # 保留：Web（冻结）
│  └─ collectors.py            # 保留：薄转发到 sources/，避免破坏旧导入
├─ tests/
│  ├─ fixtures/
│  │  ├─ rss_zh.xml
│  │  ├─ hn_top.json
│  │  ├─ tldr_issue.html
│  │  ├─ tldr_email.eml
│  │  └─ feishu_card_expected.json
│  ├─ test_rank.py
│  ├─ test_sinks_feishu.py
│  ├─ test_status_semantics.py
│  ├─ test_dedup.py
│  └─ test_cli.py
└─ .github/workflows/
   ├─ ci.yml                   # 已有则扩展：pytest + ruff
   └─ digest.yml               # 新增：定时任务模板（默认 workflow_dispatch）
```

数据流：

```
pulse.yaml
   │
   ├─ sources/*  ──fetch──▶ list[CollectedItem] ──▶ store.save_item()
   │
   ├─ digest.rank_candidates()  ──去重(7天/已推送)──▶ candidates[≤20]
   │
   ├─ generate.single_pass()    ──1 次模型调用──▶ ModelDigest
   │
   ├─ sinks/*   ──▶ 飞书 / markdown / 邮件
   │
   └─ store.finish_run(state)   ──▶ 退出码
```

---

## 4. 关键设计决策（含理由，请勿更改）

| 决策 | 内容 | 理由 |
|---|---|---|
| D1 | 默认「单次调用」，`--agent-mode` 保留旧的多轮工具调用 | 现有 6 次调用 / 10.4 万 token / 128 秒，成本与延迟都不合理；单次调用足够完成选稿+摘要 |
| D2 | 触发与执行分离，进程内不再有调度器 | serverless 兼容；避免连接池跨小时复用 |
| D3 | 状态由「产出」决定，不由「跑完」决定 | 修复静默成功 |
| D4 | 去重是核心能力，必须有持久状态 | 这是唯一无法被 50 行脚本替代的部分 |
| D5 | 飞书是 sink，不是项目本身 | 避免项目退化成「一个飞书脚本」 |
| D6 | `serve` 保留但冻结 | 不破坏现有使用，也不扩大负债 |

---

## 5. 配置规格

### 5.1 `pulse.yaml`（新增，主配置）

```yaml
version: 1
timezone: Asia/Hong_Kong
# 仅用于文档与 serve 模式；实际触发时间由外部 cron 决定
schedule: "08:00"

output:
  items: 5            # 目标条数，3-8
  max_items: 8
  language: zh
  sections: [industry, products]   # 保留现有两板块
  max_summary_chars: 140

model:
  base_url: ${LLM_BASE_URL}
  api_key: ${LLM_API_KEY}
  name: ${LLM_MODEL}
  temperature: 0.2
  max_input_tokens: 20000
  timeout_seconds: 120

sources:
  - id: hn
    type: hn
    enabled: true
  - id: tldr-mail
    type: imap
    enabled: true
    host: imap.gmail.com
    port: 993
    username: ${IMAP_USER}
    password: ${IMAP_PASSWORD}
    mailbox: INBOX
    search: 'FROM "tldr" SINCE {since}'
  - id: qbit
    type: rss
    enabled: true
    url: https://example.com/qbit/rss.xml
    channel: 量子位

sinks:
  - type: feishu
    webhook: ${FEISHU_WEBHOOK}
  - type: markdown
    path: ./digests/{date}.md

state:
  path: ./data/pulse.db
  dedup_days: 7          # 同一 entry 在 N 天内不重复推送
```

### 5.2 规则

- 支持 `${ENV_VAR}` 插值；缺失变量直接报错并指出变量名（**不要静默用空字符串**）。
- 未知字段报错（`extra="forbid"`），并给出「可用字段」提示。
- `sources[].type` 必须是已注册类型，报错时列出已注册类型。
- 兼容旧配置：若存在 `data/settings.json` 且无 `pulse.yaml`，`pulse init --from-legacy` 生成 yaml；`pulse run` 直接报错提示执行迁移，不做隐式回退。
- 环境变量仍可覆盖：`PULSE_CONFIG`（配置路径）、`LLM_API_KEY` 等。

### 5.3 配置模型（pydantic）

```python
class ModelConfig(BaseModel): ...
class SourceConfig(BaseModel):
    id: str
    type: Literal["rss", "hn", "tldr", "imap"]
    enabled: bool = True
    # 各类型专属字段放在 extra 中由 adapter 自行校验
class SinkConfig(BaseModel):
    type: Literal["feishu", "markdown", "email"]
class PulseConfig(BaseModel):
    version: int = 1
    timezone: str
    schedule: str = "08:00"
    output: OutputConfig
    model: ModelConfig
    sources: list[SourceConfig]
    sinks: list[SinkConfig]
    state: StateConfig
```

---

## 6. CLI 契约

```bash
pulse run [--config PATH] [--date YYYY-MM-DD] [--dry-run] [--agent-mode] [--json]
pulse demo [--sink markdown|feishu-json] [--out PATH]
pulse check [--config PATH] [--json]
pulse status [--config PATH] [--json]
pulse init [--from-legacy PATH] [--out pulse.yaml]
pulse serve [--config PATH]          # 保留，冻结，不写进 README 主路径
```

| 退出码 | 含义 |
|---|---|
| 0 | 成功，且至少 1 个 sink 发送成功 |
| 1 | 部分成功（有源失败 / 部分 sink 失败）：CI 可用此告警 |
| 2 | 配置错误（yaml、插值、字段校验） |
| 3 | 运行错误（模型不可用、数据库异常） |
| 4 | 无有效内容（全部源失败，或去重后无候选） |

要求：

- `--dry-run`：不调用模型、不发送，用现有 `preview_digest()` 生成规则预览并打印；**任何情况下不能访问网络**（测试会断言）。
- `--json`：stdout 只输出一个 JSON 对象（供 CI / 脚本消费），日志走 stderr。
- `demo`：使用 `tests/fixtures/` 里的样本，**零网络、零密钥**，默认输出 markdown 到 stdout。

---

## 7. 数据模型变更

保留现有 7 张表，做增量修改：

```sql
-- digests 增加
ALTER TABLE digests ADD COLUMN status TEXT NOT NULL DEFAULT 'ok';      -- ok|degraded
ALTER TABLE digests ADD COLUMN delivered_at TEXT;
ALTER TABLE digests ADD COLUMN delivery TEXT NOT NULL DEFAULT '{}';    -- 各 sink 结果

-- 新增：投递记录（幂等 + 重试依据）
CREATE TABLE IF NOT EXISTS deliveries (
  id INTEGER PRIMARY KEY,
  digest_id INTEGER NOT NULL REFERENCES digests(id),
  sink TEXT NOT NULL,
  state TEXT NOT NULL,              -- pending|sent|failed|skipped
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT NOT NULL DEFAULT '',
  sent_at TEXT,
  UNIQUE(digest_id, sink)
);

-- 新增：已推送条目（跨天去重的显式记录）
CREATE TABLE IF NOT EXISTS delivered_entries (
  entry_id INTEGER PRIMARY KEY REFERENCES entries(id),
  digest_id INTEGER NOT NULL,
  delivered_at TEXT NOT NULL
);
```

`runs.state` 取值固定为：`running | success | partial | degraded | failed | aborted`。

判定规则（必须写在 `pipeline.py` 里，并有注释）：

```
sources_ok == 0                      → failed（无内容时同时用退出码 4）
0 < sources_ok < len(enabled_sources) → partial
全部成功但 sink 全失败               → failed
达到条数且 sinks 至少成功一个        → success
内容可用但 sink 全部失败             → degraded（简报已存库，未送达）
```

---

## 8. 模块改造清单

### 8.1 `network.py`

**现状**：模块级 `Network` 持有一个长生命周期 `AsyncClient`；每次请求做 DNS 检查；手动跟随重定向；对 429/5xx 重试。

**改造**：

1. `Network` 增加 `__aenter__/__aexit__`，**每次 `run_once` 新建、结束 `aclose()`**。禁止跨 run 复用。
2. `httpx.AsyncClient(limits=httpx.Limits(max_connections=8, max_keepalive_connections=4))`。
3. 重定向分支：`continue` 之前必须读完或显式关闭响应（`await response.aclose()`），避免连接未归还。
4. 新增异常分类，供状态判定使用：
   ```python
   class SourceFailure(Exception):
       kind: Literal["network", "http", "parse", "auth", "empty"]
   ```
5. 连续 `PoolTimeout` 时：判定为客户端故障，`run_once` 允许**整体重建 Network 重试一次**（最多一次，避免放大故障）。
6. DNS 检查保留，但 `socket.getaddrinfo` 超时设为 5 秒，失败按 `network` 归类。

**验收**：`test_status_semantics.py::test_pool_timeout_is_client_failure` 模拟全源 PoolTimeout，断言 run 状态为 `failed`、退出码 4，且日志出现「客户端故障」字样。

### 8.2 `digest.py`

**现状**：`rank_candidates` 用 `log1p(hn_score)` 主导打分。

**改造**：替换为按源归一化的公式（详见第 9 节），删除 `log1p(hn_score)` 主项。保留 `preview_digest`、`needs_daily_digest`（后者仅 `serve` 用）。

### 8.3 `collectors.py` → `sources/`

- 新增 `sources/base.py`：
  ```python
  class Source(Protocol):
      type: str
      def __init__(self, config: dict, network: Network): ...
      async def fetch(self, since: datetime) -> list[CollectedItem]: ...
  ```
  注册表：`SOURCES: dict[str, type[Source]]`，用装饰器 `@register("rss")`。
- 把 `hacker_news` / `tldr` / `parse_feed` / `parse_media` 平移进 `sources/hn.py`、`sources/tldr.py`、`sources/rss.py`，逻辑不改，只改签名。
- 新增 `sources/imap.py`：`imaplib.IMAP4_SSL` + `email` 标准库解析 HTML 正文，复用 `parse_tldr()`。**不引入新依赖**。
- `collectors.py` 保留为薄转发（`from .sources import ...`），避免破坏 `tests/test_core.py` 的旧导入。

### 8.4 `generate.py`（新增）

```python
async def generate_single_pass(config, candidates, existing) -> ModelDigest
```

- 一次调用：`candidates` 截断到 ≤20 条，每条只保留 `title / source / summary[:300] / url / published_at`。
- 提示词要求输出严格 JSON，字段与 `ModelDigest` 对齐（复用 `models.ChosenItem` 校验）。
- 解析失败重试 1 次（附上校验错误信息），仍失败则抛 `GenerationError`。
- 记录 usage（复用 `store.record_usage`），并断言 `input_tokens < config.model.max_input_tokens`，超出则截断候选而不是报错。

### 8.5 `agent.py`

保持不动，仅在 `cli.py` 里通过 `--agent-mode` 选择。README 不宣传。

### 8.6 `service.py` / `app.py`

- `service.py`：把 `collect()` / `digest()` 的实现**移动到 `pipeline.py`**，`Service` 改为薄包装，只给 `serve` 用。
- `app.py`：不改逻辑，仅确保 `create_app()` 仍能启动。

### 8.7 `storage.py`

- 增加 `deliveries`、`delivered_entries` 相关方法：
  ```python
  def mark_delivered(self, entry_ids: list[int], digest_id: int) -> None
  def recently_delivered(self, days: int) -> set[int]
  def record_delivery(self, digest_id: int, sink: str, state: str, error: str = "") -> None
  ```
- `finish_run` 增加 `state` 校验（只允许第 7 节的枚举）。
- 迁移：`Store.__init__` 里执行 `_migrate()`，用 `PRAGMA table_info` 判断列是否存在，幂等。

### 8.8 `cli.py`（新增）

`argparse`，不引入 typer/click。结构：

```python
def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except ConfigError as exc:   return 2
    except Exception as exc:     log.exception(...); return 3
```

### 8.9 `pyproject.toml`

- 新增依赖：`pyyaml>=6`。
- 新增入口：
  ```toml
  [project.scripts]
  pulse = "ai_pulse.cli:main"
  ```
- 版本号 → `0.3.0`。

---

## 9. 排序规格（替代现有公式）

```
输入：近 24h 候选条目，按 source 分组
1) 源内百分位：p_s(item) = 该源内按 (hn_score 或 published_at) 的排名百分位
2) popularity = max(p_s) over sources            # 取最强信号，而不是 HN 独占
3) multi      = 1 - exp(-0.5 * (n_sources - 1))  # 1源=0, 2源=0.39, 3源=0.63
4) recency    = exp(-age_hours / 24)
5) official   = 1 if 任一来源 channel 含「官方」 else 0
6) score = 0.45*popularity + 0.20*multi + 0.20*recency + 0.15*official
7) 排序稳定：score 相同按 sha1(title) 升序
```

要求：

- 新增 `test_rank.py`：
  - `test_hn_no_longer_dominates`：构造 HN 高分数条目 + 三源交叉条目，断言交叉条目排名 ≥ HN 条目。
  - `test_single_source_no_penalty`：单源条目仍可进入前 20。
  - `test_deterministic_order`：同分同序。

---

## 10. 生成规格

- 目标：单次调用，输入 < 20k token，耗时 < 40 秒。
- 板块：`industry` 最多 3 条，`products` 最多 3 条，总数 ≤ `output.items`（默认 5）。
- 每条必须含：`title`（≤60 字）、`summary`（80–140 字）、`reason`（≤40 字，"为什么值得看"）、`evidence_ids`、`evidence_status`。
- 禁止编造数字/日期；摘要中出现的数字必须能在候选摘要中找到（该条写进 prompt，不做自动校验）。
- prompt 模板放 `src/ai_pulse/prompts/digest.md`，便于用户自定义（配置文件可覆盖路径）。

---

## 11. Sink 规格

### 11.1 `sinks/base.py`

```python
class Sink(Protocol):
    type: str
    async def send(self, digest: dict, items: list[dict]) -> None: ...
```

失败必须抛异常，由 `pipeline` 记录到 `deliveries`，**不允许吞掉**。

### 11.2 `sinks/feishu.py`

- 使用「自定义机器人 webhook」，POST `msg_type=interactive`。
- 卡片结构：header（日期 + 一句总览）→ 每条一个 `div`（标题加粗 + 链接、摘要、`理由：…`）→ 底部 note（`来源 5/7 · 生成 08:00 · [反馈](…)`）。
- 限制：总大小 < 28KB；超长摘要截断到 140 字。
- 失败重试：网络错误重试 2 次（指数退避 2s/6s）；HTTP 4xx 不重试。
- 单测：把卡片 JSON 与 `tests/fixtures/feishu_card_expected.json` 比对，**时间戳与随机 id 先归一化**。

### 11.3 `sinks/markdown.py`

- 输出 `./digests/YYYY-MM-DD.md`，frontmatter 含 `date / coverage / items`。
- 同一天重复运行覆盖同名文件（幂等），不追加。

### 11.4 `sinks/email.py`

- 标准库 `smtplib` + `email.mime`，HTML 正文由 markdown 简单转换（不引入 markdown 库，用现有模板）。
- 配置：`host/port/username/password/from/to/starttls`。

---

## 12. 失败语义与可观测性

1. 简报顶部固定一行：
   `覆盖 5/7 个来源 · 上次成功 08:00 · 失败：机器之心(HTTP 403)、量子位(parse)`
2. `pulse status` 输出：
   ```json
   {"last_run": {...}, "sources": [{"id":"hn","state":"ok","last_success":"..."}],
    "last_digest": {"date":"2026-11-05","status":"ok","delivered":["feishu"]}}
   ```
3. 任何「0 来源成功」必须是 `failed` + 退出码 4，**不得**为 `success`。
4. 日志级别：默认 INFO，`--verbose` 为 DEBUG；**日志里不得出现 api_key / webhook 明文**（写测试）。

---

## 13. 测试计划

| 文件 | 覆盖点 |
|---|---|
| `test_rank.py` | 新公式：HN 不再垄断、多源加分、确定性排序 |
| `test_sinks_feishu.py` | 卡片 JSON golden、超长截断、4xx 不重试 |
| `test_status_semantics.py` | 全失败→failed/4；部分→partial/1；sink 全失败→degraded/1；PoolTimeout 归类 |
| `test_dedup.py` | 7 天内不重复；跨过窗口可重复；deliveries 幂等 |
| `test_cli.py` | `demo` 零网络（monkeypatch `httpx.AsyncClient` 抛错）、`--dry-run` 零网络、退出码、`--json` 只输出 JSON |
| `test_config.py` | 未知字段报错、缺 env 报错、legacy 迁移 |

约束：

- 全部离线；不读取真实 `.env`；fixture 放 `tests/fixtures/`。
- 现有 57 项测试必须继续通过；允许调整因接口变化而失效的断言，但**不允许删除测试**。

---

## 14. 部署

### 14.1 GitHub Actions（首发，写进 README）

`.github/workflows/digest.yml` 模板：

```yaml
name: daily-digest
on:
  schedule: [{cron: "0 0 * * *"}]   # UTC 00:00 = 北京时间 08:00
  workflow_dispatch:
permissions:
  contents: read
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.12"}
      - run: pip install .
      - uses: actions/cache@v4
        with:
          path: data
          key: pulse-state-${{ github.run_id }}
          restore-keys: pulse-state-
      - run: pulse run --config pulse.yaml
        env:
          LLM_API_KEY: ${{ secrets.LLM_API_KEY }}
          FEISHU_WEBHOOK: ${{ secrets.FEISHU_WEBHOOK }}
          IMAP_USER: ${{ secrets.IMAP_USER }}
          IMAP_PASSWORD: ${{ secrets.IMAP_PASSWORD }}
```

**必须诚实记录的限制**：Actions 的 cache 可能被驱逐，去重状态可能丢失；丢失只会导致重复推送，不影响正确性。文档里要写这句话。

### 14.2 本地

`docs/quickstart-local.md`：`pipx install .` + `crontab` 一行。

### 14.3 Cloudflare（P2，先写文档不写代码）

Worker + Cron + D1 的迁移路径写进 `docs/quickstart-cloudflare.md`，标注「尚未实现，欢迎 PR」。

---

## 15. README 重写要求

首屏**只放四样**，搭建步骤全部移到 `docs/`：

1. 一句话：`每天早上 8 点，5 条、每条 140 字，推到你自己的飞书。`
2. 一张飞书卡片截图（放 `docs/assets/card.png`）。
3. `uvx --from . pulse demo`（零密钥）。
4. 三种部署的链接：Actions / 本地 cron / Cloudflare（规划中）。

禁止：首屏出现「如何创建飞书机器人」的 8 个步骤。

---

## 16. 实施顺序与验收

### P0（必做，预计 1.5 天）

1. `cli.py` + `pipeline.run_once()` + 退出码。
2. `network.py` 生命周期与错误分类（修复静默故障）。
3. 状态语义（第 7 节判定表）。
4. `sinks/feishu.py` + `sinks/markdown.py`。
5. `pulse demo` 零密钥。
6. `pulse.yaml` 加载与校验（可先不支持 imap）。
7. 测试：`test_status_semantics`、`test_sinks_feishu`、`test_cli`。

**验收**（bash / PowerShell 通用，退出码分别用 `echo $?` / `$LASTEXITCODE`）：

```bash
pytest -q                                                          # 全绿
ruff check .                                                       # 无错误
pulse demo                                                         # 无需网络与密钥，输出 markdown
pulse run --dry-run --config tests/fixtures/pulse.yaml             # 退出码 0
```

### P1（1 天）

1. 排序新公式 + `test_rank.py`。
2. `sources/imap.py` + `test_sources_imap.py`（fixture 用 `.eml`）。
3. 去重表 + `test_dedup.py`。
4. `generate.py` 单次调用（`--agent-mode` 保留）。
5. README 重写 + `docs/quickstart-*`。

**验收**：连续 3 天用 Actions 跑通；`pulse status --json` 能反映真实源状态。

### P2（可选）

1. Cloudflare 文档。
2. `sinks/email.py`。
3. 把 `serve` 标记为 legacy 并在依赖里拆成 optional extra。
4. `requires-python` 降到 `>=3.11`（若代码无 3.12 专有语法）。

---

## 17. 禁止事项

1. ❌ 不要引入 Celery / Redis / Postgres / Docker Compose。
2. ❌ 不要新增 Web 页面或改 UI。
3. ❌ 不要删除 `agent.py`、`app.py`、`templates/`。
4. ❌ 不要为了「更好看」重写 SQLite 层。
5. ❌ 不要在代码里硬编码任何 API Key、webhook、邮箱。
6. ❌ 不要让任何测试依赖真实网络。
7. ❌ 不要把失败吞掉改成 `success`（这是本次改造要修的核心问题）。

---

## 18. 交付物清单

- [ ] `pulse` CLI 可用，`demo` 零密钥
- [ ] `pulse.yaml` 规格与校验
- [ ] 飞书卡片 + markdown 两个 sink
- [ ] 状态语义修复 + `status` 命令
- [ ] 排序公式替换 + 测试
- [ ] 去重表 + 测试
- [ ] 单次调用生成
- [ ] `digest.yml` 工作流模板
- [ ] README + 两篇 quickstart
- [ ] `CHANGELOG.md` 记录 0.3.0 的破坏性变更（配置格式、CLI）

---

## 19. 风险与待确认

| 项 | 说明 | 处理 |
|---|---|---|
| PoolTimeout 根因 | 本次只能通过「重建客户端 + 生命周期隔离」降低概率，无法在离线环境复现 | P0 加分类与重试；上线后看 `pulse status` 是否仍出现 |
| GitHub Actions 状态持久化 | cache 可能被驱逐 | 文档写明「可能重复推送」；P2 提供 R2/S3 backend |
| IMAP 邮箱兼容 | Gmail 需应用专用密码；国内邮箱需授权码 | quickstart 里按服务商分别写 |
| 公众号源 | 不在本项目实现 | 文档给出 `sources/` 扩展说明，风险留给使用者 |
| 单次调用质量 | 可能与多轮持平或略差 | 用 `--agent-mode` 保留对照，跑 3 天再决定是否删除旧路径 |

---

*本规格基于 2026-11-04 的代码与数据库状态编写。执行中如与代码不符，以「保留旧路径 + 小步改造」为原则，不要大改。*
