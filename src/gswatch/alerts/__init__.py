"""Каналы уведомлений.

Каждый канал — отдельный модуль. Чтобы добавить свой (почта, SMS, звонок),
напишите функцию от одного Alert и подключите её в build_sinks().
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import Alert, Sink, Slot, dispatch, format_message
from .command import make_command_sink
from .log_sink import make_log_sink
from .macos import make_macos_sink
from .messages import make_messages_sink
from .telegram import make_telegram_sink
from .vk import make_vk_sink

if TYPE_CHECKING:
    from ..config import Config

log = logging.getLogger("gswatch.alerts")

__all__ = [
    "Alert",
    "Sink",
    "Slot",
    "build_sinks",
    "dispatch",
    "format_message",
    "make_command_sink",
    "make_log_sink",
    "make_macos_sink",
    "make_messages_sink",
    "make_telegram_sink",
    "make_vk_sink",
]


def mask(value: str) -> str:
    """Скрыть середину идентификатора: +70001234510 → +7••••••••10.

    В стартовую сводку попадают номер телефона и id чатов. Узнать по ней «тот
    ли адресат» надо, а показывать целиком — нет: логом делятся, когда просят
    помочь с настройкой.
    """
    if len(value) <= 4:
        return "•" * len(value)
    return f"{value[:2]}{'•' * (len(value) - 4)}{value[-2:]}"


def build_sinks(cfg: "Config") -> list[Sink]:
    """Собрать список каналов по настройкам.

    Канал подключается, только если он включён рубильником И заполнены его
    параметры. Про каждый выключенный пишем, по какой из двух причин он молчит:
    «выключен» и «не настроен» — разные вещи, и путать их дорого.

    Лог включён всегда: он остаётся, даже если все остальные каналы отвалятся.
    Порядок важен — сначала то, что работает без сети.
    """
    sinks: list[Sink] = [make_log_sink(cfg.slots_in_message)]
    channels = cfg.channels

    def report(name: str, on: bool, configured: bool, detail: str) -> bool:
        if not on:
            log.info("%-9s выключен настройкой", name)
            return False
        if not configured:
            log.info("%-9s не настроен (%s)", name, detail)
            return False
        log.info("%-9s включён: %s", name, detail)
        return True

    if report(
        "Мак", channels.mac, cfg.mac_alerts.enabled, cfg.mac_alerts.summary()
    ):
        sinks.append(make_macos_sink(cfg.mac_alerts, cfg.slots_in_message))

    if report(
        "Messages",
        channels.messages,
        bool(cfg.messages_to),
        f"{cfg.messages_service} → {mask(cfg.messages_to)}"
        if cfg.messages_to
        else "пуст GSWATCH_MESSAGES_TO",
    ):
        sinks.append(
            make_messages_sink(
                cfg.messages_to, cfg.slots_in_message, cfg.messages_service
            )
        )

    if report(
        "Telegram",
        channels.telegram,
        cfg.telegram_enabled,
        f"чат {mask(cfg.tg_chat_id)}" if cfg.telegram_enabled else "нет токена или чата",
    ):
        sinks.append(
            make_telegram_sink(cfg.tg_token, cfg.tg_chat_id, cfg.slots_in_message)
        )

    if report(
        "ВК",
        channels.vk,
        cfg.vk_enabled,
        f"peer {mask(cfg.vk_peer_id)}" if cfg.vk_enabled else "нет токена или peer_id",
    ):
        sinks.append(make_vk_sink(cfg.vk_token, cfg.vk_peer_id, cfg.slots_in_message))

    if len(sinks) == 1:
        log.warning(
            "Кроме лога не включён ни один канал — о слоте вы узнаете, только "
            "если сами заглянете в вывод"
        )
    return sinks
