def default_sources() -> list[dict]:
    definitions = [
        ("Hacker News", "hn", "https://hacker-news.firebaseio.com/v0/", "社区公开 API", True),
        ("TLDR AI", "tldr", "https://tldr.tech/ai/archives", "公开归档", True),
        ("TLDR 科技主刊", "tldr", "https://tldr.tech/tech/archives", "公开归档", True),
        ("GitHub AI 项目发现", "github", "https://api.github.com/search/repositories", "公开仓库 API", True),
        ("OpenAI 官方", "rss", "https://openai.com/news/rss.xml", "官方 RSS", True),
        ("Google AI 官方", "rss", "https://blog.google/technology/ai/rss/", "官方 RSS", True),
        ("机器之心", "media", "https://www.jiqizhixin.com/", "官网替代渠道，非公众号全量", True),
        ("量子位", "media", "https://www.qbitai.com/", "官网替代渠道，非公众号全量", True),
        ("新智元", "web", "https://mp.weixin.qq.com/", "公众号原生入口待接通", False),
        ("AI科技评论", "web", "https://www.leiphone.com/", "媒体候选，准确账号及栏目待核验", False),
        ("极客公园", "media", "https://www.geekpark.net/", "官网替代渠道，非公众号全量", True),
        ("InfoQ", "web", "https://www.infoq.cn/", "官网替代入口待适配", False),
        (
            "DeepSeek 官方发布",
            "rss",
            "https://github.com/deepseek-ai/DeepSeek-V3/releases.atom",
            "单仓库发布订阅，不代表厂商完整动态",
            False,
        ),
        (
            "Qwen 官方发布",
            "rss",
            "https://github.com/QwenLM/Qwen3/releases.atom",
            "单仓库发布订阅，不代表厂商完整动态",
            False,
        ),
    ]
    return [
        {
            "name": name,
            "kind": kind,
            "url": url,
            "channel": channel,
            "enabled": enabled,
            "state": "unverified" if enabled else "pending",
            "message": "尚未验证" if enabled else "待验证免费接入渠道",
        }
        for name, kind, url, channel, enabled in definitions
    ]
