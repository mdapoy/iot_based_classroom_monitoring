import logging
import os
from dotenv import load_dotenv

load_dotenv()

def setup_logger():
    logger = logging.getLogger("app")

    # 🔥 ambil dari .env
    debug_mode = os.getenv("DEBUG", "false").lower() == "true"

    if debug_mode:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    if not logger.handlers:
        ch = logging.StreamHandler()

        formatter = logging.Formatter(
            "[%(asctime)s] [%(levelname)s] %(message)s"
        )
        ch.setFormatter(formatter)

        logger.addHandler(ch)

    return logger


logger = setup_logger()