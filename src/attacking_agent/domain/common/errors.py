"""Иерархия доменных ошибок харнеса.

Ошибки домена не зависят от инфраструктуры и выражают нарушения инвариантов
безопасности или контрактов. Большинство отказов моделируется типизированными
статусами результата (см. :mod:`attacking_agent.domain.common.results`); исключения
используются для нарушений, при которых продолжение недопустимо.
"""

from __future__ import annotations


class HarnessError(Exception):
    """Базовая ошибка домена харнеса."""


class ImmutabilityError(HarnessError):
    """Попытка изменить неизменяемый (замороженный) объект."""


class ValidationError(HarnessError):
    """Нарушение контракта валидации входных данных."""


class SecretLeakError(ValidationError):
    """Обнаружен сырой секрет там, где допустима только ссылка (ref)."""


class ScopeViolationError(HarnessError):
    """Операция выходит за пределы разрешённой области (scope)."""


class BudgetError(HarnessError):
    """Нарушение инварианта бюджета/учёта."""


class PermitError(HarnessError):
    """Нарушение контракта допуска (permit)."""
