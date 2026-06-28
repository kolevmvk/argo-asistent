from __future__ import annotations

import sys

from .config import Config
from .telegram_bot import TelegramBot


def main() -> int:
    config = Config.load()
    errors = config.validate()
    if errors:
        for error in errors:
            print(f"Config error: {error}", file=sys.stderr)
        return 2
    TelegramBot(config).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

