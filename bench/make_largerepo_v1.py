"""Large-repo suite v1 (frozen 2026-09-26): bench/tasks_largerepo_v1.

The dev, held-out and quality suites use 3-30 file repos, where agents barely explore and the context
pack has little to save. These five tasks share a ~200-file codebase ("shopcore": 14 domains of
modules that import each other), with each task's bug or feature placed among them. Written before
any run; never tune Arbiter against it.
"""

from __future__ import annotations

import random
from pathlib import Path

from bench.make_heldout_v1 import d, write

ROOT = Path(__file__).resolve().parent / "tasks_largerepo_v1"
TASKS: list[dict] = []
PKG = "shopcore"

DOMAINS = ["auth", "billing", "catalog", "orders", "inventory", "notifications", "reports", "integrations",
           "analytics", "search", "shipping", "users", "admin", "utils"]
NOUNS = ["summary", "ledger", "batch", "snapshot", "export", "digest", "window", "rollup", "profile", "queue",
         "policy", "rule", "cache", "index", "mapper", "loader", "writer", "reader", "resolver", "planner",
         "scheduler", "tracker", "registry", "adapter", "formatter", "validator", "builder", "filter"]
VERBS = ["compute", "build", "load", "merge", "split", "normalize", "collect", "render", "validate", "score",
         "group", "rank", "resolve", "estimate", "flatten", "prune"]


def filler() -> dict[str, str]:
    """Plausible modules that import each other (and some real core modules), deterministic."""
    rng = random.Random(1729)
    files: dict[str, str] = {f"{PKG}/__init__.py": '"""shopcore: the shop platform backend."""\n'}
    names: dict[str, list[str]] = {}
    for dom in DOMAINS:
        files[f"{PKG}/{dom}/__init__.py"] = f'"""{dom.title()} domain."""\n'
        mods = rng.sample(NOUNS, 13)
        names[dom] = [f"{m}_{i}" if i else m for i, m in enumerate(mods[:1])] + mods[1:]
    core_users = {"billing": "from shopcore.billing.rounding import round_money\n",
                  "reports": "from shopcore.billing.rounding import round_money\n",
                  "orders": "from shopcore.config.settings import get_setting\n",
                  "notifications": "from shopcore.config.settings import get_setting\n",
                  "admin": "from shopcore.audit import record\n"}
    for dom in DOMAINS:
        for mod in names[dom]:
            v1, v2 = rng.sample(VERBS, 2)
            sib = rng.choice([m for m in names[dom] if m != mod])
            uses_core = dom in core_users and rng.random() < 0.35
            imports = f"from shopcore.{dom} import {sib}\n" + (core_users[dom] if uses_core else "")
            body_extra = ("    total = round_money(total)\n" if uses_core and "round_money" in core_users[dom]
                          else "")
            files[f"{PKG}/{dom}/{mod}.py"] = d(f'''
                """{dom.title()}: {mod.replace("_", " ")}."""
                {imports.rstrip()}


                def {v1}_{mod}(rows, *, limit=None):
                    """{v1.title()} the {mod.replace("_", " ")} for a list of row dicts."""
                    out = [r for r in rows if r.get("{dom}_id") is not None]
                    if limit is not None:
                        out = out[:limit]
                    total = sum(float(r.get("amount", 0)) for r in out)
                {body_extra.rstrip() or "    total = float(total)"}
                    return {{"rows": out, "total": total, "count": len(out)}}


                def {v2}_{mod}_keys(rows):
                    """Distinct keys seen across the rows."""
                    keys = set()
                    for r in rows:
                        keys.update(r)
                    return sorted(keys)


                def uses_{sib}():
                    return {sib}.__name__
                ''')
    return files


