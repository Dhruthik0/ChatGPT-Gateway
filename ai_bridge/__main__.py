import logging
import sys

import uvicorn

from . import config as cfg_mod
from .api import create_app


def main() -> None:
    cfg = cfg_mod.load()
    logging.basicConfig(
        level=logging.DEBUG if cfg.log_bodies else logging.INFO,
        format="[%(levelname)s] %(name)s %(message)s",
        stream=sys.stderr,
    )
    uvicorn.run(create_app(config=cfg), host=cfg.host, port=cfg.port, log_config=None)


if __name__ == "__main__":
    main()
