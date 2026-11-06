# 全文阅读与兴趣选稿

0.4开发版默认生成最多10条，行业热点与产品案例各5条。来源发现文章后，模型先根据来源摘要判断阅读优先级；程序获取原文、提取和保存正文，模型复核并总结，再组合简报。正文或合格内容不足时递补并如实少报。

selection.profile描述身份、关注和不感兴趣的内容。weights包含relevance、impact、usefulness、novelty四个非负权重，默认均为1；这只是可比较基线，没有宣称最优。模型按0至3粗分档给出判断和理由，程序计算分数；短摘要不直接视为低价值。最终选稿合并同事件报道，同分时优先不同主题。

```yaml
selection:
  profile: 关注AI前沿与真实开发应用，寻找产品灵感，排除纯融资、人事和同质化套壳。
  weights:
    relevance: 1
    impact: 1
    usefulness: 1
    novelty: 1
  max_model_calls: 60
  max_runtime_seconds: 600
```

后两项是部署资源预算，可按实际模型速度调整。单次请求继续使用model.max_input_tokens作为预算，按UTF-8字节数保守划分输入；这会比真实token计数更保守，并可能增加长文请求次数。正文不会为了适配预算被静默截断。超长文章分段阅读，所有段完成后再合成；记录字符范围与正文哈希，只有完成完整提取正文处理的文章才能入选。

模型支持时可以配置model.reasoning_effort及model.max_output_tokens；默认不发送这两个可选参数，避免强加给不支持的兼容服务。当前本地联调用同一模型的low强度与8192输出token预算，它们不改变正文完整输入和引用校验。接口拒绝参数时明确报错，不静默切换模型。

model.trust_env默认true，遵循部署环境的代理设置。本次本机的代理路径出现生成连接被远端关闭，设为false后完成真实运行；该选项只影响模型客户端，TLS证书校验保持开启，网页来源采集和系统代理不随之改变。

正文缓存保留抓取时刻，24小时内重试可复用；已通过复核的结果还需正文哈希、画像、模型、提示词和schema签名一致才复用。画像或正文改变会重新处理。状态统计会显示复用数量，不重复声称本轮重新调用模型读了这些材料。模型请求与正文请求独立释放客户端，避免一个长请求失败后继续复用其连接池。

正文获取与模型阅读分别记录。公开静态HTML使用Trafilatura提取；RSS或Atom明确携带的正文、HN原生长帖以及GitHub README也可作为阅读材料。200字符的最低可用性检查仅用于排除过短内容，不能证明网页提取绝对完整。登录提示、明确截断、PDF和不支持的格式不会退回来源摘要冒充全文。已知聚合页面有明确原文链接时追溯上游，否则不把摘编当作一手正文。

模型摘要为80至140字符，保留必要限制、作者主张和社区估算的性质。引句必须与保存正文对应；标题和摘要里的数字必须来自材料，不换算单位。确定性检查不能代替完整事实核验。

status的last_digest.reading和运行detail显示尝试阅读、正文可用、完成、失败、入选及预算状态；数据库evaluations保存逐篇评分和排除理由，article_contents保存本地正文。来源抓取成功与取得可用正文不同，HTTP 200也不自动代表正文可用。

本地只有Markdown输出时，可在pulse.local.yaml保留markdown sink、移除feishu sink，并使用独立state.path，再执行：

```bash
pulse check --config pulse.local.yaml
pulse run --config pulse.local.yaml --json
pulse status --config pulse.local.yaml --json
```

密钥继续来自环境变量。demo与dry-run仍回放历史5条，不访问网络或模型，也不展示为当日10条生成结果。真实正文保存在本地忽略目录；Actions默认只上传去除原文的状态快照，作业结束后不保留全文缓存。
