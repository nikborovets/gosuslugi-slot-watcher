"""Цикл опроса: раз в ~period_min ± jitter проверяем слоты по всем отделениям."""

from __future__ import annotations

import argparse
import logging
import os
import random
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .alerts import Alert, Slot, build_sinks, dispatch
from .browser import SessionLost, Watcher
from .config import Config, ConfigError, Office, load_config
from .constants import (
    LOG_FILE_BACKUPS,
    LOG_FILE_MAX_BYTES,
    MIN_DELAY_SEC,
    SLOT_ID_KEY,
    SLOT_TIME_KEY,
)
from .selftest import run_selftest

log = logging.getLogger("gswatch")


def attach_file_log(log_file: Path | None) -> None:
    """Подключить запись лога в файл с ротацией, если путь задан.

    Файл получает права 600, а его каталог 700: в лог попадают номер заявки
    (в адресе страницы записи) и названия отделений — те самые данные, что мы
    прячем в остальных местах. Формат с полной датой, в отличие от консольного:
    файл живёт сутками, и время без даты в нём бесполезно.

    Ошибку открытия глотаем с предупреждением: не писать в файл — не повод не
    работать, консоль остаётся.
    """
    if log_file is None:
        return
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(log_file.parent, 0o700)
        # Создаём файл заранее с правами 600, до того как в него что-то уйдёт:
        # RotatingFileHandler открыл бы его с 0644 — читаемым другими.
        if not log_file.exists():
            os.close(os.open(log_file, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600))
        else:
            os.chmod(log_file, 0o600)
        handler = RotatingFileHandler(
            log_file,
            maxBytes=LOG_FILE_MAX_BYTES,
            backupCount=LOG_FILE_BACKUPS,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-7s %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logging.getLogger().addHandler(handler)
        log.info("лог пишется в %s", log_file)
    except OSError as exc:
        log.warning("не удалось открыть лог-файл %s: %s — пишу только в консоль", log_file, exc)


def started_alert(cfg: Config) -> Alert:
    """Сводка при запуске: заодно сразу видно, что каналы настроены верно,
    а не выяснится через месяц, когда появится слот."""
    return Alert(
        kind="started",
        title="▶️ Сторож запущен",
        body="\n".join(
            [
                f"Заявка {cfg.order_id}, загранпаспорт {cfg.passport_label}.",
                f"Проверка раз в ~{cfg.period_min:.0f} мин (±{cfg.jitter_min:.0f}).",
                "",
                "Слежу за отделениями:",
                *(f"• {office.label}" for office in cfg.offices),
            ]
        ),
    )


def session_lost_alert(reason: str) -> Alert:
    return Alert(
        kind="session_lost",
        title="⚠️ Сессия Госуслуг протухла",
        body=f"{reason}. Войдите заново в открытом окне — жду.",
    )


class SlotMonitor:
    def __init__(self, cfg: Config, sinks: list) -> None:
        self.cfg = cfg
        self.sinks = sinks
        self._had_slots: dict[str, bool] = {}
        self._last_alert_at: dict[str, float] = {}

    def _should_alert(self, office: Office) -> bool:
        """Алертим на появление слотов и потом не чаще repeat_alert_min."""
        if not self._had_slots.get(office.code_frgu, False):
            return True
        last = self._last_alert_at.get(office.code_frgu, 0.0)
        return time.monotonic() - last > self.cfg.repeat_alert_min * 60

    def check_office(self, watcher: Watcher, office: Office) -> None:
        response = watcher.fetch_slots(office)
        if response.status != 200:
            dispatch(
                self.sinks,
                Alert(
                    kind="error",
                    title=f"HTTP {response.status} по отделению «{office.label}»",
                    office_label=office.label,
                ),
            )
            return

        raw = response.slots
        if not raw:
            log.info("%s — слотов нет", office.label)
            self._had_slots[office.code_frgu] = False
            return

        slots = tuple(
            Slot(
                slot_id=str(s.get(SLOT_ID_KEY, "")),
                visit_time=str(s.get(SLOT_TIME_KEY, "")),
            )
            for s in raw
        )
        if self._should_alert(office):
            dispatch(
                self.sinks,
                Alert(
                    kind="slots",
                    title=f"🟢 Слоты: «{office.label}» — свободно {len(slots)}",
                    office_label=office.label,
                    slots=slots,
                    booking_url=self.cfg.booking_page,
                ),
            )
            self._last_alert_at[office.code_frgu] = time.monotonic()
        else:
            log.info(
                "%s — слоты есть (%d), но алерт был недавно", office.label, len(slots)
            )
        self._had_slots[office.code_frgu] = True

    def run_once(self, watcher: Watcher) -> None:
        for office in self.cfg.offices:
            try:
                self.check_office(watcher, office)
            except SessionLost:
                raise
            except Exception as exc:  # noqa: BLE001 — одно отделение не валит цикл
                log.warning("%s — сбой проверки: %s", office.label, exc)
            # Не выпускаем все запросы одной пачкой.
            time.sleep(
                random.uniform(
                    self.cfg.office_pause_min_sec, self.cfg.office_pause_max_sec
                )
            )

    def next_delay(self) -> float:
        spread = random.uniform(-1.0, 1.0) * self.cfg.jitter_min
        return max(MIN_DELAY_SEC, (self.cfg.period_min + spread) * 60.0)


def run(cfg: Config | None = None, sinks: list | None = None) -> None:
    cfg = cfg or load_config()
    attach_file_log(cfg.log_file)
    sinks = sinks if sinks is not None else build_sinks(cfg)
    monitor = SlotMonitor(cfg, sinks)

    with Watcher(cfg) as watcher:
        watcher.open_booking_page()
        watcher.wait_for_login()

        dispatch(sinks, started_alert(cfg))
        while True:
            try:
                monitor.run_once(watcher)
            except SessionLost as exc:
                dispatch(sinks, session_lost_alert(str(exc)))
                watcher.open_booking_page()
                watcher.wait_for_login()
                continue

            # Сохраняем куки каждый цикл: если процесс убьют жёстко,
            # __exit__ не отработает и сессионные куки пропадут.
            watcher.save_state()

            delay = monitor.next_delay()
            log.info("следующая проверка через %.1f мин", delay / 60)
            time.sleep(delay)


def cli() -> None:
    parser = argparse.ArgumentParser(
        prog="gswatch",
        description="Сторож свободных слотов на подачу на загранпаспорт (ЕПГУ).",
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="прогнать все уведомления на подставных данных, "
        "не обращаясь к Госуслугам: SMS и Telegram уйдут по-настоящему",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    try:
        if args.selftest:
            cfg = load_config()
            attach_file_log(cfg.log_file)
            run_selftest(cfg, build_sinks(cfg))
        else:
            run()
    except ConfigError as exc:
        log.error("Настройки: %s", exc)
        raise SystemExit(2) from exc
    except KeyboardInterrupt:
        log.info("остановлено")


if __name__ == "__main__":
    cli()
