"""Order pricing engine: bulk discounts, loyalty tiers, coupons, shipping, and tax."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")

TIER_DISCOUNTS = {"standard": Decimal("0"), "silver": Decimal("0.03"), "gold": Decimal("0.05")}

# Region -> (tax rate, taxes shipping?, categories exempt from tax)
TAX_RULES: dict[str, tuple[Decimal, bool, frozenset[str]]] = {
    "CA": (Decimal("0.0725"), False, frozenset({"grocery"})),
    "NY": (Decimal("0.04"), True, frozenset({"grocery", "clothing"})),
    "TX": (Decimal("0.0625"), True, frozenset({"grocery"})),
    "OR": (Decimal("0"), False, frozenset()),
}

# Region -> (base fee, per-kg fee)
SHIPPING_RATES: dict[str, tuple[Decimal, Decimal]] = {
    "CA": (Decimal("5.00"), Decimal("0.50")),
    "NY": (Decimal("6.00"), Decimal("0.60")),
    "TX": (Decimal("5.50"), Decimal("0.45")),
    "OR": (Decimal("5.00"), Decimal("0.55")),
}
FREE_SHIPPING_THRESHOLD = Decimal("75.00")
EXPRESS_MULTIPLIER = Decimal("1.8")
HEAVY_ITEM_KG = Decimal("20")
HEAVY_SURCHARGE = Decimal("15.00")
MAX_PERCENT_OFF = Decimal("0.50")


class PricingError(ValueError):
    """Raised for invalid orders, customers, or coupons."""


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class LineItem:
    sku: str
    category: str
    unit_price: Decimal
    quantity: int
    weight_kg: Decimal = Decimal("0")

    @property
    def subtotal(self) -> Decimal:
        return self.unit_price * self.quantity


@dataclass(frozen=True)
class Customer:
    tier: str = "standard"
    region: str = "CA"
    tax_exempt: bool = False


@dataclass(frozen=True)
class Coupon:
    code: str
    kind: str                      # "percent", "fixed", "free_shipping", "bogo"
    value: Decimal = Decimal("0")  # fraction for percent, amount for fixed
    min_subtotal: Decimal = Decimal("0")
    category: str | None = None    # restrict to one category
    expires: date | None = None
    stackable: bool = False


@dataclass
class Quote:
    subtotal: Decimal
    bulk_discount: Decimal
    tier_discount: Decimal
    coupon_discount: Decimal
    shipping: Decimal
    tax: Decimal
    total: Decimal
    applied_coupons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def validate_order(items: list[LineItem], customer: Customer) -> None:
    if not items:
        raise PricingError("order has no items")
    if customer.tier not in TIER_DISCOUNTS:
        raise PricingError(f"unknown customer tier {customer.tier!r}")
    if customer.region not in TAX_RULES:
        raise PricingError(f"we do not ship to {customer.region!r}")
    seen: set[str] = set()
    for item in items:
        if item.sku in seen:
            raise PricingError(f"duplicate SKU {item.sku}")
        seen.add(item.sku)
        if item.quantity <= 0:
            raise PricingError(f"quantity for {item.sku} must be positive")
        if item.unit_price < 0 or item.weight_kg < 0:
            raise PricingError(f"price and weight for {item.sku} must be non-negative")


def bulk_discount_rate(quantity: int) -> Decimal:
    """10+ units: 5%, 50+ units: 10%, 100+ units: 15% off that line."""
    if quantity >= 100:
        return Decimal("0.15")
    if quantity >= 50:
        return Decimal("0.10")
    if quantity >= 10:
        return Decimal("0.05")
    return Decimal("0")


def bulk_discount(items: list[LineItem]) -> Decimal:
    return sum((item.subtotal * bulk_discount_rate(item.quantity) for item in items), Decimal("0"))


def select_coupons(coupons: list[Coupon], subtotal: Decimal, today: date) -> tuple[list[Coupon], list[str]]:
    """Drop expired or ineligible coupons. At most one non-stackable coupon is kept (the first)."""
    kept: list[Coupon] = []
    notes: list[str] = []
    codes: set[str] = set()
    has_exclusive = False
    for coupon in coupons:
        code = coupon.code.upper()
        if code in codes:
            notes.append(f"{code}: duplicate ignored")
            continue
        codes.add(code)
        if coupon.kind not in {"percent", "fixed", "free_shipping", "bogo"}:
            raise PricingError(f"unknown coupon kind {coupon.kind!r}")
        if coupon.expires is not None and coupon.expires < today:
            notes.append(f"{code}: expired")
            continue
        if subtotal < coupon.min_subtotal:
            notes.append(f"{code}: requires subtotal of {coupon.min_subtotal}")
            continue
        if not coupon.stackable:
            if has_exclusive:
                notes.append(f"{code}: cannot be combined")
                continue
            has_exclusive = True
        kept.append(coupon)
    return kept, notes


def coupon_value(coupon: Coupon, items: list[LineItem], discounted_subtotal: Decimal) -> Decimal:
    """Money off for a single coupon (free_shipping is handled in shipping())."""
    eligible = [i for i in items if coupon.category is None or i.category == coupon.category]
    if not eligible:
        return Decimal("0")
    base = sum((i.subtotal for i in eligible), Decimal("0"))
    if coupon.kind == "percent":
        if not Decimal("0") < coupon.value <= MAX_PERCENT_OFF:
            raise PricingError("percent coupons must be between 0 and 50%")
        return base * coupon.value
    if coupon.kind == "fixed":
        if coupon.value <= 0:
            raise PricingError("fixed coupons must be positive")
        return min(coupon.value, base, discounted_subtotal)
    if coupon.kind == "bogo":
        # Buy one, get one free: every second unit of the cheapest eligible line is free.
        cheapest = min(eligible, key=lambda i: i.unit_price)
        return cheapest.unit_price * (cheapest.quantity // 2)
    return Decimal("0")


def shipping(items: list[LineItem], customer: Customer, merchandise: Decimal, express: bool, free: bool) -> tuple[Decimal, list[str]]:
    notes: list[str] = []
    weight = sum((i.weight_kg * i.quantity for i in items), Decimal("0"))
    base, per_kg = SHIPPING_RATES[customer.region]
    cost = base + per_kg * weight
    if any(i.weight_kg >= HEAVY_ITEM_KG for i in items):
        cost += HEAVY_SURCHARGE
        notes.append("heavy item surcharge")
    if express:
        cost *= EXPRESS_MULTIPLIER
    if free:
        notes.append("free shipping coupon")
        return Decimal("0"), notes
    if not express and merchandise >= FREE_SHIPPING_THRESHOLD:
        notes.append("free standard shipping over threshold")
        return Decimal("0"), notes
    return cost, notes


def tax(items: list[LineItem], customer: Customer, discount_ratio: Decimal, shipping_cost: Decimal) -> Decimal:
    if customer.tax_exempt:
        return Decimal("0")
    rate, taxes_shipping, exempt = TAX_RULES[customer.region]
    taxable = sum((i.subtotal for i in items if i.category not in exempt), Decimal("0"))
    taxable *= (1 - discount_ratio)
    if taxes_shipping:
        taxable += shipping_cost
    return taxable * rate


def quote(
    items: list[LineItem],
    customer: Customer,
    coupons: list[Coupon] | None = None,
    *,
    express: bool = False,
    today: date | None = None,
) -> Quote:
    """Price an order. Discounts apply in order: bulk, loyalty tier, coupons. Totals never go negative."""
    validate_order(items, customer)
    today = today or date.today()
    subtotal = sum((i.subtotal for i in items), Decimal("0"))

    bulk = bulk_discount(items)
    tier = (subtotal - bulk) * TIER_DISCOUNTS[customer.tier]
    after_tier = subtotal - bulk - tier

    selected, notes = select_coupons(coupons or [], subtotal, today)
    coupon_total = Decimal("0")
    for coupon in selected:
        coupon_total += coupon_value(coupon, items, after_tier - coupon_total)
    coupon_total = min(coupon_total, after_tier)
    merchandise = after_tier - coupon_total

    free_ship = any(c.kind == "free_shipping" for c in selected)
    ship, ship_notes = shipping(items, customer, merchandise, express, free_ship)
    discount_ratio = (subtotal - merchandise) / subtotal if subtotal else Decimal("0")
    tax_amount = tax(items, customer, discount_ratio, ship)

    return Quote(
        subtotal=money(subtotal),
        bulk_discount=money(bulk),
        tier_discount=money(tier),
        coupon_discount=money(coupon_total),
        shipping=money(ship),
        tax=money(tax_amount),
        total=money(merchandise + ship + tax_amount),
        applied_coupons=[c.code.upper() for c in selected],
        notes=notes + ship_notes,
    )
