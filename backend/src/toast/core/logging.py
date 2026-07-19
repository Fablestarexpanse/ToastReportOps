"""Structured JSON logging with secret scrubbing.

No secret value may ever reach a log line: any event key whose name matches
SECRET_PATTERN has its value redacted before rendering.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

SECRET_PATTERN = re.compile(
    r"(secret|token|password|passwd|credential|apikey|_key$|^key$)", re.IGNORECASE
)
REDACTED = "[REDACTED]"


def scrub_secrets(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in event_dict:
        if SECRET_PATTERN.search(key):
            event_dict[key] = REDACTED
    return event_dict


def configure_logging(level: str = "INFO", service: str = "toast") -> None:
    logging.basicConfig(
        format="%(message)s", stream=sys.stdout, level=getattr(logging, level.upper(), logging.INFO)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            scrub_secrets,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(service=service)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