BASE_CORE = {
    f"{PKG}/config/__init__.py": "",
    f"{PKG}/config/settings.py": d(r'''
        """Platform settings with environment-style overrides."""
        import warnings

        DEFAULTS = {"smtp_host": "localhost", "smtp_port": 25, "max_cart_items": 50, "currency": "USD"}
        _overrides: dict = {}


        def configure(**values):
            _overrides.update(values)


        def reset():
            _overrides.clear()


        def get_setting(name):
            if name == "mail_host":
                warnings.warn("'mail_host' is deprecated; use 'smtp_host'", DeprecationWarning, stacklevel=2)
                name = "smtp_host"
            if name in _overrides:
                return _overrides[name]
            if name == "smtp_host" and "mail_host" in _overrides:
                warnings.warn("'mail_host' is deprecated; use 'smtp_host'", DeprecationWarning, stacklevel=2)
                return _overrides["mail_host"]
            return DEFAULTS[name]
        '''),
    f"{PKG}/billing/rounding.py": d(r'''
        """Money rounding used by invoices and reports."""
        from decimal import ROUND_HALF_UP, Decimal


        def round_money(value) -> float:
            """Round to cents, half up (0.125 -> 0.13)."""
            return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        '''),
    f"{PKG}/billing/invoices.py": d(r'''
        from shopcore.billing.rounding import round_money


        def invoice_total(lines):
            """lines: [(unit_price, qty)]; each line is rounded, then summed."""
            return round_money(sum(round_money(p * q) for p, q in lines))
        '''),
    f"{PKG}/reports/revenue.py": d(r'''
        from shopcore.billing.invoices import invoice_total


        def monthly_revenue(invoices):
            """invoices: {month: [lines]} -> {month: total}."""
            return {m: invoice_total(lines) for m, lines in sorted(invoices.items())}
        '''),
    f"{PKG}/orders/errors.py": d(r'''
        class OrderError(Exception):
            """Base class for order problems."""


        class CartLimitError(OrderError):
            """Too many distinct items in a cart."""
        '''),
    f"{PKG}/i18n/__init__.py": "",
    f"{PKG}/i18n/messages.py": d(r'''
        MESSAGES = {
            "cart_empty": "Your cart is empty.",
            "cart_limit": "A cart can hold at most {limit} different items.",
            "out_of_stock": "{sku} is out of stock.",
        }


        def message(key, **kw):
            return MESSAGES[key].format(**kw)
        '''),
    f"{PKG}/orders/cart.py": d(r'''
        from shopcore.config.settings import get_setting
        from shopcore.i18n.messages import message
        from shopcore.orders.errors import CartLimitError


        class Cart:
            def __init__(self):
                self.items: dict[str, int] = {}

            def add_item(self, sku, qty=1):
                if sku not in self.items and len(self.items) >= get_setting("max_cart_items"):
                    raise CartLimitError(message("cart_limit", limit=get_setting("max_cart_items")))
                self.items[sku] = self.items.get(sku, 0) + qty

            def count(self):
                return len(self.items)
        '''),
    f"{PKG}/notifications/email/__init__.py": "",
    f"{PKG}/notifications/email/sender.py": d(r'''
        from shopcore.config.settings import get_setting


        def connection_info():
            return {"host": get_setting("smtp_host"), "port": get_setting("smtp_port")}
        '''),
    f"{PKG}/api/__init__.py": "",
    f"{PKG}/api/pagination.py": d(r'''
        def paginate(items, page, per_page):
            """Items for a 1-based page number."""
            if page < 1 or per_page < 1:
                return []
            start = (page - 1) * per_page
            return items[start:start + per_page]
        '''),
    f"{PKG}/api/handlers/__init__.py": "",
    f"{PKG}/api/handlers/orders.py": d(r'''
        from shopcore.api.pagination import paginate
        from shopcore.audit import record


        def list_orders(orders, page=1, per_page=20):
            return {"page": page, "items": paginate(orders, page, per_page)}


        def cancel_order(orders, order_id, actor):
            orders[order_id]["status"] = "cancelled"
            record("order.cancel", actor, order_id)
            return orders[order_id]


        def refund_order(orders, order_id, actor):
            orders[order_id]["status"] = "refunded"
            record("order.refund", actor, order_id)
            return orders[order_id]
        '''),
    f"{PKG}/api/handlers/users.py": d(r'''
        from shopcore.audit import record


        def create_user(users, user_id, actor):
            users[user_id] = {"id": user_id, "active": True}
            record("user.create", actor, user_id)
            return users[user_id]
        '''),
    f"{PKG}/api/handlers/catalog.py": d(r'''
        from shopcore.audit import record


        def delete_product(products, sku, actor):
            products.pop(sku)
            record("product.delete", actor, sku)
            return True
        '''),
    f"{PKG}/audit.py": d(r'''
        """Audit trail for admin actions."""
        LOG: list = []


        def record(event, actor, target):
            LOG.append({"event": event, "actor": actor, "target": target})


        def clear():
            LOG.clear()
        '''),
    "tests/__init__.py": "",
    "tests/test_smoke.py": d(r'''
        from shopcore.api.handlers.users import create_user
        from shopcore.audit import LOG, clear
        from shopcore.orders.cart import Cart


        def test_cart_adds():
            c = Cart()
            c.add_item("a")
            assert c.count() == 1


        def test_create_user_audited():
            clear()
            create_user({}, "u1", "admin")
            assert LOG[-1]["event"] == "user.create"
        '''),
    "README.md": "# shopcore\n\nThe shop platform backend: orders, billing, catalog, notifications and more.\n",
}


