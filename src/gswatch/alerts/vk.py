"""Уведомления во ВКонтакте.

Зачем: сервис российский, блокировки и VPN его не касаются — в отличие от
Telegram, который из России доступен непостоянно.

Что нужно, чтобы включить:

1. создать сообщество (группу) ВК — писать от имени личной страницы нельзя,
   API для этого закрыт;
2. Управление → Работа с API → создать ключ доступа с правом «Сообщения»;
3. Управление → Сообщения → включить сообщения сообщества;
4. написать сообществу что-нибудь со своей страницы, иначе оно не имеет права
   писать вам первым (ограничение то же, что у Telegram);
5. peer_id — ваш числовой id ВКонтакте (https://vk.com/id0 (редирект на вас)).
"""

from __future__ import annotations

import json
import logging
import random
import urllib.error
import urllib.parse
import urllib.request

from ..constants import VK_API_URL, VK_API_VERSION, VK_TIMEOUT_SEC
from .base import Alert, Sink, format_message

log = logging.getLogger("gswatch.alerts")


def make_vk_sink(token: str, peer_id: str, max_slots: int) -> Sink:
    def sink(alert: Alert) -> None:
        payload = urllib.parse.urlencode(
            {
                "access_token": token,
                "v": VK_API_VERSION,
                "peer_id": peer_id,
                "message": format_message(alert, max_slots),
                # random_id — защита ВК от дублей: одинаковый id за короткий
                # промежуток отбрасывается как повтор.
                "random_id": random.getrandbits(31),
            }
        ).encode("utf-8")
        try:
            with urllib.request.urlopen(
                urllib.request.Request(VK_API_URL, data=payload),
                timeout=VK_TIMEOUT_SEC,
            ) as response:
                body = json.loads(response.read())
        except (OSError, urllib.error.HTTPError, ValueError) as exc:
            log.warning("ВК недоступен: %s", exc)
            return
        # ВК отвечает 200 даже на ошибку, причина — в поле "error".
        if "error" in body:
            log.warning(
                "ВК отказал: %s", body["error"].get("error_msg", body["error"])
            )

    sink.__name__ = "vk_sink"
    return sink
