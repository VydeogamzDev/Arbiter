"""A tiny in-process event bus for order lifecycle events."""
from shop.inventory.reservations import release

_handlers: dict[str, list] = {}


def on(event: str, fn) -> None:
    _handlers.setdefault(event, []).append(fn)


def emit(event: str, *args) -> None:
    for fn in _handlers.get(event, []):
        fn(*args)


def _release_on_cancel(stock, order) -> None:
    release(stock, order)


on("order_cancelled", _release_on_cancel)
