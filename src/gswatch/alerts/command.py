"""Отдать алерт внешнему скрипту — JSON в stdin.

Способ прикрутить что угодно, не трогая gswatch: свой звук, почту, телефонный
звонок через российский шлюз, запись в базу.
"""

from __future__ import annotations

import json
import logging
import subprocess

from ..constants import COMMAND_SINK_TIMEOUT_SEC
from .base import Alert, Sink

log = logging.getLogger("gswatch.alerts")


def make_command_sink(command: list[str]) -> Sink:
    def sink(alert: Alert) -> None:
        payload = json.dumps(
            {
                "kind": alert.kind,
                "title": alert.title,
                "body": alert.body,
                "office": alert.office_label,
                "booking_url": alert.booking_url,
                "slots": [s.visit_time for s in alert.slots],
            },
            ensure_ascii=False,
        )
        try:
            subprocess.run(
                command,
                input=payload,
                text=True,
                timeout=COMMAND_SINK_TIMEOUT_SEC,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("внешний обработчик %r не отработал: %s", command, exc)

    sink.__name__ = "command_sink"
    return sink
