import sys
import logging
import os


# Resolve log level once from environment (avoids circular import with config).
# Falls back to INFO if LOG_LEVEL is unset or invalid.
_ENV_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
_LOG_LEVEL = getattr(logging, _ENV_LEVEL, logging.INFO)


def setup_logger(name: str) -> logging.Logger:
    """Return a logger configured with the application-wide log level.

    Uses the LOG_LEVEL environment variable (resolved once at import time).
    Avoids duplicate handlers if called multiple times for the same name.

    Args:
        name: The logger name (typically ``__name__``).

    Returns:
        logging.Logger: Configured logger instance.
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(_LOG_LEVEL)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(_LOG_LEVEL)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    logger.propagate = False

    return logger
