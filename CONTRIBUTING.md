# 参与贡献

Pulse的首发部署入口是GitHub Actions：读配置、抓源、跨天去重、生成、投递、退出。请先提交能复现问题的小改动；新来源和新输出通道是优先扩展方向。

## 本地准备

需要Python3.11或3.12，以及Git。建议创建虚拟环境后安装：

```bash
git clone https://github.com/NeverBeforr/ai-pulse-agent.git
cd ai-pulse-agent
python -m pip install -e '.[dev]'
pytest -q
ruff check .
python -m ai_pulse demo
```

测试只使用公开样本、临时状态和假凭据，不需要真实模型、邮箱或机器人。不要把个人环境、数据库或实际密钥加入提交。Windows现有Conda环境的使用见[本地说明](docs/quickstart-local.md)。

## 从哪里开始

- [添加来源](docs/adding-a-source.md)：通常修改适配器、来源注册、配置模型三个运行文件，再补样本与测试。
- [添加输出通道](docs/adding-a-sink.md)：实现发送、注册、配置校验，并验证失败和幂等语义。
- [good first issue](https://github.com/NeverBeforr/ai-pulse-agent/labels/good%20first%20issue)：适合范围明确的小任务；维护者确认需求后再扩大实现。

## 提交前

修复需要覆盖触发条件的离线回归测试；新增解析器提供最小样本，说明样本来源及删改。行为、配置或安装方式发生变化时同步更新用户文档。完整验证：

```bash
pytest -q
ruff check .
pulse demo
python -m ai_pulse demo
pulse run --dry-run --config tests/fixtures/pulse.yaml
```

PR应说明具体问题、修改后的行为和验证结果。无需为了提交整理无关文件、抽象单次逻辑或改写旧Web。

入口只校验一次，拒绝重复判空和滥用try/catch；单次逻辑不乱抽函数，低概率防御先征求同意。

漏洞请按[安全说明](SECURITY.md)私下报告。规划与评审历史归档在notes，当前行为以代码、测试和docs为准。
