<example>
<source file="pricing.py">
def apply_discount(price: float, percent: int) -> float:
    """Return price reduced by percent (0-100), rounded to 2 decimals."""
    if price < 0:
        raise ValueError("price must be non-negative")
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100")
    return round(price * (100 - percent) / 100, 2)
</source>
<test_plan>
happy_path: typical discounts on a normal price
edge_cases: zero price; float rounding to 2 decimals
error_cases: negative price -> ValueError; percent outside 0-100 -> ValueError
boundaries: percent 0 and 100 (valid), -1 and 101 (invalid)
</test_plan>
<tests file="test_pricing.py">
import pytest

from pricing import apply_discount


@pytest.mark.parametrize(
    ("price", "percent", "expected"),
    [
        (100.0, 10, 90.0),
        (59.99, 25, 44.99),
        (0.0, 50, 0.0),
        (10.0, 0, 10.0),      # boundary: no discount
        (10.0, 100, 0.0),     # boundary: full discount
        (19.999, 0, 20.0),    # rounds to 2 decimals
    ],
)
def test_apply_discount_returns_reduced_price(price, percent, expected):
    assert apply_discount(price, percent) == pytest.approx(expected)


def test_negative_price_raises():
    with pytest.raises(ValueError, match="non-negative"):
        apply_discount(-0.01, 10)


@pytest.mark.parametrize("percent", [-1, 101])
def test_percent_outside_range_raises(percent):
    with pytest.raises(ValueError, match="between 0 and 100"):
        apply_discount(10.0, percent)
</tests>
</example>

<example>
<source file="stack.py">
class Stack:
    """LIFO stack with an optional maximum size."""

    def __init__(self, max_size: int | None = None):
        self._items = []
        self.max_size = max_size

    def push(self, item):
        if self.max_size is not None and len(self._items) >= self.max_size:
            raise OverflowError("stack is full")
        self._items.append(item)

    def pop(self):
        if not self._items:
            raise IndexError("pop from empty stack")
        return self._items.pop()

    def __len__(self):
        return len(self._items)
</source>
<test_plan>
happy_path: push then pop returns items in LIFO order; len tracks size
edge_cases: unbounded stack accepts many items; None is a valid item
error_cases: pop on empty -> IndexError; push on full -> OverflowError
boundaries: max_size reached exactly; pop after filling and emptying
</test_plan>
<tests file="test_stack.py">
import pytest

from stack import Stack


@pytest.fixture
def stack():
    return Stack()


def test_pop_returns_items_in_lifo_order(stack):
    for item in (1, 2, 3):
        stack.push(item)
    assert [stack.pop(), stack.pop(), stack.pop()] == [3, 2, 1]


def test_len_tracks_pushes_and_pops(stack):
    stack.push("a")
    stack.push("b")
    stack.pop()
    assert len(stack) == 1


def test_none_is_a_valid_item(stack):
    stack.push(None)
    assert stack.pop() is None


def test_pop_on_empty_stack_raises(stack):
    with pytest.raises(IndexError, match="empty"):
        stack.pop()


def test_push_beyond_max_size_raises():
    bounded = Stack(max_size=2)
    bounded.push(1)
    bounded.push(2)  # boundary: exactly full is allowed
    with pytest.raises(OverflowError, match="full"):
        bounded.push(3)


def test_unbounded_stack_accepts_many_items(stack):
    for i in range(1000):
        stack.push(i)
    assert len(stack) == 1000
</tests>
</example>
