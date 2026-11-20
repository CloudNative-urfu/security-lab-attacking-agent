"""Детектирование секретов по имени поля и по значению.

Имя поля проверяется по границам слов (snake/camel/kebab), чтобы не вырезать безобидные
поля вроде ``tokenization_method`` и не пропускать секрет в нейтрально названном поле.
Значение проверяется по характерным высокоточным шаблонам (API-ключи, JWT, приватные
ключи). Фрипрозовые пароли без структуры детектировать по значению невозможно — это
отражено в политике честно; для них служит проверка по имени поля.
"""

from __future__ import annotations

import re

# Имена-маркеры (сравнение по отдельным словам, не подстрокой).
_SECRET_NAME_WORDS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "apikey",
        "key",
        "credential",
        "credentials",
        "privatekey",
        "accesskey",
        "clientsecret",
        "dsn",
    }
)

# Высокоточные шаблоны значений известных секретов.
_SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^sk-[A-Za-z0-9]{16,}$"),  # OpenAI-подобные ключи
    re.compile(r"^AKIA[0-9A-Z]{16}$"),  # AWS access key id
    re.compile(r"^gh[pousr]_[A-Za-z0-9]{20,}$"),  # GitHub токены
    re.compile(r"^xox[baprs]-[A-Za-z0-9-]{10,}$"),  # Slack токены
    re.compile(r"^eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}$"),  # JWT
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----"),  # PEM
)

_WORD_SPLIT = re.compile(r"[^a-z0-9]+")


def field_words(name: str) -> set[str]:
    """Разбить имя поля на слова (snake/kebab + camelCase)."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return {w for w in _WORD_SPLIT.split(spaced.lower()) if w}


def key_is_secret(key: str) -> bool:
    return bool(field_words(key) & _SECRET_NAME_WORDS)


def looks_like_secret_value(value: object) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if "vault://" in text or text.startswith(("http://", "https://")):
        return False  # ссылки и refs — не секреты-значения
    return any(p.search(text) for p in _SECRET_VALUE_PATTERNS)
