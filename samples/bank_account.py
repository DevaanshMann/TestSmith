"""A small bank account model with deposits, withdrawals, transfers, and interest."""

from __future__ import annotations

from dataclasses import dataclass, field


class InsufficientFundsError(Exception):
    """Raised when a withdrawal or transfer exceeds the available balance."""


class DailyLimitExceededError(Exception):
    """Raised when withdrawals in one day would exceed the daily limit."""


@dataclass
class Transaction:
    kind: str
    amount: float
    balance_after: float


@dataclass
class BankAccount:
    owner: str
    balance: float = 0.0
    daily_withdrawal_limit: float = 500.0
    withdrawn_today: float = 0.0
    frozen: bool = False
    history: list[Transaction] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.owner.strip():
            raise ValueError("owner must not be empty")
        if self.balance < 0:
            raise ValueError("opening balance cannot be negative")

    def deposit(self, amount: float) -> float:
        """Add a positive amount and return the new balance."""
        self._check_active()
        if amount <= 0:
            raise ValueError("deposit amount must be positive")
        self.balance = round(self.balance + amount, 2)
        self.history.append(Transaction("deposit", amount, self.balance))
        return self.balance

    def withdraw(self, amount: float) -> float:
        """Remove a positive amount, respecting the balance and the daily limit."""
        self._check_active()
        if amount <= 0:
            raise ValueError("withdrawal amount must be positive")
        if amount > self.balance:
            raise InsufficientFundsError(f"balance {self.balance:.2f} is less than {amount:.2f}")
        if self.withdrawn_today + amount > self.daily_withdrawal_limit:
            raise DailyLimitExceededError(
                f"daily limit {self.daily_withdrawal_limit:.2f} would be exceeded"
            )
        self.balance = round(self.balance - amount, 2)
        self.withdrawn_today = round(self.withdrawn_today + amount, 2)
        self.history.append(Transaction("withdrawal", amount, self.balance))
        return self.balance

    def transfer_to(self, other: "BankAccount", amount: float) -> None:
        """Move money to another account. Transfers do not count toward the daily limit."""
        self._check_active()
        other._check_active()
        if other is self:
            raise ValueError("cannot transfer to the same account")
        if amount <= 0:
            raise ValueError("transfer amount must be positive")
        if amount > self.balance:
            raise InsufficientFundsError("insufficient funds for transfer")
        self.balance = round(self.balance - amount, 2)
        other.balance = round(other.balance + amount, 2)
        self.history.append(Transaction("transfer_out", amount, self.balance))
        other.history.append(Transaction("transfer_in", amount, other.balance))

    def apply_monthly_interest(self, annual_rate: float) -> float:
        """Add one month of interest (annual_rate as a fraction, e.g. 0.05) and return it."""
        self._check_active()
        if not 0 <= annual_rate <= 0.25:
            raise ValueError("annual rate must be between 0 and 0.25")
        interest = round(self.balance * annual_rate / 12, 2)
        if interest > 0:
            self.balance = round(self.balance + interest, 2)
            self.history.append(Transaction("interest", interest, self.balance))
        return interest

    def reset_daily_limit(self) -> None:
        self.withdrawn_today = 0.0

    def freeze(self) -> None:
        self.frozen = True

    def unfreeze(self) -> None:
        self.frozen = False

    def statement(self) -> str:
        """Human-readable statement, one line per transaction."""
        lines = [f"Statement for {self.owner}"]
        if not self.history:
            lines.append("  (no transactions)")
        for tx in self.history:
            sign = "-" if tx.kind in ("withdrawal", "transfer_out") else "+"
            lines.append(f"  {tx.kind:<13} {sign}{tx.amount:>10.2f}  balance {tx.balance_after:>10.2f}")
        return "\n".join(lines)

    def _check_active(self) -> None:
        if self.frozen:
            raise PermissionError(f"account of {self.owner} is frozen")
