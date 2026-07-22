"""Громкий сигнал на самом Маке.

Самый надёжный канал: сеть не нужна вообще, блокировки и VPN ни при чём.
Скрипт всё равно обязан крутиться на этой машине (там окно Chrome с сессией),
поэтому в момент алерта Мак гарантированно включён и рядом.

Всё выполняется в фоновом потоке: модальное окно ждёт нажатия кнопки, а цикл
опроса ждать не должен.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from ..constants import (
    LOUD_ALERT_KINDS,
    MAC_SOUNDS_DIR,
    MAC_WAKE_SEC,
    OSASCRIPT_TIMEOUT_SEC,
)
from .base import Alert, Sink, format_message

if TYPE_CHECKING:
    from ..config import MacAlertOptions

log = logging.getLogger("gswatch.alerts")


def sound_path(name: str) -> Path:
    return MAC_SOUNDS_DIR / f"{name}.aiff"


# Данные передаются в AppleScript аргументами (`on run argv`), а не склейкой
# строк. Адреса отделений и всё, что пришло от портала, — недоверенный ввод:
# подстановка его в текст скрипта даёт ту же инъекцию, что и склейка SQL, и
# экранированием кавычек она не лечится.
_NOTIFICATION_SCRIPT = """
on run argv
    display notification (item 2 of argv) with title (item 1 of argv)
end run
"""

_ALERT_SCRIPT = """
on run argv
    display alert (item 1 of argv) message (item 2 of argv) buttons {item 3 of argv} default button (item 3 of argv)
end run
"""

_GET_VOLUME_SCRIPT = """
on run argv
    return output volume of (get volume settings)
end run
"""

_SET_VOLUME_SCRIPT = """
on run argv
    set volume output volume (item 1 of argv as integer)
end run
"""


def _osascript(script: str, *args: str) -> subprocess.CompletedProcess | None:
    """Выполнить AppleScript, передав данные позиционными аргументами."""
    try:
        return subprocess.run(
            ["osascript", "-", *args],
            input=script,
            capture_output=True,
            text=True,
            timeout=OSASCRIPT_TIMEOUT_SEC,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("osascript не отработал: %s", exc)
        return None


def _current_volume() -> int | None:
    result = _osascript(_GET_VOLUME_SCRIPT)
    if result is None or result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def _spoken_text(alert: Alert) -> str:
    """Короткая фраза для голоса: длинный текст вслух невыносим."""
    if alert.kind != "slots":
        return alert.title
    where = alert.office_label or "отделение"
    return f"Появились слоты. {where}. Свободно {len(alert.slots)}."


class MacNotifier:
    """Собирает громкий сигнал из включённых кусочков."""

    def __init__(self, opts: "MacAlertOptions", max_slots: int) -> None:
        self.opts = opts
        self.max_slots = max_slots

    def __call__(self, alert: Alert) -> None:
        if alert.kind in LOUD_ALERT_KINDS:
            # Фоновый поток: модалка блокирует до нажатия кнопки.
            threading.Thread(target=self._loud, args=(alert,), daemon=True).start()
        else:
            # Запуск, ошибки, протухшая сессия — это не повод будить дом.
            self._notification(alert)

    def _notification(self, alert: Alert) -> None:
        body = alert.body.splitlines()[0] if alert.body else ""
        _osascript(_NOTIFICATION_SCRIPT, alert.title, body)

    def _loud(self, alert: Alert) -> None:
        opts = self.opts
        restore_to: int | None = None

        if opts.wake_screen:
            # Гасим ожидание: caffeinate -u будит дисплей и держит его N секунд.
            try:
                subprocess.Popen(
                    ["caffeinate", "-u", "-t", str(MAC_WAKE_SEC)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as exc:
                log.warning("caffeinate не запустился: %s", exc)

        if opts.max_volume:
            # Запоминаем прежнюю громкость: выкрутить на максимум и оставить
            # так — свинство, вернём как было, когда отзвучит.
            restore_to = _current_volume()
            _osascript(_SET_VOLUME_SCRIPT, str(opts.volume_level))

        say_proc = None
        if opts.voice:
            try:
                say_proc = subprocess.Popen(
                    ["say", "-v", opts.voice, _spoken_text(alert)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as exc:
                log.warning("say не запустился: %s", exc)

        modal_proc = None
        if opts.modal:
            modal_proc = self._start_modal(alert)

        if opts.sound:
            self._play_loop(opts, modal_proc)

        # Ждём, пока человек закроет окно, и только потом возвращаем громкость.
        if modal_proc is not None:
            try:
                modal_proc.wait(timeout=opts.modal_timeout_sec)
            except subprocess.TimeoutExpired:
                modal_proc.kill()
        if say_proc is not None:
            try:
                say_proc.wait(timeout=OSASCRIPT_TIMEOUT_SEC)
            except subprocess.TimeoutExpired:
                say_proc.kill()

        if restore_to is not None:
            _osascript(_SET_VOLUME_SCRIPT, str(restore_to))

    def _start_modal(self, alert: Alert) -> subprocess.Popen | None:
        """Открыть окно, не дожидаясь нажатия: звук должен звучать параллельно.

        Скрипт отдаётся через stdin, а текст — аргументами, поэтому Popen, а не
        run: ждать здесь нельзя, но и склеивать строки тоже.
        """
        body = format_message(alert, self.max_slots)
        try:
            proc = subprocess.Popen(
                ["osascript", "-", alert.title, body, self.opts.modal_button],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except OSError as exc:
            log.warning("модальное окно не открылось: %s", exc)
            return None
        # Закрываем stdin — иначе osascript ждёт конца скрипта и не рисует окно.
        assert proc.stdin is not None
        proc.stdin.write(_ALERT_SCRIPT)
        proc.stdin.close()
        return proc

    def _play_loop(self, opts: "MacAlertOptions", modal_proc) -> None:
        """Пищим, пока не нажата кнопка или не кончились повторы.

        Одиночный сигнал легко проспать; бесконечный — невыносим, поэтому
        и то и другое ограничено sound_repeat.
        """
        path = sound_path(opts.sound)
        if not path.exists():
            log.warning("нет звука %s — пропускаю", path)
            return
        for _ in range(opts.sound_repeat):
            if modal_proc is not None and modal_proc.poll() is not None:
                break  # окно закрыли — сигнал принят
            try:
                subprocess.run(
                    ["afplay", str(path)],
                    timeout=OSASCRIPT_TIMEOUT_SEC,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                log.warning("afplay не отработал: %s", exc)
                return


def make_macos_sink(opts: "MacAlertOptions", max_slots: int) -> Sink:
    notifier = MacNotifier(opts, max_slots)

    def sink(alert: Alert) -> None:
        notifier(alert)

    sink.__name__ = "macos_sink"
    return sink
