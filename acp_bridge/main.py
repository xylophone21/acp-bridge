"""Main entry point for the acp-bridge application."""

import argparse
import asyncio
import logging
import logging.handlers
import sys
from pathlib import Path

from acp_bridge.bridge import run_bridge
from acp_bridge.config import Config


def _setup_logging(level: str, log_dir: str | None = None) -> None:
    fmt = "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s"
    log_level = getattr(logging, level.upper(), logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler()]

    if log_dir:
        path = Path(log_dir)
        path.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.TimedRotatingFileHandler(
            path / "bridge.log", when="midnight", backupCount=30,
        )
        file_handler.namer = lambda name: name.replace("bridge.log.", "bridge-") + ".log"
        handlers.append(file_handler)

    logging.basicConfig(level=log_level, format=fmt, handlers=handlers)

    for name in ("httpx", "httpcore", "urllib3", "Lark", "websockets"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.WARNING)
        logger.handlers.clear()


def main():
    parser = argparse.ArgumentParser(description="ACP Bridge - Feishu to ACP Bridge")
    sub = parser.add_subparsers(dest="command")

    init_parser = sub.add_parser("init", help="Generate a scaffold config file")
    init_parser.add_argument("-c", "--config", default="bridge.toml")
    init_parser.add_argument("--override", action="store_true")

    run_parser = sub.add_parser("run", help="Run the bridge")
    run_parser.add_argument("--config", default="bridge.toml")
    run_parser.add_argument("--log-level", default="INFO")
    run_parser.add_argument("--log-dir", default=None, help="Directory for daily rotated log files")

    args = parser.parse_args()

    if args.command == "init":
        Config.init(args.config, args.override)
    elif args.command == "run":
        _setup_logging(args.log_level, args.log_dir)
        config = Config.load(args.config)
        asyncio.run(run_bridge(config))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