def repo_with(**overrides: str) -> dict[str, str]:
    files = {**filler(), **BASE_CORE}
    files.update({k: v for k, v in overrides.items()})
    return files


def task(id: str, category: str, prompt: str, requirements: dict[str, str], broken: dict[str, str | None],
         hidden: str) -> None:
    repo = {**filler(), **BASE_CORE}
    for k, v in broken.items():
        if v is None:
            repo.pop(k, None)
        else:
            repo[k] = v
    solution = {k: BASE_CORE[k] for k in broken if k in BASE_CORE}
    TASKS.append(dict(id=id, category=category, prompts=[prompt], requirements=requirements, repo=repo,
                      hidden={"test_hidden.py": hidden}, solution=solution, sloppy={}, notes=""))


R = f"{PKG}/billing/rounding.py"
task("lr_rounding_cent", "retrieval",
     "Finance reports that invoice totals in the monthly revenue report are sometimes one cent low "
     "(for example a line of 3 x 0.125 should total 0.38). Find the cause and fix it.",
     {"half_up": "test_half_up", "invoice_total": "test_invoice_total", "monthly_revenue": "test_monthly_revenue"},
     {R: d(r'''
        """Money rounding used by invoices and reports."""


        def round_money(value) -> float:
            """Round to cents, half up (0.125 -> 0.13)."""
            return int(float(value) * 100) / 100
        ''')},
     d(r'''
        from shopcore.billing.invoices import invoice_total
        from shopcore.billing.rounding import round_money
        from shopcore.reports.revenue import monthly_revenue


        def test_half_up():
            assert round_money(0.125) == 0.13 and round_money(2.675) == 2.68 and round_money(1.004) == 1.0


        def test_invoice_total():
            assert invoice_total([(0.125, 3), (19.995, 1)]) == 20.38


        def test_monthly_revenue():
            assert monthly_revenue({"2026-01": [(0.125, 3)], "2026-02": [(10.0, 2)]}) == {"2026-01": 0.38,
                                                                                         "2026-02": 20.0}
        '''))

