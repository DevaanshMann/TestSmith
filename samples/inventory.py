"""Warehouse inventory tracking."""

from __future__ import annotations

from dataclasses import dataclass


class OutOfStockError(Exception):
    """Raised when removing more units than are in stock."""


@dataclass
class Item:
    sku: str
    name: str
    unit_price: float
    quantity: int = 0
    reorder_level: int = 5


class Inventory:
    def __init__(self) -> None:
        self._items: dict[str, Item] = {}

    def add_item(self, sku: str, name: str, unit_price: float, quantity: int = 0, reorder_level: int = 5) -> Item:
        """Register a new SKU. Raises ValueError for duplicates or invalid values."""
        if sku in self._items:
            raise ValueError(f"SKU {sku} already exists")
        if unit_price < 0 or quantity < 0 or reorder_level < 0:
            raise ValueError("price, quantity and reorder level must be non-negative")
        item = Item(sku, name, unit_price, quantity, reorder_level)
        self._items[sku] = item
        return item

    def get(self, sku: str) -> Item:
        try:
            return self._items[sku]
        except KeyError:
            raise KeyError(f"unknown SKU {sku}") from None

    def restock(self, sku: str, units: int) -> int:
        """Add units to stock and return the new quantity."""
        if units <= 0:
            raise ValueError("units must be positive")
        item = self.get(sku)
        item.quantity += units
        return item.quantity

    def remove(self, sku: str, units: int) -> int:
        """Remove units from stock and return the new quantity."""
        if units <= 0:
            raise ValueError("units must be positive")
        item = self.get(sku)
        if units > item.quantity:
            raise OutOfStockError(f"only {item.quantity} units of {sku} left")
        item.quantity -= units
        return item.quantity

    def needs_reorder(self) -> list[str]:
        """SKUs whose quantity is at or below their reorder level, sorted."""
        return sorted(sku for sku, item in self._items.items() if item.quantity < item.reorder_level)

    def total_value(self) -> float:
        """Total stock value, rounded to cents."""
        return round(sum(i.unit_price * i.quantity for i in self._items.values()), 2)

    def search(self, term: str) -> list[Item]:
        """Case-insensitive substring search on item names."""
        term = term.strip().lower()
        if not term:
            return []
        return [i for i in self._items.values() if term in i.name.lower()]
