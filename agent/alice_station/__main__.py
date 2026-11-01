"""Запуск: python3 -m alice_station [--config PATH] [--pc] [-v]"""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from . import __version__, config


def main() -> None:
    ap = argparse.ArgumentParser(prog="alice-station", description="Голосовой агент XiaoZhi для Станции Миди")
    ap.add_argument("--config", type=Path, help="путь к config.toml")
    ap.add_argument("--pc", action="store_true",
                    help="режим эмулятора на ПК: микрофон/динамик по умолчанию, без LED и кнопок")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--version", action="version", version=__version__)
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = config.load(args.config)
    if args.pc or cfg["device"]["kind"] == "pc":
        cfg.data["device"]["kind"] = "pc"
        cfg.data["audio"]["capture_device"] = cfg.data["audio"].get("pc_capture_device", "default")
        cfg.data["led"]["enabled"] = False
        cfg.data["trigger"]["button_enabled"] = False
        cfg.data["music"]["player_cmd"] = cfg.data["music"].get(
            "pc_player_cmd", ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "{url}"])
        cfg.data["music"]["player_env"] = {}
        if cfg.data["panel"]["host"] == "0.0.0.0":
            cfg.data["panel"]["host"] = "127.0.0.1"

    from .app import App

    async def run():
        await App(cfg).run()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
