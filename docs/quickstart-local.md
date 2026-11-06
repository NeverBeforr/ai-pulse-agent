# 在本地通过 cron 运行 Pulse

本页供开发和本地调试；首发推荐GitHub Actions。需要Python3.11或3.12，安装后每次只启动一个短进程：

```bash
pipx install git+https://github.com/NeverBeforr/ai-pulse-agent@v0.3.0
pulse demo
pulse init --out pulse.local.yaml
```

在shell环境中提供LLM_BASE_URL、LLM_MODEL、LLM_API_KEY和FEISHU_WEBHOOK。默认使用公开来源；若改用可选IMAP，还要提供IMAP_USER、IMAP_PASSWORD。凭据只从环境读取，程序不自动读取真实.env，也不会从旧settings.json读取密钥。

机器人开启签名校验时，还需在环境中提供FEISHU_SIGNING_SECRET。该项可省略，但不能在YAML里填写明文。

```bash
export LLM_BASE_URL='https://your-provider.example/v1'
export LLM_MODEL='your-model'
export LLM_API_KEY='your-key'
export FEISHU_WEBHOOK='https://open.feishu.cn/open-apis/bot/v2/hook/your-token'
pulse check --config pulse.local.yaml
pulse run --config pulse.local.yaml
pulse status --config pulse.local.yaml --json
```

真实run先检查全部已启用配置，缺环境变量返回2。dry-run完全离线，不调用模型、不投递、不改用户去重状态；回放历史简报并列出未绑定变量，不代表真实配置已经可运行。可选邮箱来源还需要IMAP_USER、IMAP_PASSWORD。

模型客户端使用SDK的标准网络环境，代理由部署环境中的HTTP_PROXY、HTTPS_PROXY、NO_PROXY等控制，程序不修改系统代理。

0.4可用model.trust_env单独控制模型是否读取代理环境，默认true；本次本机联调配置为false以直连已配置的模型服务，网页采集仍沿用标准代理环境。

## 定时

cron不一定继承交互式shell变量。可以把export语句写入仅自己可读的环境脚本，再在任务中加载；该脚本不要提交到仓库。

```cron
0 8 * * * cd /absolute/path/ai-pulse-agent && . /absolute/path/private-pulse-env && /absolute/path/to/pulse run --config pulse.local.yaml >>pulse.log 2>&1
```

示例使用cron运行机器的时区，请自行确认。配置中的schedule仅作说明，不会启动内置调度。不要同时运行多个任务写同一状态文件。

Windows开发环境已有Conda时：

```powershell
conda activate E:\ai-pulse-agent\.conda
python -s -m pip install -e '.[dev]'
pulse demo
pulse run --dry-run
```

Windows的定时可用任务计划程序，由外部调用同一命令；主路径无需启动Web或保持终端常驻。旧看板可通过`pulse serve`手动使用，界面已冻结且没有内置定时。

## TLDR邮箱

Gmail通常需要开启IMAP并使用应用专用密码；支持范围由邮箱账户政策决定。国内邮箱通常需要在邮箱设置中启用IMAP并取得授权码，主机和端口按对应服务商填写。Pulse通过readonly目录和BODY.PEEK[]读取，不标记已读。

只解析TLDR HTML正文，不购买邮箱或第三方抓取服务。无HTML正文、认证失败、解析失败都会记录来源错误，不会伪装为成功。

本版使用用户名与授权码登录IMAP，没有实现邮箱OAuth流程。

## 从旧配置迁移

```bash
pulse init --from-legacy data/settings.json --out pulse.local.yaml
```

不会覆盖现有YAML，也不会复制旧API Key。原Web代码、模板、agent及旧数据库保留。SQLite增量迁移保留原7张表，增加投递相关列和表；历史Web简报不自动视为已经发送到飞书。

## 扩展来源

在src/ai_pulse/sources/新增适配器，实现fetch(since)，通过register注册并在包入口导入；同时为其配置字段、样本和失败语义写离线测试。当前不实现微信公众号抓取，官网或邮箱订阅不代表公众号全量覆盖。