task("lr_cart_limit", "requirements",
     "Add a cart size limit: Cart.add_item in shopcore/orders/cart.py must raise CartLimitError when adding a new "
     "SKU would exceed the max_cart_items setting (default 50; adding more of a SKU already in the cart is always "
     "fine). Define CartLimitError in shopcore/orders/errors.py as a subclass of OrderError, and use a new "
     "\"cart_limit\" entry in shopcore/i18n/messages.py for its message: \"A cart can hold at most {limit} "
     "different items.\"",
     {"raises_at_limit": "test_raises_at_limit", "existing_sku_ok": "test_existing_sku_ok",
      "error_hierarchy": "test_error_hierarchy", "message_entry": "test_message_entry",
      "uses_setting": "test_uses_setting"},
     {f"{PKG}/orders/cart.py": d(r'''
        class Cart:
            def __init__(self):
                self.items: dict[str, int] = {}

            def add_item(self, sku, qty=1):
                self.items[sku] = self.items.get(sku, 0) + qty

            def count(self):
                return len(self.items)
        '''),
      f"{PKG}/orders/errors.py": d(r'''
        class OrderError(Exception):
            """Base class for order problems."""
        '''),
      f"{PKG}/i18n/messages.py": d(r'''
        MESSAGES = {
            "cart_empty": "Your cart is empty.",
            "out_of_stock": "{sku} is out of stock.",
        }


        def message(key, **kw):
            return MESSAGES[key].format(**kw)
        ''')},
     d(r'''
        import pytest

        from shopcore.config import settings
        from shopcore.i18n.messages import message
        from shopcore.orders import errors
        from shopcore.orders.cart import Cart


        def fill(c, n):
            for i in range(n):
                c.add_item(f"s{i}")


        def test_raises_at_limit():
            settings.reset()
            c = Cart()
            fill(c, 50)
            with pytest.raises(errors.CartLimitError) as e:
                c.add_item("one-more")
            assert "50" in str(e.value)


        def test_existing_sku_ok():
            settings.reset()
            c = Cart()
            fill(c, 50)
            c.add_item("s3", 2)
            assert c.items["s3"] == 3


        def test_error_hierarchy():
            assert issubclass(errors.CartLimitError, errors.OrderError)


        def test_message_entry():
            assert message("cart_limit", limit=7) == "A cart can hold at most 7 different items."


        def test_uses_setting():
            settings.configure(max_cart_items=2)
            try:
                c = Cart()
                fill(c, 2)
                with pytest.raises(errors.CartLimitError):
                    c.add_item("x")
            finally:
                settings.reset()
        '''))

task("lr_setting_rename", "api_compat",
     "Rename the `mail_host` setting to `smtp_host` everywhere in shopcore (the email sender reads it). Old code "
     "and configs still use `mail_host`: get_setting(\"mail_host\") and configure(mail_host=...) must keep working, "
     "emitting a DeprecationWarning.",
     {"new_name": "test_new_name", "old_lookup_warns": "test_old_lookup_warns",
      "old_override_warns": "test_old_override_warns", "sender_uses_new": "test_sender_uses_new"},
     {f"{PKG}/config/settings.py": d(r'''
        """Platform settings with environment-style overrides."""

        DEFAULTS = {"mail_host": "localhost", "smtp_port": 25, "max_cart_items": 50, "currency": "USD"}
        _overrides: dict = {}


        def configure(**values):
            _overrides.update(values)


        def reset():
            _overrides.clear()


        def get_setting(name):
            if name in _overrides:
                return _overrides[name]
            return DEFAULTS[name]
        '''),
      f"{PKG}/notifications/email/sender.py": d(r'''
        from shopcore.config.settings import get_setting


        def connection_info():
            return {"host": get_setting("mail_host"), "port": get_setting("smtp_port")}
        ''')},
     d(r'''
        import pathlib
        import warnings

        import pytest

        from shopcore.config import settings
        from shopcore.notifications.email.sender import connection_info


        def test_new_name():
            settings.reset()
            with warnings.catch_warnings():
                warnings.simplefilter("error", DeprecationWarning)
                assert settings.get_setting("smtp_host") == "localhost"
                settings.configure(smtp_host="mx.example")
                assert settings.get_setting("smtp_host") == "mx.example"
            settings.reset()


        def test_old_lookup_warns():
            settings.reset()
            with pytest.warns(DeprecationWarning):
                assert settings.get_setting("mail_host") == "localhost"


        def test_old_override_warns():
            # The prompt doesn't say whether configure() or the lookup warns; either is fine.
            settings.reset()
            with pytest.warns(DeprecationWarning):
                settings.configure(mail_host="legacy.example")
                assert settings.get_setting("smtp_host") == "legacy.example"
            settings.reset()


        def test_sender_uses_new():
            settings.reset()
            with warnings.catch_warnings():
                warnings.simplefilter("error", DeprecationWarning)
                assert connection_info() == {"host": "localhost", "port": 25}
            src = pathlib.Path("shopcore/notifications/email/sender.py").read_text()
            assert "smtp_host" in src and "mail_host" not in src
        '''))

