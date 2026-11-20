import os
from pathlib import Path
from typing import Literal, TypedDict

from endoreg_db.utils.file_operations import ensure_directory
from endoreg_db.utils.paths import get_runtime_paths

DEFAULT_FILE_LOG_LEVEL = "INFO"
DEFAULT_CONSOLE_LOG_LEVEL = "WARNING"


class _FormatterConfig(TypedDict):
    format: str


_FileHandlerConfig = TypedDict(
    "_FileHandlerConfig",
    {
        "level": str,
        "class": str,
        "filename": Path,
        "formatter": str,
    },
)


_ConsoleHandlerConfig = TypedDict(
    "_ConsoleHandlerConfig",
    {
        "level": str,
        "class": str,
        "formatter": str,
    },
)


class _LoggerConfig(TypedDict):
    handlers: list[str]
    level: str
    propagate: bool


type _HandlerConfig = _FileHandlerConfig | _ConsoleHandlerConfig
type _FormatterConfigMap = dict[str, _FormatterConfig]
type _HandlerConfigMap = dict[str, _HandlerConfig]
type _LoggerConfigMap = dict[str, _LoggerConfig]


class LoggingConfig(TypedDict):
    version: Literal[1]
    disable_existing_loggers: bool
    formatters: _FormatterConfigMap
    handlers: _HandlerConfigMap
    loggers: _LoggerConfigMap


def _file_handler_config(
    *,
    level: str,
    filename: Path,
    formatter: str,
) -> _FileHandlerConfig:
    return {
        "level": level,
        "class": "logging.FileHandler",
        "filename": filename,
        "formatter": formatter,
    }


def _console_handler_config(
    *,
    level: str,
    formatter: str,
) -> _ConsoleHandlerConfig:
    return {
        "level": level,
        "class": "logging.StreamHandler",
        "formatter": formatter,
    }


def get_logging_config(
    logger_names: list[str],
    file_log_level: str | None = None,
    console_log_level: str | None = None,
    log_dir: Path | None = None,
) -> LoggingConfig:
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
    file_log_level = file_log_level or os.environ.get(
        "FILE_LOG_LEVEL", DEFAULT_FILE_LOG_LEVEL
    )
    console_log_level = console_log_level or os.environ.get(
        "CONSOLE_LOG_LEVEL", DEFAULT_CONSOLE_LOG_LEVEL
    )
    log_dir = log_dir or get_runtime_paths().logs

    # Ensure log directory exists
    ensure_directory(log_dir)

    handlers: _HandlerConfigMap = {
        "console": _console_handler_config(
            level=console_log_level,
            formatter="standard",
        ),
        "file_root": _file_handler_config(
            level=file_log_level,
            filename=log_dir / "root.log",
            formatter="standard",
        ),
    }
    loggers: _LoggerConfigMap = {
        # Root logger configuration - logs INFO+ to file, WARNING+ to console
        "": {
            "handlers": ["console", "file_root"],  # Use both handlers
            "level": file_log_level,  # Set to lowest level needed (INFO for file)
            "propagate": False,  # Root logger doesn't propagate further
        },
        # Django's default logger
        "django": {
            "handlers": ["console", "file_root"],  # Log to console and root file
            "level": file_log_level,  # Or your preferred level, ensure it's captured by root file
            "propagate": False,  # Don't propagate Django logs to avoid double handling by root
        },
    }

    # Dynamically create file handlers and logger configurations
    for name in logger_names:
        handler_name = f"file_{name}"
        handlers[handler_name] = _file_handler_config(
            level=file_log_level,
            filename=log_dir / f"{name}.log",
            formatter="standard",
        )
        loggers[name] = {
            "handlers": [handler_name],  # Log only to its own file directly
            "level": file_log_level,  # Use file level
            "propagate": True,  # Propagate to root logger (which handles console and root.log)
        }

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            },
        },
        "handlers": handlers,
        "loggers": loggers,
    }
