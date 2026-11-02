"""python3 -m vkmusic [-c /data/vk-music/config.json] [-v]"""
from __future__ import annotations

import argparse
import logging

from .server import load_config, run


def main() -> None:
    ap = argparse.ArgumentParser(prog="vkmusic", description="VK Музыка для Станции Миди")
    ap.add_argument("-c", "--config", default="/data/vk-music/config.json")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(load_config(args.config))


if __name__ == "__main__":
    main()
