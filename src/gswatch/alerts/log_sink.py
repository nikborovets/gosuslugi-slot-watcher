"""Вывод в лог. Единственный канал, включённый всегда."""

from __future__ import annotations

import logging

from .base import Alert, Sink, format_message

log = logging.getLogger("gswatch.alerts")


def make_log_sink(max_slots: int) -> Sink:
    def sink(alert: Alert) -> None:
        level = (
            logging.WARNING
            if alert.kind in ("session_lost", "error")
            else logging.INFO
        )
        log.log(level, format_message(alert, max_slots))

    sink.__name__ = "log_sink"
    return sink
