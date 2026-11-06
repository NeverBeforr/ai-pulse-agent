# 在 GitHub Actions 运行 Pulse

Pulse是短进程：读取pulse.yaml、发现文章、跨天去重、兴趣初筛、全文阅读与总结、投递、退出。定时由Actions触发，程序不自带调度器。0.4全文流程目前在本地验证，已发布的v0.3.0仍为历史版本。

## 准备配置

Fork仓库后，检查根目录pulse.yaml。默认来源为HN、TLDR公开归档和官方RSS；输出到飞书及本地markdown。默认不需要邮箱账号。

在仓库Settings中的Secrets and variables → Actions配置两项Secrets：

| Secret | 用途 |
| --- | --- |
| LLM_API_KEY | 支持兼容Chat Completions及JSON输出的模型密钥 |
| FEISHU_WEBHOOK | 你自己的飞书自定义机器人完整Webhook |

再配置两项非密钥Variables：LLM_BASE_URL、LLM_MODEL。例如模型地址通常以/v1结尾；不要追加/chat/completions。这些是你自己模型服务商的连接信息，不会随Fork自动复制。

飞书使用群内的自定义机器人Webhook。卡片包含Pulse，可与机器人的关键词要求对应。如果机器人开启签名校验，再添加FEISHU_SIGNING_SECRET Secret，填入机器人安全设置提供的签名密钥。程序自动读取该环境变量并按飞书官方算法生成时间戳及签名；未开启签名的机器人不需要第三项Secret。密钥不能写进YAML。

多机器人需要不同密钥时，可在对应feishu配置中使用signing_secret: ${YOUR_SECRET_ENV}显式引用，再由工作流注入该变量。签名、关键词或IP校验失败会记录具体业务错误，不会假报发送成功。完整消息含签名后的大小不得超过20KB。

如果后续需要读取邮箱中的TLDR，可以将tldr来源替换为imap来源，并额外设置IMAP_USER Variable和IMAP_PASSWORD Secret；邮箱主机、目录和筛选规则按配置规格填写。工作流已预留这两个环境变量。邮箱授权码不是交互式登录验证码；停用来源不必绑定凭据。

## 第一次执行

代码推送后，“离线检查”CI会运行pytest、Ruff、demo与dry-run。Fork的仓库可能需要先在Actions页面启用工作流。CI通过且两项Secrets、两项Variables填写完成后，在Actions选择“每日简报”→Run workflow，确认飞书已收到，并检查输出JSON、来源覆盖和退出码。

工作流默认只有workflow_dispatch，不会替你开启收费的每日执行。手动成功后，取消digest.yml中schedule的注释：UTC 00:00对应北京时间／香港时间08:00。更改时刻不需要改程序。

GitHub计划任务可能延迟，不保证精确分钟到达。首次开源部署和连续三天真实运行在本地无法替代验收。

## 状态与重试

0表示至少一个sink成功且达到目标；1表示部分或降级，需要检查失败来源、未投递通道或内容不足；2是配置错误；3是模型／数据库等运行错误；4是没有有效内容。非0退出会让工作流可见失败，状态仍尽可能保存。

同日重跑优先重试已保存简报的失败sink，不重复调用模型，也不重复投递已确认成功的sink。跨日重试旧简报时使用`pulse run --date YYYY-MM-DD`。

状态不依赖每天新增key的cache。每轮先由scripts/export_state.py生成去除正文、来源摘要、逐篇评估与原文引句的一致快照，再将data/checkpoint/pulse.db保存为pulse-state制品，保留14天；下一轮恢复到data/pulse.db。原始正文保留在本次运行的本地数据库，不默认上传。失败运行如果已经写入状态，也会保存恢复所需状态，以便重试投递。

制品到期或手动删除后，去重历史可能丢失并导致重复推送。恢复时会明确提示找不到状态；GitHub API或下载失败则直接失败，不静默创建空库。制品包含来源与简报历史，公开仓库中有相应读取权限的人可以下载；不要把私人邮件原文当作可公开的样本或日志。

工作流需要contents: read及actions: read，上传使用Actions制品服务。若修改state.path，需同步检查导出快照和scripts/restore_state.py中的恢复目标。当前使用分支独立的状态，避免不同分支混用。

同一分支的运行已串行化。不要让多个外部任务同时写同一SQLite文件。网络中断时可能出现“飞书已接受但响应丢失”，有限重试可能导致远端重复消息，当前不承诺端到端严格恰好一次投递。

## SMTP与其他平台

SMTP与Cloudflare运行时尚未实现。首发维护GitHub Actions部署，其他平台暂不提供完整运行保证。
