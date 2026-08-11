"""Отправка через Telegram Bot API.

Осторожно: доступность api.telegram.org из России непостоянна. Канал полезен,
но полагаться на него как на единственный не стоит — для этого есть локальный
сигнал на Маке, которому сеть не нужна вовсе.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from ..constants import (
    TELEGRAM_API_TEMPLATE,
    TELEGRAM_SAFE_LIMIT,
    TELEGRAM_TIMEOUT_SEC,
)
from .base import Alert, Sink, format_message

log = logging.getLogger("gswatch.alerts")


def make_telegram_sink(token: str, chat_ids: tuple[str, ...], max_slots: int) -> Sink:
    """Слать уведомление в каждый из chat_ids.

    Без parse_mode: адреса отделений содержат точки, скобки и кавычки, которые
    Markdown-разметка Telegram воспринимает как синтаксис и отвечает 400.
    Обычный текст ничего экранировать не требует.

    Каждому чату — отдельным запросом: недоступный адресат или ошибка на нём
    (частая — «chat not found», если человек не написал боту первым) не должны
    глушить остальных.
    """
    url = TELEGRAM_API_TEMPLATE.format(token=token)

    def sink(alert: Alert) -> None:
        text = format_message(alert, max_slots, TELEGRAM_SAFE_LIMIT)
        for chat_id in chat_ids:
            payload = urllib.parse.urlencode(
                {
                    "chat_id": chat_id,
                    "text": text,
                    "disable_web_page_preview": "true",
                }
            ).encode("utf-8")
            request = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=TELEGRAM_TIMEOUT_SEC
                ) as response:
                    body = json.loads(response.read())
                if not body.get("ok"):
                    log.warning(
                        "Telegram отказал (чат %s): %s",
                        chat_id,
                        body.get("description"),
                    )
            except urllib.error.HTTPError as exc:
                # Телеграм объясняет причину в теле ответа — без него ошибка немая.
                detail = exc.read().decode("utf-8", "replace")[:300]
                log.warning("Telegram HTTP %s (чат %s): %s", exc.code, chat_id, detail)
            except OSError as exc:
                # Сеть отвалилась или запрос не прошёл — не повод ронять сторожа.
                log.warning("Telegram недоступен (чат %s): %s", chat_id, exc)

    sink.__name__ = "telegram_sink"
    return sink
