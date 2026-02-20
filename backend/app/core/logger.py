import sys
import logging


def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Setup logger for the application.

    Returns:
        logging.Logger: Configured logger instance.
    """

    logger = logging.getLogger(name)

    # Avoid duplicate handlers
    if logger.handlers:
        return logger
    
    logger.setLevel(level)

    # Create console handler and set level to debug
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)

    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Add formatter to console_handler
    console_handler.setFormatter(formatter)

    # Add console_handler to logger
    logger.addHandler(console_handler)

    # Prevent log messages from being propagated to the root logger
    logger.propagate = False

    return logger

# Create a logger instance for the application
logger = setup_logger('app')
