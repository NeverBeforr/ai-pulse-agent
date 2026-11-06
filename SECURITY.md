# 安全说明

目前维护0.3.x版本。项目会处理模型API Key、飞书Webhook及可选邮箱授权码，所有凭据必须通过环境变量提供。GitHub Actions使用仓库Secrets；普通Variables只保存模型地址、名称等非密钥信息。

## 报告漏洞

请使用GitHub的[私密漏洞报告](https://github.com/NeverBeforr/ai-pulse-agent/security/advisories/new)，提供受影响版本、最小复现、影响和建议修复。不要在公开Issue或PR贴出真实密钥、完整Webhook、邮箱授权码或包含它们的日志。报告和复现请使用假凭据。

维护者收到报告后会确认影响并讨论修复；本项目由个人维护，不承诺固定响应时限。

## 如果凭据已经泄露

1. 在对应模型服务商撤销旧API Key并生成新Key；更新Actions中的LLM_API_KEY及本机环境变量。
2. 飞书Webhook泄露时，在飞书群里重置或重建机器人，更新FEISHU_WEBHOOK；旧地址应失效。签名密钥泄露时同时重置签名并更新FEISHU_SIGNING_SECRET。
3. 邮箱授权码泄露时，在邮箱服务商撤销应用密码或授权码，再更新IMAP_PASSWORD。
4. 暂停相关定时工作流，检查服务商用量、访问记录以及Actions日志和制品。先撤销凭据，再处理Git历史或日志；只删除文件不能让已泄露的凭据失效。

不要将密钥放入pulse.yaml、源码、公开文档或Issue。程序不会自动读取.env，也不会从旧settings.json恢复密钥。

## 已知边界

飞书支持按时间戳及签名密钥加签，邮箱OAuth尚未实现。来源内容、模型输出均不能授予执行权限；模型结果经过结构和证据ID校验，但不等于完成事实核验。Actions状态制品可能过期或被删除，远端接受消息后响应丢失也可能引发重复投递，当前不提供端到端恰好一次保证。
