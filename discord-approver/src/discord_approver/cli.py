"""CLI entry point for the Discord Approver Bot.

Usage: discord-approver
   or: python -m discord_approver.cli
"""

from __future__ import annotations

import logging
from pathlib import Path

from discord_approver.bot import build_bot
from discord_approver.broker_client import HTTPBrokerClient
from discord_approver.config import load_config
from discord_approver.state import SqliteMessageStore


def main() -> None:
    """Load config, wire up components, run the bot."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    cfg = load_config()

    # Ensure state directory exists
    cfg.state_dir.mkdir(parents=True, exist_ok=True)

    store = SqliteMessageStore(Path(cfg.state_dir) / "messages.sqlite3")
    broker = HTTPBrokerClient(
        cfg.broker_url, cfg.broker_token, signing_secret=cfg.broker_signing_secret
    )
    bot = build_bot(cfg, store, broker)

    logging.getLogger(__name__).info(
        "starting bot (channel=%s, broker=%s, poll=%.1fs)",
        cfg.discord_channel_id, cfg.broker_url, cfg.poll_interval,
    )

    bot.run(cfg.discord_token, log_handler=None)  # log_handler=None to avoid double-logging


if __name__ == "__main__":
    main()
