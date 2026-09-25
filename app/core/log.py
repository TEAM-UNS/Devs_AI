import logging
import sys


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
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