"""Боевая репетиция: прогнать все уведомления, не трогая Госуслуги.

Отвечает на вопрос «а точно ли я узна́ю, когда появится слот» — не дожидаясь,
пока он появится на самом деле. Каналы работают настоящие: SMS придёт на
телефон, Telegram в чат, Мак зашумит. Подставлены только ответы портала.

Прогоняется не копия логики, а тот же SlotMonitor, что и в боевом режиме, —
иначе проверка проверяла бы саму себя.

Запуск: gswatch --selftest
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .alerts import Sink, dispatch
from .browser import SessionLost, SlotsResponse
from .config import Config, Office
from .constants import SLOT_ID_KEY, SLOT_TIME_KEY

log = logging.getLogger("gswatch.selftest")

# Пауза между сценами, чтобы уведомления не слиплись в одну кучу и было видно,
# какое от чего.
SCENE_PAUSE_SEC = 3.0


@dataclass
class _ScriptedWatcher:
    """Подставной Watcher: отдаёт заранее заготовленные ответы вместо портала."""

    responses: list[SlotsResponse | Exception]

    def fetch_slots(self, office: Office) -> SlotsResponse:
        item = self.responses.pop(0) if self.responses else SlotsResponse(200, {})
        if isinstance(item, Exception):
            raise item
        return item


def _fake_slots(count: int) -> SlotsResponse:
    """Ответ портала с count свободными слотами, по форме — как настоящий."""
    base = time.localtime()
    slots = [
        {
            SLOT_ID_KEY: f"selftest-{i}",
            SLOT_TIME_KEY: f"{base.tm_year}-08-{i + 4:02d}T1{i % 8}:30:00+03:00",
        }
        for i in range(count)
    ]
    return SlotsResponse(status=200, payload={"slots": slots})


def _scene(number: int, title: str, expect: str) -> None:
    log.info("")
    log.info("─" * 62)
    log.info("СЦЕНА %d. %s", number, title)
    log.info("Чего ждать: %s", expect)
    log.info("─" * 62)


def run_selftest(cfg: Config, sinks: list[Sink]) -> None:
    from .main import SlotMonitor, session_lost_alert, started_alert

    office = cfg.offices[0]
    channels = ", ".join(getattr(s, "__name__", str(s)) for s in sinks)
    log.info("Самопроверка. Госуслуги не опрашиваются, каналы настоящие.")
    log.info("Задействованы: %s", channels)
    log.info("Отделение для примера: %s", office.label)

    _scene(1, "Запуск сторожа", "то же сообщение, что при обычном старте")
    dispatch(sinks, started_alert(cfg))
    time.sleep(SCENE_PAUSE_SEC)

    monitor = SlotMonitor(cfg, sinks)

    _scene(2, "Слотов нет", "тишина: это штатный ответ, а не событие")
    monitor.check_office(_ScriptedWatcher([SlotsResponse(200, {"slots": []})]), office) # type: ignore
    time.sleep(SCENE_PAUSE_SEC)

    _scene(3, "Появились слоты", "ГРОМКИЙ сигнал на Маке, SMS, Telegram")
    monitor.check_office(_ScriptedWatcher([_fake_slots(3)]), office) # type: ignore
    time.sleep(SCENE_PAUSE_SEC)

    _scene(
        4,
        "Слоты всё ещё есть",
        f"тишина: повтор подавляется {cfg.repeat_alert_min:.0f} мин",
    )
    monitor.check_office(_ScriptedWatcher([_fake_slots(3)]), office) # type: ignore
    time.sleep(SCENE_PAUSE_SEC)

    _scene(5, "Портал ответил ошибкой", "тихое уведомление про HTTP 500")
    monitor.check_office(_ScriptedWatcher([SlotsResponse(500, None)]), office) # type: ignore
    time.sleep(SCENE_PAUSE_SEC)

    _scene(6, "Сессия протухла", "тихое уведомление: нужно войти заново")
    dispatch(sinks, session_lost_alert("HTTP 401 от /equeue/agg/slots"))
    time.sleep(SCENE_PAUSE_SEC)

    _scene(7, "Сбой на одном отделении", "предупреждение в лог, цикл не падает")
    try:
        monitor.run_once(_ScriptedWatcher([RuntimeError("сеть отвалилась")])) # type: ignore
    except SessionLost:
        log.error("SessionLost не должен был всплыть здесь")

    log.info("")
    log.info("Самопроверка закончена.")
    log.info("Сверьте: пришли ли SMS и Telegram по сценам 1, 3, 5, 6;")
    log.info("шумел ли Мак только в сцене 3; молчал ли он в сценах 2 и 4.")
