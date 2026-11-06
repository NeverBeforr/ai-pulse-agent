"""Pulse命令入口，退出码与结构化输出供脚本消费。"""

import argparse
import asyncio
import json
import logging
import sys
import traceback
from dataclasses import asdict
from pathlib import Path

from .config import init_config, load_config, redact
from .errors import ConfigError


class PulseParser(argparse.ArgumentParser):
    def error(self, message):
        raise ConfigError("命令参数错误：" + message)


def build_parser():
    parser = PulseParser(prog="pulse", description="可自托管的每日中文简报引擎")
    parser.add_argument("--verbose", action="store_true", help="显示调试日志")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "check", "status", "serve"):
        command = commands.add_parser(name)
        command.add_argument("--config", help="配置路径，默认pulse.yaml或PULSE_CONFIG")
        if name != "serve":
            command.add_argument("--json", action="store_true", help="stdout仅输出一个JSON对象")
        command.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)
        if name == "run":
            command.add_argument("--date", help="回溯指定日期，YYYY-MM-DD")
            command.add_argument("--dry-run", action="store_true", help="严格离线预览，不调用模型或投递")
            command.add_argument("--agent-mode", action="store_true", help="使用保留的多轮生成器作对照")
            command.add_argument("--once", action="store_true", help=argparse.SUPPRESS)
    demo = commands.add_parser("demo")
    demo.add_argument("--sink", choices=("markdown", "feishu-json"), default="markdown")
    demo.add_argument("--out", help="写入指定文件，否则输出stdout")
    init = commands.add_parser("init")
    init.add_argument("--from-legacy", nargs="?", const="data/settings.json")
    init.add_argument("--out", default="pulse.yaml")
    return parser


class SafeFormatter(logging.Formatter):
    def __init__(self, secrets):
        super().__init__("%(levelname)s %(message)s")
        self.secrets = secrets

    def format(self, record):
        return redact(super().format(record), self.secrets)


def _emit(payload, *, json_mode=False):
    print(json.dumps(payload, ensure_ascii=False, default=str) if json_mode else payload)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if stream.encoding.lower().replace("-", "") != "utf8":
            stream.reconfigure(encoding="utf-8")
    arguments = sys.argv[1:] if argv is None else argv
    args = argparse.Namespace(verbose="--verbose" in arguments, json="--json" in arguments)
    secret_values = []
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(SafeFormatter(secret_values))
    logger = logging.getLogger("ai_pulse")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG if args.verbose else logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        args = build_parser().parse_args(arguments)
        if args.command == "demo":
            from .offline import fixture_digest
            from .sinks.feishu import render_card
            from .sinks.markdown import render_markdown

            digest = fixture_digest()
            text = (
                render_markdown(digest, digest["items"])
                if args.sink == "markdown"
                else json.dumps(render_card(digest, digest["items"]), ensure_ascii=False, indent=2)
            )
            if args.out:
                Path(args.out).parent.mkdir(parents=True, exist_ok=True)
                Path(args.out).write_text(text, encoding="utf-8")
            else:
                print(text)
            return 0
        if args.command == "init":
            path = init_config(args.out, args.from_legacy)
            print(f"已生成{path}；请从环境变量提供密钥")
            return 0
        offline = args.command in {"status", "serve"} or getattr(args, "dry_run", False)
        config = load_config(args.config, offline=offline)
        secret_values.extend(config._secret_values)
        if args.command == "check":
            data = {
                "valid": True,
                "sources": [s.id for s in config.sources if s.enabled],
                "sinks": [s.type for s in config.sinks],
            }
            _emit(data if args.json else "配置有效；此命令不探测网络", json_mode=args.json)
            return 0
        if args.command == "status":
            from .storage import Store

            path = Path(config.state.path)
            result = {"last_run": None, "sources": [], "last_digest": None}
            records = {}
            if path.exists():
                store = Store(path.parent, database_path=path)
                result["last_run"] = next(iter(store.runs()), None)
                records = {(row["name"], row["url"]): row for row in store.sources()}
                digest = store.digest()
                if digest:
                    result["last_digest"] = {
                        "date": digest["local_date"],
                        "status": digest["status"],
                        "reading": digest["coverage"].get("reading"),
                        "delivered": [
                            r["sink"].split(":", 1)[0]
                            for r in store.deliveries(digest["id"])
                            if r["state"] == "sent"
                        ],
                    }
            for source in config.sources:
                if not source.enabled:
                    continue
                url = source.url or f"{source.type}://{source.host or source.id}"
                previous = records.get((source.id, url))
                result["sources"].append(
                    {
                        "id": source.id,
                        "state": previous["state"] if previous else "unverified",
                        "last_success": previous["last_success"] if previous else None,
                        "message": previous["message"] if previous else "尚未运行当前配置的来源",
                    }
                )
            print(redact(json.dumps(result, ensure_ascii=False, default=str), secret_values))
            return 0
        if args.command == "serve":
            import uvicorn

            from .app import create_app
            from .config import ENV_REF, Settings

            key = config.model.api_key.get_secret_value()
            settings = Settings(
                llm_base_url=config.model.base_url,
                llm_model=config.model.name,
                llm_api_key="" if ENV_REF.search(key) else key,
                scheduler_enabled=False,
                data_dir=Path(config.state.path).parent,
                database_path=Path(config.state.path),
                model_mode="cloud"
                if not ENV_REF.search(key) and not ENV_REF.search(config.model.name)
                else "preview",
                schedule_time=config.schedule,
                timezone=config.timezone,
            )
            logger.warning("serve为冻结的旧看板入口，内置调度已停用")
            uvicorn.run(create_app(settings), host="127.0.0.1", port=8765, workers=1)
            return 0
        from .pipeline import offline_preview, run_once

        if args.date:
            from datetime import date

            try:
                if date.fromisoformat(args.date).isoformat() != args.date:
                    raise ValueError("日期格式不匹配")
            except ValueError as exc:
                raise ConfigError("--date必须是YYYY-MM-DD格式的有效日期") from exc
        result = (
            offline_preview(config)
            if args.dry_run
            else asyncio.run(run_once(config, date=args.date, agent_mode=args.agent_mode))
        )
        if args.json:
            _emit(asdict(result), json_mode=True)
        elif result.markdown:
            print(result.markdown)
            logger.info("%s", result.message)
        else:
            logger.info("运行状态：%s；退出码：%s；%s", result.state, result.exit_code, result.message)
        return result.exit_code
    except KeyboardInterrupt:
        logger.error("运行被用户中断")
        if getattr(args, "json", False):
            _emit({"state": "aborted", "exit_code": 3, "error": "运行被用户中断"}, json_mode=True)
        return 3
    except ConfigError as exc:
        message = redact(exc, secret_values)
        logger.error("配置错误：%s", message)
        if getattr(args, "json", False):
            _emit({"state": "failed", "exit_code": 2, "error": message}, json_mode=True)
        return 2
    except Exception as exc:
        message = redact(f"{type(exc).__name__}: {exc}", secret_values)
        logger.error("运行错误：%s", message)
        if args.verbose:
            logger.debug("%s", redact(traceback.format_exc(), secret_values))
        if getattr(args, "json", False):
            _emit({"state": "failed", "exit_code": 3, "error": message}, json_mode=True)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