task("lr_pagination_skip", "retrieval",
     "Bug report: the orders list API skips the first page of results. Page 1 of /orders shows what should be "
     "page 2. Fix it.",
     {"first_page": "test_first_page", "last_partial_page": "test_last_partial_page",
      "out_of_range": "test_out_of_range", "handler_first_page": "test_handler_first_page"},
     {f"{PKG}/api/pagination.py": d(r'''
        def paginate(items, page, per_page):
            """Items for a 1-based page number."""
            if page < 1 or per_page < 1:
                return []
            start = page * per_page
            return items[start:start + per_page]
        ''')},
     d(r'''
        from shopcore.api.handlers.orders import list_orders
        from shopcore.api.pagination import paginate

        ITEMS = list(range(45))


        def test_first_page():
            assert paginate(ITEMS, 1, 20) == list(range(20))


        def test_last_partial_page():
            assert paginate(ITEMS, 3, 20) == list(range(40, 45))


        def test_out_of_range():
            assert paginate(ITEMS, 4, 20) == [] and paginate(ITEMS, 0, 20) == []


        def test_handler_first_page():
            assert list_orders(ITEMS, page=1, per_page=10)["items"] == list(range(10))
        '''))

task("lr_audit_actions", "requirements",
     "Security wants every destructive admin action audited the way create_user already is. Add audit records to "
     "cancel_order and refund_order (shopcore/api/handlers/orders.py) and delete_product "
     "(shopcore/api/handlers/catalog.py), with the event names order.cancel, order.refund and product.delete, the "
     "acting user as the actor and the order id or SKU as the target.",
     {"cancel_audited": "test_cancel_audited", "refund_audited": "test_refund_audited",
      "delete_audited": "test_delete_audited", "create_unchanged": "test_create_unchanged"},
     {f"{PKG}/api/handlers/orders.py": d(r'''
        from shopcore.api.pagination import paginate


        def list_orders(orders, page=1, per_page=20):
            return {"page": page, "items": paginate(orders, page, per_page)}


        def cancel_order(orders, order_id, actor):
            orders[order_id]["status"] = "cancelled"
            return orders[order_id]


        def refund_order(orders, order_id, actor):
            orders[order_id]["status"] = "refunded"
            return orders[order_id]
        '''),
      f"{PKG}/api/handlers/catalog.py": d(r'''
        def delete_product(products, sku, actor):
            products.pop(sku)
            return True
        ''')},
     d(r'''
        from shopcore.api.handlers.catalog import delete_product
        from shopcore.api.handlers.orders import cancel_order, refund_order
        from shopcore.api.handlers.users import create_user
        from shopcore.audit import LOG, clear


        def test_cancel_audited():
            clear()
            cancel_order({"o1": {"status": "open"}}, "o1", "alice")
            assert LOG == [{"event": "order.cancel", "actor": "alice", "target": "o1"}]


        def test_refund_audited():
            clear()
            refund_order({"o2": {"status": "paid"}}, "o2", "bob")
            assert LOG == [{"event": "order.refund", "actor": "bob", "target": "o2"}]


        def test_delete_audited():
            clear()
            delete_product({"sku9": {}}, "sku9", "carol")
            assert LOG == [{"event": "product.delete", "actor": "carol", "target": "sku9"}]


        def test_create_unchanged():
            clear()
            create_user({}, "u1", "dan")
            assert LOG == [{"event": "user.create", "actor": "dan", "target": "u1"}]
        '''))


if __name__ == "__main__":
    write(ROOT, TASKS)
    print(f"files per repo: {len(TASKS[0]['repo'])}")
