# 添加一个输出通道

参考[sinks/feishu.py](../src/ai_pulse/sinks/feishu.py)、[sinks/markdown.py](../src/ai_pulse/sinks/markdown.py)及[Sink协议](../src/ai_pulse/sinks/base.py)。通常修改适配器、注册表、配置模型三个运行文件，再补离线测试。

## 适配器与注册

在src/ai_pulse/sinks/新增类，构造函数接收已经校验的SinkConfig，实现async send(digest, items)。digest包含local_date、created_at、timezone和coverage；items包含中文标题、摘要、理由、链接和来源。

在src/ai_pulse/sinks/__init__.py的SINKS注册表加入新类型。在models.py中更新SinkConfig.type、必要字段和sink_fields允许字段表。新增密钥字段还需要注册config.py的SECRET_FIELDS并测试拒绝明文。

## 成功与失败

send只有在目标明确确认成功后才能返回。HTTP 200不一定代表业务接受，应检查服务商业务码；网络失败、超时、业务拒绝都必须抛出异常。编排层会记录deliveries并继续处理其他输出通道，不要在适配器内把失败转换成正常返回。

每次send创建并释放客户端，不跨运行保存连接池。按服务商语义决定是否重试，特别注意远端已接受但响应丢失可能产生重复。若服务商支持幂等键，可用简报ID构造；不要承诺无法保证的端到端恰好一次。

## 离线测试

验证payload或渲染内容、长度限制、确认成功、HTTP错误、业务错误和重试上限。通过MockTransport或假客户端测试，不向真实用户发送消息。接入编排后还需验证：全sink失败degraded/1，部分失败partial/1，重试时已确认成功的sink不会再次发送。

真实投递验证应单独授权并记录结果，不放进pytest。用户修改输出通道顺序会影响当前type:index形式的投递标识，应在已有失败投递处理完成后调整配置。
