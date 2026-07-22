"""Настройки, которые относятся лично к вам. Читаются из .env.

Константы протокола ЕПГУ (адреса ручек, идентификаторы услуг) — в constants.py,
их трогать не нужно. Здесь только то, что у каждого своё.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

from .constants import (
    BOOKING_PAGE_TEMPLATE,
    ESERVICE_ID,
    FORM_ID,
    MAC_SOUNDS_DIR,
    MESSAGES_SERVICES,
    PASSPORT_TYPE_LABELS,
    SERVICE_IDS,
)

log = logging.getLogger(__name__)

# Корень проекта: src/gswatch/config.py → ../../
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Необязательный справочник отделений: выгрузка из ответа портала, нужна только
# ради читаемых подписей в логе. В репозиторий не входит — файла может не быть,
# и это штатная ситуация, см. README.
OFFICES_REFERENCE = PROJECT_ROOT / "offices_moscow.json"


class ConfigError(Exception):
    """Настройки заполнены так, что работать нельзя."""


@dataclass(frozen=True)
class AlertChannels:
    """Рубильники каналов: выключить, не стирая настройки.

    Канал работает, только если он включён здесь И заполнены его параметры.
    Так номер телефона или токен бота можно оставить в .env на будущее, а сам
    канал временно погасить одной строкой.

    Лога в списке нет намеренно: он всегда включён, иначе при отвале остальных
    каналов сторож работал бы вслепую.
    """

    mac: bool = True
    messages: bool = True
    telegram: bool = True
    vk: bool = True


@dataclass(frozen=True)
class MacAlertOptions:
    """Из чего складывается громкий сигнал на Маке.

    Каждый кусочек включается отдельно: кому-то хватит звука, кому-то нужна
    модалка на весь экран. Пустая строка в voice/sound = эта часть выключена.
    """

    wake_screen: bool = True
    max_volume: bool = True
    volume_level: int = 100
    voice: str = "Milena"  # голос say; пусто — не говорить вслух
    sound: str = "Sosumi"  # имя из /System/Library/Sounds; пусто — не пищать
    sound_repeat: int = 5
    modal: bool = True
    modal_button: str = "Понял"
    # Если человека нет у Мака, окно висело бы вечно и держало поток.
    modal_timeout_sec: float = 900.0

    @property
    def enabled(self) -> bool:
        return any(
            (self.wake_screen, self.max_volume, self.voice, self.sound, self.modal)
        )

    def summary(self) -> str:
        parts = []
        if self.wake_screen:
            parts.append("разбудить экран")
        if self.max_volume:
            parts.append(f"громкость {self.volume_level}")
        if self.voice:
            parts.append(f"голос {self.voice}")
        if self.sound:
            parts.append(f"звук {self.sound}×{self.sound_repeat}")
        if self.modal:
            parts.append("модальное окно")
        return ", ".join(parts) if parts else "ничего"


@dataclass(frozen=True)
class Office:
    """Отделение МВД. code_frgu — это и есть organizationId в запросе слотов."""

    code_frgu: str
    label: str

    def __str__(self) -> str:
        return self.label


# Казённая обвязка, одинаковая у всех 109 названий — вырезаем, чтобы остался район.
_TITLE_NOISE: tuple[str, ...] = (
    "Отделение по вопросам миграции отдела ",
    "Отдел по вопросам миграции отдела ",
    "Министерства внутренних дел Российской Федерации ",
    "города Москвы",
    "по району ",
    "по ",
)


def _short_title(title: str) -> str:
    """Выжать из казённого названия район.

    "Отдел по вопросам миграции отдела Министерства внутренних дел Российской
    Федерации по району Марфино города Москвы" → "Марфино".

    Нужно, чтобы различать отделы по одному адресу: в 17-м проезде Марьиной
    Рощи, д. 4 к. 1 сидят сразу два — Марфино и Марьина Роща.
    """
    short = " ".join(title.split())
    for chunk in _TITLE_NOISE:
        short = short.replace(chunk, "")
    return short.strip(' "«»,') or title


def load_office_reference() -> dict[str, str]:
    """CODE_FRGU → читаемая подпись, из справочника 109 отделений Москвы.

    Нужен только для логов. Если файла нет — не беда, будут голые коды.
    """
    if not OFFICES_REFERENCE.exists():
        log.warning("нет %s — в логе будут коды вместо адресов", OFFICES_REFERENCE.name)
        return {}
    try:
        items = json.loads(OFFICES_REFERENCE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("не читается %s: %s", OFFICES_REFERENCE.name, exc)
        return {}
    reference: dict[str, str] = {}
    for item in items:
        code = item.get("codeFrgu")
        if not code:
            continue
        name = _short_title(item.get("title", ""))
        address = item.get("address", "")
        reference[str(code)] = f"{name}, {address}" if name and address else (name or address)
    return reference


def _split_codes(raw: str) -> list[str]:
    """"123, 456" → ["123", "456"]."""
    return [chunk.strip() for chunk in raw.replace("\n", ",").split(",") if chunk.strip()]


@dataclass
class Config:
    # Номер вашей заявки на загранпаспорт (parentOrderId). Виден в адресе
    # страницы записи и в личном кабинете.
    order_id: str

    # Ключ из SERVICE_IDS — какую именно услугу спрашивать.
    passport_type: str

    # Отделения, за которыми следим.
    offices: tuple[Office, ...]

    # Базовый период опроса и разброс: реальная пауза = period_min ± jitter_min.
    # Неровные интервалы выглядят естественнее машинно-точных.
    period_min: float = 8.0
    jitter_min: float = 5.0

    # Пока слоты в отделении не пропадали, не повторять алерт чаще этого.
    repeat_alert_min: float = 20.0

    # Пауза между отделениями внутри одного обхода, секунды: запросы уходят
    # вразбивку, а не пачкой.
    office_pause_min_sec: float = 1.0
    office_pause_max_sec: float = 4.0

    # Сколько слотов перечислять в уведомлении.
    slots_in_message: int = 10

    # Профиль Chrome с сессией ЕПГУ. Вне репозитория: там куки от аккаунта.
    profile_dir: Path = field(default_factory=lambda: Path("~/.gswatch/chrome-profile"))

    # Какие каналы включены. Параметры каждого — ниже.
    channels: AlertChannels = field(default_factory=AlertChannels)

    # Громкий сигнал на самом Маке. Единственный канал, которому не нужна сеть.
    mac_alerts: MacAlertOptions = field(default_factory=MacAlertOptions)

    # Messages.app: номер телефона или Apple ID. Пусто — выключено.
    messages_to: str = ""
    # Какой службой слать: "SMS" даёт настоящее входящее со звуком,
    # "iMessage" при отправке самому себе может прийти беззвучно.
    messages_service: str = "SMS"

    # Telegram. Пусто — канал просто не подключается, лог остаётся.
    tg_token: str = ""
    tg_chat_id: str = ""

    # ВКонтакте: нужны сообщество с ботом и ваш числовой id. См. alerts/vk.py.
    vk_token: str = ""
    vk_peer_id: str = ""

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.tg_token and self.tg_chat_id)

    @property
    def vk_enabled(self) -> bool:
        return bool(self.vk_token and self.vk_peer_id)

    @property
    def service_id(self) -> str:
        return SERVICE_IDS[self.passport_type]

    @property
    def passport_label(self) -> str:
        return PASSPORT_TYPE_LABELS.get(self.passport_type, self.passport_type)

    @property
    def booking_page(self) -> str:
        return BOOKING_PAGE_TEMPLATE.format(form_id=FORM_ID, order_id=self.order_id)

    def slots_payload(self, office: Office) -> dict:
        """Тело POST /api/lk/v1/equeue/agg/slots, байт в байт как в HAR."""
        return {
            "organizationId": [office.code_frgu],
            "serviceId": [self.service_id],
            "eserviceId": ESERVICE_ID,
            "attributes": [],
            "filter": None,
        }


def load_config(env_file: Path | None = None) -> Config:
    """Собрать настройки из .env. Переменные окружения имеют приоритет."""
    import os

    env_path = env_file or (PROJECT_ROOT / ".env")
    values: dict[str, str | None] = {}
    if env_path.exists():
        values.update(dotenv_values(env_path))
    else:
        log.warning("нет %s — беру настройки из окружения", env_path)
    values.update({k: v for k, v in os.environ.items() if k.startswith("GSWATCH_")})

    def get(key: str, default: str = "") -> str:
        return (values.get(key) or default).strip()

    def get_optional(key: str, default: str) -> str:
        """Как get, но пустое значение означает «выключено», а не «по умолчанию».

        Нужно там, где пустота — осмысленный выбор: GSWATCH_MAC_VOICE= должно
        отключать голос, а не молча возвращать Milena.
        """
        raw = values.get(key)
        return default if raw is None else raw.strip()

    order_id = get("GSWATCH_ORDER_ID")
    if not order_id.isdigit():
        raise ConfigError(
            "GSWATCH_ORDER_ID должен быть числом — это номер заявки (parentOrderId) "
            f"из адреса страницы записи, а получено {order_id!r}. "
            "Скопируйте .env.example в .env и заполните."
        )

    passport_type = get("GSWATCH_PASSPORT_TYPE", "new_adult")
    if passport_type not in SERVICE_IDS:
        raise ConfigError(
            f"GSWATCH_PASSPORT_TYPE={passport_type!r} — неизвестный тип. "
            f"Допустимые: {', '.join(SERVICE_IDS)}"
        )

    codes = _split_codes(get("GSWATCH_OFFICES"))
    if not codes:
        raise ConfigError(
            "GSWATCH_OFFICES пуст — укажите хотя бы один CODE_FRGU через запятую. "
            "Свой код есть в ответе POST /api/service/booking: "
            "applicantAnswers.ms1 -> attributeValues.CODE_FRGU"
        )
    reference = load_office_reference()
    offices = tuple(
        Office(code_frgu=code, label=reference.get(code) or f"отделение {code}")
        for code in codes
    )
    unknown = [o.code_frgu for o in offices if o.code_frgu not in reference]
    if unknown and reference:
        log.warning(
            "нет в справочнике Москвы: %s — опрашивать буду, но проверьте коды",
            ", ".join(unknown),
        )

    def get_float(key: str, default: float) -> float:
        raw = get(key)
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError as exc:
            raise ConfigError(f"{key}={raw!r} — ожидалось число") from exc

    period = get_float("GSWATCH_PERIOD_MIN", 8.0)
    jitter = get_float("GSWATCH_JITTER_MIN", 5.0)
    if period <= 0:
        raise ConfigError("GSWATCH_PERIOD_MIN должен быть больше нуля")
    if jitter >= period:
        # Иначе разброс уводит паузу в ноль и опрос становится долбёжкой.
        raise ConfigError(
            f"GSWATCH_JITTER_MIN ({jitter}) должен быть меньше "
            f"GSWATCH_PERIOD_MIN ({period})"
        )

    def get_int(key: str, default: int) -> int:
        raw = get(key)
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError as exc:
            raise ConfigError(f"{key}={raw!r} — ожидалось целое число") from exc

    slots_in_message = get_int("GSWATCH_SLOTS_IN_MESSAGE", 10)
    if slots_in_message < 1:
        raise ConfigError("GSWATCH_SLOTS_IN_MESSAGE должен быть не меньше 1")

    pause_min = get_float("GSWATCH_OFFICE_PAUSE_MIN_SEC", 1.0)
    pause_max = get_float("GSWATCH_OFFICE_PAUSE_MAX_SEC", 4.0)
    if pause_min < 0:
        raise ConfigError("GSWATCH_OFFICE_PAUSE_MIN_SEC не может быть отрицательной")
    if pause_max < pause_min:
        raise ConfigError(
            f"GSWATCH_OFFICE_PAUSE_MAX_SEC ({pause_max}) должна быть не меньше "
            f"GSWATCH_OFFICE_PAUSE_MIN_SEC ({pause_min})"
        )

    def get_bool(key: str, default: bool) -> bool:
        raw = get(key).lower()
        if not raw:
            return default
        if raw in ("1", "true", "yes", "on", "да"):
            return True
        if raw in ("0", "false", "no", "off", "нет"):
            return False
        raise ConfigError(f"{key}={raw!r} — ожидалось true или false")

    channels = AlertChannels(
        mac=get_bool("GSWATCH_ALERT_MAC", True),
        messages=get_bool("GSWATCH_ALERT_MESSAGES", True),
        telegram=get_bool("GSWATCH_ALERT_TELEGRAM", True),
        vk=get_bool("GSWATCH_ALERT_VK", True),
    )

    mac_sound = get_optional("GSWATCH_MAC_SOUND", "Sosumi")
    if mac_sound and not (MAC_SOUNDS_DIR / f"{mac_sound}.aiff").exists():
        available = sorted(p.stem for p in MAC_SOUNDS_DIR.glob("*.aiff"))
        raise ConfigError(
            f"GSWATCH_MAC_SOUND={mac_sound!r} — такого звука нет. "
            f"Доступны: {', '.join(available)}"
        )

    mac_alerts = MacAlertOptions(
        wake_screen=get_bool("GSWATCH_MAC_WAKE_SCREEN", True),
        max_volume=get_bool("GSWATCH_MAC_MAX_VOLUME", True),
        volume_level=get_int("GSWATCH_MAC_VOLUME_LEVEL", 100),
        voice=get_optional("GSWATCH_MAC_VOICE", "Milena"),
        sound=mac_sound,
        sound_repeat=get_int("GSWATCH_MAC_SOUND_REPEAT", 5),
        modal=get_bool("GSWATCH_MAC_MODAL", True),
    )
    if not 0 <= mac_alerts.volume_level <= 100:
        raise ConfigError("GSWATCH_MAC_VOLUME_LEVEL должен быть от 0 до 100")
    if mac_alerts.sound_repeat < 1:
        raise ConfigError("GSWATCH_MAC_SOUND_REPEAT должен быть не меньше 1")

    messages_to = get("GSWATCH_MESSAGES_TO")
    messages_service = get("GSWATCH_MESSAGES_SERVICE", "SMS")
    if messages_to and messages_service not in MESSAGES_SERVICES:
        raise ConfigError(
            f"GSWATCH_MESSAGES_SERVICE={messages_service!r} — допустимо "
            f"{' или '.join(MESSAGES_SERVICES)}"
        )

    tg_token = get("GSWATCH_TG_TOKEN")
    tg_chat_id = get("GSWATCH_TG_CHAT_ID")
    if bool(tg_token) != bool(tg_chat_id):
        # Полконфига — почти наверняка недозаполнено, а не осознанный выбор.
        # Молча слать в лог в таком случае обиднее, чем сказать прямо.
        missing = "GSWATCH_TG_TOKEN" if not tg_token else "GSWATCH_TG_CHAT_ID"
        raise ConfigError(
            f"для Telegram нужны обе переменные, а {missing} пуста. "
            "Оставьте обе пустыми, чтобы отключить Telegram."
        )
    if tg_token and ":" not in tg_token:
        raise ConfigError(
            "GSWATCH_TG_TOKEN не похож на токен бота — ожидается вид "
            "123456789:AA... Скопируйте его целиком из @BotFather."
        )

    return Config(
        order_id=order_id,
        passport_type=passport_type,
        offices=offices,
        period_min=period,
        jitter_min=jitter,
        repeat_alert_min=get_float("GSWATCH_REPEAT_ALERT_MIN", 20.0),
        office_pause_min_sec=pause_min,
        office_pause_max_sec=pause_max,
        slots_in_message=slots_in_message,
        profile_dir=Path(
            get("GSWATCH_PROFILE_DIR", "~/.gswatch/chrome-profile")
        ).expanduser(),
        channels=channels,
        mac_alerts=mac_alerts,
        messages_to=messages_to,
        messages_service=messages_service,
        tg_token=tg_token,
        tg_chat_id=tg_chat_id,
        vk_token=get("GSWATCH_VK_TOKEN"),
        vk_peer_id=get("GSWATCH_VK_PEER_ID"),
    )
