"""Main entry point for the acp-bridge application."""

import argparse
import asyncio
import logging
import sys

from acp_bridge.bridge import run_bridge
from acp_bridge.config import Config


def main():
    parser = argparse.ArgumentParser(description="ACP Bridge - Feishu to ACP Bridge")
    sub = parser.add_subparsers(dest="command")

    init_parser = sub.add_parser("init", help="Generate a scaffold config file")
    init_parser.add_argument("-c", "--config", default="bridge.toml")
    init_parser.add_argument("--override", action="store_true")

    run_parser = sub.add_parser("run", help="Run the bridge")
    run_parser.add_argument("--config", default="bridge.toml")
    run_parser.add_argument("--log-level", default="INFO")

    args = parser.parse_args()

    if args.command == "init":
        Config.init(args.config, args.override)
    elif args.command == "run":
        logging.basicConfig(
            level=getattr(logging, args.log_level.upper(), logging.INFO),
            format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
        )
        # Silence noisy third-party loggers
        for name in ("httpx", "httpcore", "urllib3", "Lark", "websockets"):
            logger = logging.getLogger(name)
            logger.setLevel(logging.WARNING)
            logger.handlers.clear()

        config = Config.load(args.config)
        asyncio.run(run_bridge(config))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
