"""Работа через настоящий Chrome с постоянным профилем.

Почему не httpx с выгруженными куками: ручка слотов пускает только по кукам
ЕПГУ (без них — 401), сессия короткая, а портал шлёт фингерпринт-телеметрию
(/api/mp-metrics/v1/gsm/raw-data). Запрос, выпущенный из самой страницы,
несёт подлинные UA, TLS-отпечаток, куки и заголовки — расходиться нечему.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from playwright.sync_api import Page, TimeoutError as PWTimeout, sync_playwright

from .config import Config, Office
from .constants import (
    CHECK_SESSION_URL,
    CHROME_ARGS,
    CHROME_CHANNEL,
    FORM_ID,
    HEADER_FORM_ID,
    HEADER_ORDER_ID,
    LOGIN_POLL_MS,
    SLOTS_URL,
    STATE_FILENAME,
)

log = logging.getLogger(__name__)

# Запрос выпускается из самой страницы, поэтому куки и отпечаток — браузерные.
# Имена заголовков приходят из constants.py: их навешивает интерцептор портала.
_FETCH_JS = """
async ({ url, body, headers }) => {
  let r;
  try {
    r = await fetch(url, {
      method: "POST",
      credentials: "include",
      headers: headers,
      body: JSON.stringify(body),
    });
  } catch (e) {
    // "Failed to fetch" значит, что запрос не ушёл вообще: разрыв сети,
    // уход страницы в навигацию, блокировка. HTTP-кода тут нет и быть не
    // может, поэтому возвращаем 0 и обстановку — по ней причина различима.
    return {
      status: 0,
      payload: null,
      error: String(e && e.message || e),
      href: location.href,
      online: navigator.onLine,
    };
  }
  let payload = null;
  try { payload = await r.json(); } catch (e) { payload = null; }
  return { status: r.status, payload };
}
"""


class SessionLost(Exception):
    """Портал ответил 401/403 — сессия протухла, нужен ручной вход."""


class RequestNotSent(Exception):
    """Запрос не покинул браузер: сеть, навигация или блокировка."""


@dataclass
class SlotsResponse:
    status: int
    payload: dict | None

    @property
    def slots(self) -> list[dict]:
        return (self.payload or {}).get("slots") or []


class Watcher:
    """Держит окно Chrome на странице записи и опрашивает через неё API."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._pw = None
        self._ctx = None
        self._page: Page | None = None

    def __enter__(self) -> "Watcher":
        self.cfg.profile_dir.mkdir(parents=True, exist_ok=True)
        # Внутри профиля лежат куки Госуслуг, то есть доступ к аккаунту.
        # mkdir создал бы каталог с 0755 — читаемым другими пользователями
        # машины; chmod применяем и к уже существующему каталогу.
        try:
            os.chmod(self.cfg.profile_dir, 0o700)
        except OSError as exc:
            log.warning("не удалось закрыть права на %s: %s", self.cfg.profile_dir, exc)
        self._pw = sync_playwright().start()
        # Окно видимое: в headless UA выдаёт HeadlessChrome. См. constants.py.
        self._ctx = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.cfg.profile_dir),
            channel=CHROME_CHANNEL,
            headless=False,
            viewport=None,
            args=list(CHROME_ARGS),
        )
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self._restore_state()
        return self

    def __exit__(self, exc_type: type[BaseException] | None, *_: object) -> None:
        """Свернуться, что бы ни случилось.

        Ctrl+C в терминале уходит всей группе процессов, то есть и самому
        Chrome. Пока Python добирается сюда, браузера может уже не быть, и
        тогда обращение к нему бросает — причём прямо во время разматывания
        стека, подменяя собой исходный KeyboardInterrupt. Ронять на этом выход
        нельзя: цикл сохраняет куки в конце каждой итерации, терять нечего, а
        traceback вместо «остановлено» пугает на ровном месте.
        """
        if self._ctx is not None:
            # На Ctrl+C не пытаемся: куки уже сохранены прошлым циклом, а
            # попытка лишь добавит тревожное предупреждение о мёртвом браузере.
            if exc_type is not KeyboardInterrupt:
                self.save_state()
            try:
                self._ctx.close()
            except Exception as exc:  # noqa: BLE001 — браузера могло уже не быть
                log.debug("Chrome закрылся сам: %s", exc)
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception as exc:  # noqa: BLE001 — то же самое
                log.debug("Playwright уже остановлен: %s", exc)

    @property
    def _state_path(self):
        return self.cfg.profile_dir / STATE_FILENAME

    def _restore_state(self) -> None:
        """Долить сессионные куки, которые профиль Chromium не сохраняет."""
        if not self._state_path.exists():
            return
        try:
            cookies = json.loads(self._state_path.read_text())["cookies"]
        except (OSError, ValueError, KeyError) as exc:
            log.warning("не читается %s: %s — потребуется вход заново", self._state_path, exc)
            return
        if cookies:
            self._ctx.add_cookies(cookies)
            log.info("восстановлено %d кук из прошлого запуска", len(cookies))

    def save_state(self) -> None:
        """Сохранить куки в файл с правами 600.

        Пишем во временный файл, созданный сразу с нужными правами, и только
        потом переименовываем. Если сначала дать Playwright писать по месту,
        а chmod делать после, останется окно, в котором доступ к аккаунту
        читаем всем на машине. Переименование внутри одного каталога атомарно,
        так что и оборванная запись не оставит покалеченный файл.
        """
        tmp_path = self._state_path.with_name(self._state_path.name + ".tmp")
        try:
            os.close(os.open(tmp_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600))
            self._ctx.storage_state(path=str(tmp_path))
            os.replace(tmp_path, self._state_path)
        except Exception as exc:  # noqa: BLE001 — не повод ронять сторожа
            log.warning("не удалось сохранить куки: %s", exc)
            tmp_path.unlink(missing_ok=True)

    @property
    def page(self) -> Page:
        assert self._page is not None, "Watcher используется вне контекстного менеджера"
        return self._page

    def open_booking_page(self) -> None:
        log.info("открываю %s", self.cfg.booking_page)
        self.page.goto(self.cfg.booking_page, wait_until="domcontentloaded")

    def wait_for_login(self) -> None:
        """Ждём, пока человек войдёт в аккаунт в открывшемся окне."""
        while not self.is_authenticated():
            log.warning(
                "Не вижу активной сессии. Войдите в Госуслуги в открывшемся окне "
                "и оставьте его открытым — ждём…"
            )
            try:
                self.page.wait_for_timeout(LOGIN_POLL_MS)
            except PWTimeout:
                pass
        log.info("сессия активна")

    def is_authenticated(self) -> bool:
        """POST /auth-provider/check-session — та же ручка, что и у портала."""
        try:
            result = self.page.evaluate(
                """async (url) => {
                    const r = await fetch(url,
                        { method: "POST", credentials: "include",
                          headers: { "Content-Type": "application/json" }, body: "{}" });
                    if (!r.ok) return { ok: false, status: r.status };
                    try { return { ok: true, ...(await r.json()) }; }
                    catch (e) { return { ok: false, status: r.status }; }
                }""",
                CHECK_SESSION_URL,
            )
        except Exception:  # noqa: BLE001 — страница могла быть на перезагрузке
            log.exception("check-session не удался")
            return False
        return bool(result.get("auth"))

    def fetch_slots(self, office: Office) -> SlotsResponse:
        result = self.page.evaluate(
            _FETCH_JS,
            {
                "url": SLOTS_URL,
                "body": self.cfg.slots_payload(office),
                "headers": {
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/plain, */*",
                    HEADER_ORDER_ID: self.cfg.order_id,
                    HEADER_FORM_ID: FORM_ID,
                },
            },
        )
        status = int(result["status"])
        if status == 0:
            expected = self.cfg.booking_page
            actual = result.get("href", "")
            # Страница могла уйти в навигацию: сравниваем без query-строки,
            # Angular любит дописывать и убирать параметры на ходу.
            moved = actual.split("?")[0] != expected.split("?")[0]
            raise RequestNotSent(
                f"{result.get('error', 'запрос не ушёл')}; "
                f"сеть по мнению браузера {'есть' if result.get('online') else 'ПРОПАЛА'}"
                + (f"; страница уехала на {actual}" if moved else "")
            )
        if status in (401, 403):
            raise SessionLost(f"HTTP {status} от /equeue/agg/slots")
        return SlotsResponse(status=status, payload=result["payload"])
