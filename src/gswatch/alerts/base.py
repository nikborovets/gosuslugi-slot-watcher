"""Общие типы уведомлений и рассылка по каналам.

Канал (Sink) — это функция от одного Alert. Ядро мониторинга не знает, куда
уходят уведомления, а каналы не знают, откуда взялся Alert.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Slot:
    """Один свободный слот из ответа /equeue/agg/slots."""

    slot_id: str
    visit_time: str  # ISO-8601 с таймзоной, напр. "2026-08-04T19:00:00+03:00"

    @property
    def human_time(self) -> str:
        try:
            return datetime.fromisoformat(self.visit_time).strftime("%d.%m.%Y %H:%M")
        except ValueError:
            return self.visit_time


@dataclass(frozen=True)
class Alert:
    """Событие, достойное внимания человека."""

    kind: str  # "started" | "slots" | "session_lost" | "error"
    title: str
    body: str = ""
    office_label: str = ""
    slots: tuple[Slot, ...] = field(default_factory=tuple)
    booking_url: str = ""


class Sink(Protocol):
    """Канал доставки. Не должен бросать исключения наружу.

    Аргумент позиционный: так протоколу соответствует любая функция от одного
    Alert, как бы она свой параметр ни назвала.
    """

    def __call__(self, alert: Alert, /) -> None: ...


def format_message(alert: Alert, max_slots: int, max_chars: int | None = None) -> str:
    """Собрать текст уведомления. Общий для всех текстовых каналов.

    max_slots — сколько слотов перечислять: при полусотне свободных мест список
    бесполезен, важен сам факт и ближайшие даты.
    max_chars — обрезать длинное сообщение (у Telegram и SMS свои лимиты).
    """
    lines = [alert.title]
    if alert.body:
        lines.append(alert.body)
    if alert.slots:
        lines.append("")
        for slot in alert.slots[:max_slots]:
            lines.append(f"• {slot.human_time}")
        if len(alert.slots) > max_slots:
            lines.append(f"… и ещё {len(alert.slots) - max_slots}")
    if alert.booking_url:
        lines.append("")
        lines.append(f"Записаться: {alert.booking_url}")

    text = "\n".join(lines)
    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars] + "\n…"
    return text


def dispatch(sinks: list[Sink], alert: Alert) -> None:
    """Разослать алерт по всем каналам. Падение одного не ломает остальные."""
    for sink in sinks:
        try:
            sink(alert)
        except Exception:  # noqa: BLE001 — канал доставки не должен ронять сторожа
            log.exception("канал уведомлений %r упал", getattr(sink, "__name__", sink))
