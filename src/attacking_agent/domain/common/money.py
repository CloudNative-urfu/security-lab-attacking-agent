"""Денежная величина как value object на Decimal.

Денежные бюджеты и расходы считаются в :class:`decimal.Decimal` с явной валютой.
Операции между разными валютами запрещены. Это исключает ошибки округления чисел с
плавающей точкой при учёте расходов.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Money:
    """Неизменяемая денежная величина.

    :param amount: сумма; строки и целые нормализуются в Decimal.
    :param currency: код валюты (ISO-4217, верхний регистр).
    """

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        amount: object = self.amount
        if not isinstance(amount, Decimal):
            object.__setattr__(self, "amount", Decimal(str(amount)))
        object.__setattr__(self, "currency", self.currency.upper())

    @classmethod
    def zero(cls, currency: str) -> Money:
        return cls(Decimal("0"), currency)

    def _check(self, other: Money) -> None:
        if self.currency != other.currency:
            raise ValueError(
                f"несовместимые валюты: {self.currency} и {other.currency}"
            )

    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount - other.amount, self.currency)

    def __lt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._check(other)
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        self._check(other)
        return self.amount >= other.amount

    def is_negative(self) -> bool:
        return self.amount < Decimal("0")
