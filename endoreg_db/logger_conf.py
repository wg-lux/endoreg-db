import logging
from pathlib import Path
import os

LOG_DIR = Path("data/logs")
DEFAULT_FILE_LOG_LEVEL = "INFO"
DEFAULT_CONSOLE_LOG_LEVEL = "WARNING"

def clear_log_files(logger_names, log_dir=None):
    """
    Clears specified log files in the log directory.

    Args:
        logger_names (list): A list of strings, each being a logger name.
        log_dir (Path, optional): The directory containing log files. Defaults to LOG_DIR.
    """
    log_dir = log_dir or LOG_DIR

    # Ensure log directory exists (though clearing implies it might)
    log_dir.mkdir(parents=True, exist_ok=True)

    # Files to manage
    log_files_to_clear = [log_dir / f"{logger_name}.log" for logger_name in logger_names]
    log_files_to_clear.append(log_dir / "root.log") # Add root log file

    # Clear existing log files in the directory managed by this config
    for log_file in log_files_to_clear:
        if log_file.exists():
            try:
                log_file.unlink()
                print(f"Removed log file: {log_file}")
            except OSError as e:
                print(f"Error removing log file {log_file}: {e}")
        # Optionally, create the log file to ensure it exists after clearing
        # log_file.touch() # Removed touch to just clear

def get_logging_config(logger_names, file_log_level=None, console_log_level=None, log_dir=None):
    """
    Generates Django LOGGING configuration dynamically.

    Args:
        logger_names (list): A list of strings, each being a logger name.
        file_log_level (str, optional): The default log level for file handlers. Defaults to DEFAULT_FILE_LOG_LEVEL.
        console_log_level (str, optional): The default log level for the console handler. Defaults to DEFAULT_CONSOLE_LOG_LEVEL.
        log_dir (Path, optional): The directory to store log files. Defaults to LOG_DIR.

    Returns:
        dict: The Django LOGGING configuration dictionary.
    """
    file_log_level = file_log_level or os.environ.get("FILE_LOG_LEVEL", DEFAULT_FILE_LOG_LEVEL)
    console_log_level = console_log_level or os.environ.get("CONSOLE_LOG_LEVEL", DEFAULT_CONSOLE_LOG_LEVEL)
    log_dir = log_dir or LOG_DIR

    # Ensure log directory exists
    log_dir.mkdir(parents=True, exist_ok=True)

    handlers = {
        'console': {
            'level': console_log_level, # Use console level
            'class': 'logging.StreamHandler',
            'formatter': 'standard',
        },
        'file_root': { # Handler for the root logger's file
            'level': file_log_level, # Use file level
            'class': 'logging.FileHandler',
            'filename': log_dir / "root.log",
            'formatter': 'standard',
        },
    }
    loggers = {
        # Root logger configuration - logs INFO+ to file, WARNING+ to console
        '': {
            'handlers': ['console', 'file_root'], # Use both handlers
            'level': file_log_level, # Set to lowest level needed (INFO for file)
            'propagate': False, # Root logger doesn't propagate further
        },
         # Django's default logger
        'django': {
            'handlers': ['console', 'file_root'], # Log to console and root file
            'level': file_log_level, # Or your preferred level, ensure it's captured by root file
            'propagate': False, # Don't propagate Django logs to avoid double handling by root
        },
    }

    # Dynamically create file handlers and logger configurations
    for name in logger_names:
        handler_name = f"file_{name}"
        handlers[handler_name] = {
            'level': file_log_level, # Use file level
            'class': 'logging.FileHandler',
            'filename': log_dir / f"{name}.log",
            'formatter': 'standard',
        }
        loggers[name] = {
            'handlers': [handler_name], # Log only to its own file directly
            'level': file_log_level, # Use file level
            'propagate': True, # Propagate to root logger (which handles console and root.log)
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
    log_directory = Path("data/logs_test") # Use a test directory

    # Optionally clear logs before configuring logging
    print("Clearing logs...")
    clear_log_files(test_loggers, log_dir=log_directory)
    print("Log clearing complete.")

    # Get logging configuration
    logging_config = get_logging_config(test_loggers, file_log_level="DEBUG", console_log_level="WARNING", log_dir=log_directory)
    import json
    print("\nGenerated Logging Configuration:")
    print(json.dumps(logging_config, indent=4))

    # Example of how to use it with Python's logging
    # import logging.config
    # logging.config.dictConfig(logging_config)
    # logger1 = logging.getLogger("app1")
    # logger1.debug("This is a debug message for app1.")
    # logger1.info("This is an info message for app1.")

    # logger_db = logging.getLogger("database")
    # logger_db.warning("This is a warning for database.")

    # root_logger = logging.getLogger()
    # root_logger.info("This is an info message from root.") # Should go to root.log and console (if level >= WARNING)
    # root_logger.warning("This is a warning message from root.") # Should go to root.log and console
