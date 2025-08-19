import logging
from pathlib import Path
import os

LOG_DIR = Path("data/logs")
DEFAULT_LOG_LEVEL = "INFO"

def get_logging_config(logger_names, log_level=None, log_dir=None):
    """
    Generates Django LOGGING configuration dynamically.

    Args:
        logger_names (list): A list of strings, each being a logger name.
        log_level (str, optional): The default log level. Defaults to DEFAULT_LOG_LEVEL.
        log_dir (Path, optional): The directory to store log files. Defaults to LOG_DIR.

    Returns:
        dict: The Django LOGGING configuration dictionary.
    """
    log_level = log_level or os.environ.get("LOG_LEVEL", DEFAULT_LOG_LEVEL)
    log_dir = log_dir or LOG_DIR

    # Ensure log directory exists
    log_dir.mkdir(parents=True, exist_ok=True)

    # Clear existing log files in the directory managed by this config
    for logger_name in logger_names:
        log_file = log_dir / f"{logger_name}.log"
        if log_file.exists():
            try:
                log_file.unlink()
            except OSError as e:
                print(f"Error removing log file {log_file}: {e}")
        # Create the log file to ensure it exists
        log_file.touch()


    handlers = {
        'console': {
            'level': log_level,
            'class': 'logging.StreamHandler',
            'formatter': 'standard',
        },
    }
    loggers = {
        # Root logger configuration - logs to console by default
        '': {
            'handlers': ['console'],
            'level': log_level,
            'propagate': True,
        },
         # Django's default logger
        'django': {
            'handlers': ['console'],
            'level': 'INFO', # Or your preferred level for Django logs
            'propagate': False, # Don't propagate Django logs to root
        },
        # Suppress faker debug messages
        'faker': {
            'handlers': [],  # No handlers = no output
            'level': 'CRITICAL',  # Only critical messages (essentially disabled)
            'propagate': False,
        },
        # Also suppress faker.providers which can be noisy
        'faker.providers': {
            'handlers': [],
            'level': 'CRITICAL',
            'propagate': False,
        },
    }

    # Dynamically create file handlers and logger configurations
    for name in logger_names:
        handler_name = f"file_{name}"
        handlers[handler_name] = {
            'level': log_level,
            'class': 'logging.FileHandler',
            'filename': log_dir / f"{name}.log",
            'formatter': 'standard',
        }
        loggers[name] = {
            'handlers': [handler_name, 'console'], # Log to own file and console
            'level': log_level,
            'propagate': False, # Don't pass to root logger
        }

    return {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'standard': {
                'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            },
        },
        'handlers': handlers,
        'loggers': loggers,
    }

# Example usage (optional, for testing the function itself)
if __name__ == "__main__":
    test_loggers = ["app1", "app2", "database"]
    logging_config = get_logging_config(test_loggers, log_level="DEBUG")
    import json
    print(json.dumps(logging_config, indent=4))

    # Example of how to use it with Python's logging
    # logging.config.dictConfig(logging_config)
    # logger1 = logging.getLogger("app1")
    # logger1.debug("This is a debug message for app1.")
    # logger1.info("This is an info message for app1.")

    # logger_db = logging.getLogger("database")
    # logger_db.warning("This is a warning for database.")
