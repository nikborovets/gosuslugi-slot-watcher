"""Отправка через Messages.app — по iMessage или обычной SMS.

Служба выбирается явно и откатов не делает. Это важно: AppleScript, в отличие
от интерфейса приложения, НЕ умеет сам переслать по SMS то, что не ушло по
iMessage. Просили iMessage, а номер в нём не зарегистрирован — сообщение просто
повиснет красным «Not Delivered». Проверено вживую.

Какую службу выбирать:

* iMessage — если получатель точно в iMessage. Но отправка самому себе почти
  бесполезна для тревоги: сообщение синхронизируется как исходящее, а не
  входящее, поэтому баннера и звука на телефоне может не быть;
* SMS — сообщение уходит через iPhone по «Пересылке SMS» и приходит на телефон
  как настоящее входящее, со звуком. Требует: Настройки → Сообщения →
  Пересылка SMS → разрешить этот Mac. Бонусом доходит и без интернета
  на телефоне.

Ограничение обеих служб: доставку отсюда проверить нельзя. AppleScript ставит
сообщение в очередь и возвращает успех — на несуществующем номере returncode
тоже остаётся 0. В лог попадёт лишь отказ самого скрипта: нет разрешения на
автоматизацию (-1743) или запрошенная служба вообще не настроена.

Текст передаётся аргументом, а не подстановкой в текст скрипта: иначе кавычки
и переводы строк в адресах отделений ломали бы AppleScript.
"""

from __future__ import annotations

import logging
import subprocess

from ..constants import MESSAGES_SERVICES, MESSAGES_TIMEOUT_SEC
from .base import Alert, Sink, format_message

log = logging.getLogger("gswatch.alerts")

# service type в AppleScript — константа без кавычек, поэтому подставляется
# в текст скрипта. Значение приходит только из MESSAGES_SERVICES.
_SCRIPT_TEMPLATE = """
on run argv
    set recipientId to item 1 of argv
    set messageText to item 2 of argv
    tell application "Messages"
        set targetService to 1st account whose service type = {service}
        set targetBuddy to participant recipientId of targetService
        send messageText to targetBuddy
    end tell
end run
"""


def make_messages_sink(recipient: str, max_slots: int, service: str) -> Sink:
    """recipient — номер телефона (+7…) или Apple ID; service — iMessage или SMS."""
    script = _SCRIPT_TEMPLATE.format(service=MESSAGES_SERVICES[service])

    def sink(alert: Alert) -> None:
        text = format_message(alert, max_slots)
        try:
            result = subprocess.run(
                ["osascript", "-", recipient, text],
                input=script,
                capture_output=True,
                text=True,
                timeout=MESSAGES_TIMEOUT_SEC,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("Messages: не отправлено (%s): %s", service, exc)
            return
        if result.returncode != 0:
            detail = result.stderr.strip()[:300]
            if "-1743" in detail:
                detail += (
                    " — разрешите управление Messages в Настройках → "
                    "Конфиденциальность и безопасность → Автоматизация"
                )
            elif "Can’t get account" in detail or "Can't get account" in detail:
                detail += (
                    f" — служба {service} не настроена; для SMS включите на iPhone "
                    "Настройки → Сообщения → Пересылка SMS"
                )
            log.warning("Messages: не отправлено (%s): %s", service, detail)

    sink.__name__ = f"messages_sink[{service}]"
    return sink
