# 离线回放的来源与边界

pulse demo和pulse run --dry-run回放2026年10月30日真实保存的一期中文简报：YC的智能体协作实验、Simantic固件验证、Fabivo家具裁切、Cirkit电路PDF和Algeon GPU SQL。

执行路径为随包历史材料 → 与实时HN采集共用的parse_stories → 临时SQLite → 当前rank_candidates → 录制的中文结果校验 → 当前Markdown或飞书渲染器。整个过程不联网，不创建模型客户端，不发送消息，不读取或修改用户状态库。首次安装所需的依赖下载不属于回放执行。

相关数据位于src/ai_pulse/fixtures：

- replay_hn.json：根据当时保存的公开标题、链接、正文和热度导出的规范化HN响应，并非原始HTTP抓包。
- model_output.json：当时保存的中文摘要和理由，条目ID按回放顺序重新编号。
- replay.json：录制日期、来源和栏目调整说明。

原简报来自旧版多轮路径，采用1条行业热点、4条产品案例。当前回放将Algeon调整为行业热点，以符合每板块最多3条的配额，其余中文内容保留。回放不证明当前单次调用的质量、耗时或今天的来源可用性，历史内容也未在此次联网复核。

dry-run会校验配置结构、列出未绑定的环境变量，并明确区分样本来源和未检查的当前来源。真正验证凭据先执行pulse check，再单独授权真实run。

README图片由同一飞书payload离线渲染生成，不是已发送到真实飞书群的截图。
