import logging
import sys


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format=(
            "%(asctime)s | "
            "%(levelname)s | "
            "%(name)s | "
            "%(message)s"
        ),
        stream=sys.stdout,
    )

    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.WARNING
    )

    logging.getLogger("httpx").setLevel(
        logging.WARNING
    )