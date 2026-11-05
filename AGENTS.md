# 项目开发约束

工作目录为E:\ai-pulse-agent，包名保持ai_pulse，使用项目内Conda环境与Python3.12，不派生子Agent。

入口只校验一次，拒绝重复判空和滥用try/catch；单次逻辑不乱抽函数，低概率防御先征求同意

当前定位与实现以docs/REFACTOR_SPEC_v0.3.md为准：可自托管的每日简报引擎。主命令pulse run读取pulse.yaml，采集、去重、默认一次模型生成、投递并退出。进程内不再安排定时，由cron或Actions触发。

用户后续授权的首发展示与协作整理以当前docs和测试为准：历史材料回放、模块CLI入口、Actions制品状态恢复、Python3.11/3.12验证。不得把回放、离线检查或包发布当作真实飞书投递验收。

按P0、P1、P2依次验收。P1连续三天Actions实测未完成前不进入可选P2，不把离线样本当作真实投递或线上验收。

保留旧Web、模板、导入路径和agent.py，serve降级冻结，不增加Web功能。所有新文案与注释使用中文。

密钥只能来自环境变量，不从旧settings.json回退，不在YAML、源码、文档或日志中写入明文。配置错误、来源失败、模型失败和投递失败必须明确可见。

demo和dry-run必须零网络、零模型调用、零投递；测试只使用临时状态与fixture，不读取真实.env。规格要求的有限重试属于已授权范围，不扩展成多层通用回退。

GitHub发布与离线CI已获授权，目标仓库为NeverBeforr/ai-pulse-agent。未配置飞书Webhook前不触发真实简报；首次投递验证后再启用定时与连续三天实测。
