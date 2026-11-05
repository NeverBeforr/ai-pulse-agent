# 添加一个信息源

先看现有的[sources/rss.py](../src/ai_pulse/sources/rss.py)、[sources/hn.py](../src/ai_pulse/sources/hn.py)和[Source协议](../src/ai_pulse/sources/base.py)。对于公开HTTP来源，通常修改三个运行文件，再添加一个样本和一个测试文件。

## 1. 实现适配器

在src/ai_pulse/sources/创建适配器，使用register注册类型。构造函数接收由配置模型校验过的字典和本轮Network；fetch(since)返回CollectedItem列表。

```python
from ..models import CollectedItem
from .base import register

@register("example")
class ExampleSource:
    type = "example"

    def __init__(self, config, network):
        self.config, self.network = config, network

    async def fetch(self, since):
        response = await self.network.get(self.config["url"])
        # 在这里解析服务商真实格式，按since过滤，返回CollectedItem。
        raise NotImplementedError("请实现并测试实际解析逻辑")
```

CollectedItem至少需要title和公开url；尽量提供summary与带时区的published_at。没有可信时间时使用None，不把抓取时间冒充发布时间。返回空列表表示成功访问但没有匹配内容；结构不识别、认证失败等应抛SourceFailure或原始明确异常。

## 2. 加入包入口

在src/ai_pulse/sources/__init__.py导入适配器，使装饰器实际执行。仅新建文件不会自动注册。

## 3. 更新配置模型

在src/ai_pulse/models.py的SourceConfig中，把类型加入type的Literal和adapter_fields类型字段表；需要url时，也加入必填url的检查条件。未知字段仍应拒绝，避免配置拼错却静默生效。

如果增加凭据字段，还要将字段注册到config.py的SECRET_FIELDS，使用SecretStr，并测试环境变量引用、明文拒绝和日志脱敏。这种情况会多改配置文件，不能只加适配器。

## 4. 加样本和离线测试

样本放tests/fixtures，注明来源日期和删改内容，清除个人信息。用httpx.MockTransport或假Network提供响应，禁止真实联网。覆盖：正常解析、日期与limit过滤、空结果、结构变化和认证失败。再验证全源失败仍返回failed/4、部分来源失败返回partial/1。

最后运行CONTRIBUTING中的检查命令。配置示例写入文档即可，不要把未验证的新来源直接加入所有用户的默认配置。
