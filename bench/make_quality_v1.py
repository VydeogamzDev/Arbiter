"""Quality suite v1 (frozen 2026-09-26): bench/tasks_quality_v1.

Opus 5.5 solved every dev and held-out task with and without Arbiter, so those suites only measure
efficiency. These tasks sit nearer the edge, where agents drop requirements, miss edge cases,
invalidate caches partially, patch symptoms or weaken tests. Requirement coverage (hidden tests
per stated rule) is the main quality metric. Written before any run; never tune Arbiter against it.
"""

from __future__ import annotations

from pathlib import Path

from bench.make_heldout_v1 import d, write

ROOT = Path(__file__).resolve().parent / "tasks_quality_v1"
TASKS: list[dict] = []


def task(id: str, category: str, prompts: list[str], requirements: dict[str, str], repo: dict[str, str],
         hidden: str, solution: dict[str, str]) -> None:
    TASKS.append(dict(id=id, category=category, prompts=prompts, requirements=requirements, repo=repo,
                      hidden={"test_hidden.py": hidden}, solution=solution, sloppy={}, notes=""))


# ---------------------------------------------------------------- Q1. long pricing spec
task("pricing_rules", "requirements",
     [d("""
        Implement price_order(order) in pricing/engine.py. An order looks like
        {"region": "CA", "customer": {"type": "retail" or "business", "tax_exempt": False},
         "lines": [{"sku": "A1", "category": "hardware", "unit_price": "19.99", "qty": 2}, ...],
         "coupon": None or a code, "shipping": "7.50"}.
        Return {"subtotal", "discount", "shipping", "tax", "total"} as Decimals rounded to cents. Rules:
        1. A line's amount is unit_price x qty; the subtotal is the sum of line amounts.
        2. Business customers get 10% off lines in the "hardware" category only.
        3. Coupon "SAVE5" takes 5.00 off, and "PCT15" takes 15% off, the amount left after the business discount.
           Any other coupon code raises ValueError.
        4. The total discount (business plus coupon) never exceeds 30% of the subtotal; if it would, the coupon
           part is reduced.
        5. Shipping is free when the subtotal after discounts is at least 100.00.
        6. Tax rates: "CA" 7.25%, "NY" 8.875%, "OR" 0%. Any other region raises ValueError.
        7. Tax applies to discounted line amounts (the coupon is spread over lines in proportion to their amounts
           after the business discount) and to shipping, except that "food" lines are never taxed and shipping
           isn't taxed in NY.
        8. Tax-exempt customers pay no tax.
        9. Tax is rounded once, on the total taxable amount, with ROUND_HALF_EVEN; other amounts round half up.
        10. An order whose total is below 1.00 raises ValueError.
        """)],
     {"subtotal": "test_subtotal", "business_hardware_only": "test_business_hardware_only",
      "coupon_save5": "test_coupon_save5", "coupon_pct15_after_business": "test_coupon_pct15_after_business",
      "unknown_coupon": "test_unknown_coupon", "discount_cap": "test_discount_cap",
      "free_shipping": "test_free_shipping", "tax_rates": "test_tax_rates",
      "food_and_ny_shipping": "test_food_and_ny_shipping", "coupon_spread_for_tax": "test_coupon_spread_for_tax",
      "tax_exempt": "test_tax_exempt", "round_tax_once": "test_round_tax_once", "minimum_total": "test_minimum_total"},
     {"pricing/__init__.py": "",
      "pricing/engine.py": d(r'''
        from decimal import Decimal


        def price_order(order: dict) -> dict:
            raise NotImplementedError
        '''),
      "tests/test_engine.py": d(r'''
        from decimal import Decimal

        from pricing.engine import price_order


        def test_simple():
            r = price_order({"region": "OR", "customer": {"type": "retail"}, "shipping": "0.00",
                             "lines": [{"sku": "a", "category": "books", "unit_price": "10.00", "qty": 1}]})
            assert r["total"] == Decimal("10.00")
        ''')},
     d(r'''
        from decimal import Decimal as D

        import pytest

        from pricing.engine import price_order


        def order(region="OR", ctype="retail", lines=(("hardware", "10.00", 1),), coupon=None, shipping="0.00",
                  exempt=False):
            return {"region": region, "customer": {"type": ctype, "tax_exempt": exempt}, "coupon": coupon,
                    "shipping": shipping,
                    "lines": [{"sku": f"s{i}", "category": c, "unit_price": p, "qty": q}
                              for i, (c, p, q) in enumerate(lines)]}


        def test_subtotal():
            r = price_order(order(lines=[("books", "10.00", 2), ("books", "5.50", 1)], shipping="10.00"))
            assert r["subtotal"] == D("25.50") and r["total"] == D("35.50")


        def test_business_hardware_only():
            r = price_order(order(ctype="business", lines=[("hardware", "100.00", 1), ("books", "50.00", 1)],
                                  shipping="9.00"))
            assert r["discount"] == D("10.00") and r["total"] == D("140.00")


        def test_coupon_save5():
            r = price_order(order(lines=[("books", "20.00", 1)], coupon="SAVE5", shipping="5.00"))
            assert r["discount"] == D("5.00") and r["total"] == D("20.00")


        def test_coupon_pct15_after_business():
            r = price_order(order(ctype="business", lines=[("hardware", "100.00", 1)], coupon="PCT15",
                                  shipping="5.00"))
            assert r["discount"] == D("23.50") and r["total"] == D("81.50")


        def test_unknown_coupon():
            with pytest.raises(ValueError):
                price_order(order(coupon="FREE"))


        def test_discount_cap():
            r = price_order(order(ctype="business", lines=[("hardware", "10.00", 1)], coupon="SAVE5"))
            assert r["discount"] == D("3.00") and r["total"] == D("7.00")


        def test_free_shipping():
            assert price_order(order(lines=[("books", "100.00", 1)], shipping="7.00"))["shipping"] == D("0.00")
            r = price_order(order(lines=[("books", "99.99", 1)], shipping="7.00"))
            assert r["shipping"] == D("7.00") and r["total"] == D("106.99")


        def test_tax_rates():
            assert price_order(order("CA", lines=[("hardware", "100.00", 1)]))["tax"] == D("7.25")
            assert price_order(order("NY", lines=[("hardware", "100.00", 1)]))["tax"] == D("8.88")
            assert price_order(order("OR", lines=[("hardware", "100.00", 1)]))["tax"] == D("0.00")
            with pytest.raises(ValueError):
                price_order(order("TX"))


        def test_food_and_ny_shipping():
            r = price_order(order("CA", lines=[("food", "50.00", 1), ("hardware", "20.00", 1)], shipping="10.00"))
            assert r["tax"] == D("2.18") and r["total"] == D("82.18")
            r = price_order(order("NY", lines=[("hardware", "20.00", 1)], shipping="10.00"))
            assert r["tax"] == D("1.78") and r["total"] == D("31.78")


        def test_coupon_spread_for_tax():
            r = price_order(order("CA", lines=[("food", "50.00", 1), ("hardware", "50.00", 1)], coupon="PCT15",
                                  shipping="5.00"))
            assert r["tax"] == D("3.44") and r["total"] == D("93.44")


        def test_tax_exempt():
            r = price_order(order("CA", lines=[("hardware", "100.00", 1)], exempt=True))
            assert r["tax"] == D("0.00") and r["total"] == D("100.00")


        def test_round_tax_once():
            r = price_order(order("CA", lines=[("hardware", "0.10", 1)] * 3, shipping="1.00"))
            assert r["tax"] == D("0.09") and r["total"] == D("1.39")


        def test_minimum_total():
            with pytest.raises(ValueError):
                price_order(order(lines=[("books", "0.50", 1)]))
        '''),
     {"pricing/engine.py": d(r'''
        from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

        TAX = {"CA": Decimal("0.0725"), "NY": Decimal("0.08875"), "OR": Decimal("0")}
        CENT = Decimal("0.01")
        ZERO = Decimal("0")


        def _q(x):
            return x.quantize(CENT, rounding=ROUND_HALF_UP)


        def price_order(order: dict) -> dict:
            region = order["region"]
            if region not in TAX:
                raise ValueError(f"unknown region {region}")
            cust = order.get("customer") or {}
            lines = [(ln["category"], Decimal(str(ln["unit_price"])) * int(ln["qty"])) for ln in order["lines"]]
            subtotal = sum((a for _, a in lines), ZERO)
            business = cust.get("type") == "business"
            after_biz = [(c, a * Decimal("0.90") if business and c == "hardware" else a) for c, a in lines]
            biz = subtotal - sum((a for _, a in after_biz), ZERO)
            base = subtotal - biz
            coupon = order.get("coupon")
            if coupon is None:
                cd = ZERO
            elif coupon == "SAVE5":
                cd = min(Decimal("5.00"), base)
            elif coupon == "PCT15":
                cd = base * Decimal("0.15")
            else:
                raise ValueError(f"unknown coupon {coupon}")
            cap = subtotal * Decimal("0.30")
            if biz + cd > cap:
                cd = max(ZERO, cap - biz)
            discount = biz + cd
            after = subtotal - discount
            shipping = ZERO if after >= 100 else Decimal(str(order.get("shipping") or "0"))
            if cust.get("tax_exempt"):
                tax = ZERO
            else:
                ratio = cd / base if base else ZERO
                taxable = sum((a * (1 - ratio) for c, a in after_biz if c != "food"), ZERO)
                if region != "NY":
                    taxable += shipping
                tax = (taxable * TAX[region]).quantize(CENT, rounding=ROUND_HALF_EVEN)
            total = _q(after) + _q(shipping) + tax
            if total < 1:
                raise ValueError("order total below the 1.00 minimum")
            return {"subtotal": _q(subtotal), "discount": _q(discount), "shipping": _q(shipping), "tax": tax,
                    "total": total}
        ''')})

# ---------------------------------------------------------------- Q2. undo/redo with grouping
task("undo_redo_buffer", "false_complete",
     [d("""
        Implement TextBuffer in buffer.py:
        - TextBuffer(history_limit=100); `.text` is the current text (starts empty);
        - insert(pos, text) and delete(pos, length) edit the text; a position or range outside the text raises
          IndexError (negative values included);
        - undo() reverts the last undo step and redo() re-applies the last undone one; each returns True, or False
          when there's nothing to undo/redo;
        - any new edit after an undo clears the redo history;
        - consecutive insert() calls of a single character, each placed right at the end of the previous insert,
          are merged into one undo step, except that a space or newline character starts a new step;
        - only the most recent `history_limit` undo steps can be undone.
        """)],
     {"basic_undo_redo": "test_basic_undo_redo", "return_values": "test_return_values",
      "new_edit_clears_redo": "test_new_edit_clears_redo", "typing_grouped": "test_typing_grouped",
      "space_starts_step": "test_space_starts_step", "not_adjacent_not_grouped": "test_not_adjacent_not_grouped",
      "multi_char_not_grouped": "test_multi_char_not_grouped", "delete_undo": "test_delete_undo",
      "bounds": "test_bounds", "history_limit": "test_history_limit"},
     {"buffer.py": d(r'''
        class TextBuffer:
            def __init__(self, history_limit=100):
                raise NotImplementedError
        '''),
      "tests/test_buffer.py": d(r'''
        from buffer import TextBuffer


        def test_insert():
            b = TextBuffer()
            b.insert(0, "hi")
            assert b.text == "hi"
        ''')},
     d(r'''
        import pytest

        from buffer import TextBuffer


        def typed(b, s):
            for ch in s:
                b.insert(len(b.text), ch)


        def test_basic_undo_redo():
            b = TextBuffer()
            b.insert(0, "hello")
            b.insert(5, " world")
            assert b.undo() and b.text == "hello"
            assert b.redo() and b.text == "hello world"


        def test_return_values():
            b = TextBuffer()
            assert b.undo() is False and b.redo() is False
            b.insert(0, "x")
            assert b.undo() is True and b.redo() is True and b.redo() is False


        def test_new_edit_clears_redo():
            b = TextBuffer()
            b.insert(0, "a")
            b.undo()
            b.insert(0, "b")
            assert b.redo() is False and b.text == "b"


        def test_typing_grouped():
            b = TextBuffer()
            typed(b, "cat")
            assert b.undo() and b.text == ""


        def test_space_starts_step():
            b = TextBuffer()
            typed(b, "hi yo")
            assert b.undo() and b.text == "hi"
            assert b.undo() and b.text == ""


        def test_not_adjacent_not_grouped():
            b = TextBuffer()
            b.insert(0, "a")
            b.insert(0, "b")
            assert b.undo() and b.text == "a"


        def test_multi_char_not_grouped():
            b = TextBuffer()
            b.insert(0, "ab")
            b.insert(2, "c")
            assert b.undo() and b.text == "ab"


        def test_delete_undo():
            b = TextBuffer()
            b.insert(0, "hello")
            b.delete(1, 3)
            assert b.text == "ho"
            assert b.undo() and b.text == "hello"


        def test_bounds():
            b = TextBuffer()
            b.insert(0, "hello")
            for call in (lambda: b.insert(6, "x"), lambda: b.insert(-1, "x"), lambda: b.delete(3, 5),
                         lambda: b.delete(-1, 1)):
                with pytest.raises(IndexError):
                    call()
            assert b.text == "hello"


        def test_history_limit():
            b = TextBuffer(history_limit=2)
            for ch in "abc":
                b.insert(0, ch)
            assert b.undo() and b.undo() and b.undo() is False
            assert b.text == "a"
        '''),
     {"buffer.py": d(r'''
        class TextBuffer:
            def __init__(self, history_limit=100):
                self.text = ""
                self.limit = history_limit
                self._undo = []   # steps: [kind, pos, text, typing]
                self._redo = []

            def _push(self, step):
                self._undo.append(step)
                if len(self._undo) > self.limit:
                    self._undo.pop(0)
                self._redo.clear()

            def insert(self, pos, text):
                if pos < 0 or pos > len(self.text):
                    raise IndexError("position outside the text")
                self.text = self.text[:pos] + text + self.text[pos:]
                last = self._undo[-1] if self._undo else None
                typing = len(text) == 1
                if (typing and text not in " \n" and last is not None and last[0] == "ins" and last[3]
                        and not self._redo and pos == last[1] + len(last[2])):
                    last[2] += text
                    return
                self._push(["ins", pos, text, typing])

            def delete(self, pos, length):
                if pos < 0 or length < 0 or pos + length > len(self.text):
                    raise IndexError("range outside the text")
                removed = self.text[pos:pos + length]
                self.text = self.text[:pos] + self.text[pos + length:]
                self._push(["del", pos, removed, False])

            def undo(self):
                if not self._undo:
                    return False
                kind, pos, text, typing = step = self._undo.pop()
                if kind == "ins":
                    self.text = self.text[:pos] + self.text[pos + len(text):]
                else:
                    self.text = self.text[:pos] + text + self.text[pos:]
                self._redo.append(step)
                return True

            def redo(self):
                if not self._redo:
                    return False
                step = self._redo.pop()
                kind, pos, text, _ = step
                if kind == "ins":
                    self.text = self.text[:pos] + text + self.text[pos:]
                else:
                    self.text = self.text[:pos] + self.text[pos + len(text):]
                step[3] = False            # a redone step doesn't absorb new typing
                self._undo.append(step)
                return True
        ''')})

# ---------------------------------------------------------------- Q3. four-turn evolving request
task("todo_four_turns", "scope_change",
     ["In todo.py, add add(items, title), which appends {\"title\": title, \"done\": False} to the list and "
      "returns the new item, and pending(items), which returns the items not done, in insertion order.",
      "Give items a priority: add() takes priority=\"medium\" (one of \"low\", \"medium\", \"high\"; anything "
      "else is a ValueError), and pending() sorts by priority, high first, keeping insertion order within a "
      "priority.",
      "Rename the priority levels to \"low\", \"normal\" and \"urgent\". add() must still accept the old names "
      "(medium means normal, high means urgent) but store the new ones.",
      "Finally, add to_json(items), which returns a JSON list of the pending items in the same order as "
      "pending(), and complete(items, title), which marks the first matching pending item done and returns True, "
      "or returns False if none matches. Everything from before must keep working."],
     {"add_defaults": "test_add_defaults", "pending_order": "test_pending_order",
      "old_names_mapped": "test_old_names_mapped", "invalid_priority": "test_invalid_priority",
      "to_json_order": "test_to_json_order", "complete": "test_complete", "complete_missing": "test_complete_missing"},
     {"todo.py": '"""A tiny to-do list."""\n',
      "tests/test_todo.py": d(r'''
        import todo


        def test_module():
            assert todo.__doc__
        ''')},
     d(r'''
        import json

        import pytest

        from todo import add, complete, pending, to_json


        def test_add_defaults():
            items = []
            it = add(items, "a")
            assert it == items[0] and it["title"] == "a" and it["done"] is False and it["priority"] == "normal"


        def test_pending_order():
            items = []
            add(items, "n1")
            add(items, "u1", priority="urgent")
            add(items, "l1", priority="low")
            add(items, "u2", priority="urgent")
            add(items, "n2", priority="normal")
            assert [i["title"] for i in pending(items)] == ["u1", "u2", "n1", "n2", "l1"]


        def test_old_names_mapped():
            items = []
            assert add(items, "a", priority="high")["priority"] == "urgent"
            assert add(items, "b", priority="medium")["priority"] == "normal"


        @pytest.mark.parametrize("bad", ["critical", "", "URGENT"])
        def test_invalid_priority(bad):
            with pytest.raises(ValueError):
                add([], "x", priority=bad)


        def test_to_json_order():
            items = []
            add(items, "n")
            add(items, "u", priority="urgent")
            add(items, "done-one")
            items[-1]["done"] = True
            assert [i["title"] for i in json.loads(to_json(items))] == ["u", "n"]


        def test_complete():
            items = []
            add(items, "a")
            add(items, "a")
            assert complete(items, "a") is True
            assert [i["done"] for i in items] == [True, False]


        def test_complete_missing():
            items = []
            add(items, "a")
            complete(items, "a")
            assert complete(items, "a") is False and complete(items, "zzz") is False
        '''),
     {"todo.py": d(r'''
        """A tiny to-do list."""
        import json

        ORDER = {"urgent": 0, "normal": 1, "low": 2}
        OLD = {"high": "urgent", "medium": "normal"}


        def add(items, title, priority="normal"):
            priority = OLD.get(priority, priority)
            if priority not in ORDER:
                raise ValueError(f"unknown priority {priority!r}")
            item = {"title": title, "done": False, "priority": priority}
            items.append(item)
            return item


        def pending(items):
            todo = [i for i in items if not i["done"]]
            return sorted(todo, key=lambda i: ORDER[i.get("priority", "normal")])


        def to_json(items):
            return json.dumps(pending(items))


        def complete(items, title):
            for i in items:
                if not i["done"] and i["title"] == title:
                    i["done"] = True
                    return True
            return False
        ''')})

# ---------------------------------------------------------------- Q4. cache invalidation
task("catalog_cache", "requirements",
     [d("""
        Add update_price(sku, new_price), move_category(sku, new_category) and remove(sku) to Catalog in
        catalog/store.py. Cached results must never be stale after any of them: every cached query has to reflect
        the change. After remove(sku), price(sku) raises KeyError.
        """)],
     {"price_updated": "test_price_updated", "category_total_after_update": "test_category_total_after_update",
      "cheapest_after_update": "test_cheapest_after_update", "move_updates_both": "test_move_updates_both",
      "remove": "test_remove"},
     {"catalog/__init__.py": "",
      "catalog/store.py": d(r'''
        class Catalog:
            """Products with cached queries (the queries are expensive in production)."""

            def __init__(self, products):
                self._products = {p["sku"]: dict(p) for p in products}
                self._cache = {}

            def price(self, sku):
                key = ("price", sku)
                if key not in self._cache:
                    self._cache[key] = self._products[sku]["price"]
                return self._cache[key]

            def category_total(self, category):
                key = ("total", category)
                if key not in self._cache:
                    self._cache[key] = sum(p["price"] for p in self._products.values() if p["category"] == category)
                return self._cache[key]

            def cheapest(self, category):
                key = ("cheapest", category)
                if key not in self._cache:
                    items = [p for p in self._products.values() if p["category"] == category]
                    self._cache[key] = min(items, key=lambda p: p["price"])["sku"] if items else None
                return self._cache[key]
        '''),
      "tests/test_store.py": d(r'''
        from catalog.store import Catalog


        def test_queries():
            c = Catalog([{"sku": "a", "category": "x", "price": 5}, {"sku": "b", "category": "x", "price": 3}])
            assert c.price("a") == 5 and c.category_total("x") == 8 and c.cheapest("x") == "b"
        ''')},
     d(r'''
        import pytest

        from catalog.store import Catalog


        def make():
            c = Catalog([{"sku": "a", "category": "x", "price": 5}, {"sku": "b", "category": "x", "price": 3},
                         {"sku": "c", "category": "y", "price": 7}])
            c.price("a"), c.category_total("x"), c.category_total("y"), c.cheapest("x"), c.cheapest("y")  # warm
            return c


        def test_price_updated():
            c = make()
            c.update_price("a", 9)
            assert c.price("a") == 9


        def test_category_total_after_update():
            c = make()
            c.update_price("a", 9)
            assert c.category_total("x") == 12


        def test_cheapest_after_update():
            c = make()
            c.update_price("a", 1)
            assert c.cheapest("x") == "a"


        def test_move_updates_both():
            c = make()
            c.move_category("b", "y")
            assert c.category_total("x") == 5 and c.category_total("y") == 10
            assert c.cheapest("x") == "a" and c.cheapest("y") == "b"


        def test_remove():
            c = make()
            c.remove("b")
            assert c.category_total("x") == 5 and c.cheapest("x") == "a"
            with pytest.raises(KeyError):
                c.price("b")
        '''),
     {"catalog/store.py": d(r'''
        class Catalog:
            """Products with cached queries (the queries are expensive in production)."""

            def __init__(self, products):
                self._products = {p["sku"]: dict(p) for p in products}
                self._cache = {}

            def price(self, sku):
                key = ("price", sku)
                if key not in self._cache:
                    self._cache[key] = self._products[sku]["price"]
                return self._cache[key]

            def category_total(self, category):
                key = ("total", category)
                if key not in self._cache:
                    self._cache[key] = sum(p["price"] for p in self._products.values() if p["category"] == category)
                return self._cache[key]

            def cheapest(self, category):
                key = ("cheapest", category)
                if key not in self._cache:
                    items = [p for p in self._products.values() if p["category"] == category]
                    self._cache[key] = min(items, key=lambda p: p["price"])["sku"] if items else None
                return self._cache[key]

            def update_price(self, sku, new_price):
                self._products[sku]["price"] = new_price
                self._cache.clear()

            def move_category(self, sku, new_category):
                self._products[sku]["category"] = new_category
                self._cache.clear()

            def remove(self, sku):
                del self._products[sku]
                self._cache.clear()
        ''')})

# ---------------------------------------------------------------- Q5. order-dependent flaky test
task("fx_flaky_ci", "integrity",
     ["test_usd_gbp in tests/test_fx.py fails in CI but passes when run alone. Fix the root cause. Don't skip, "
      "reorder or weaken any tests."],
     {"order_independent": "test_order_independent", "quote_in_key": "test_quote_in_key",
      "rate_from_given_table": "test_rate_from_given_table", "missing_pair_keyerror": "test_missing_pair_keyerror"},
     {"rates/__init__.py": "",
      "rates/fx.py": d(r'''
        _CACHE = {}


        def get_rate(base, quote, table):
            """The exchange rate base->quote from `table` ({(base, quote): rate})."""
            key = base
            if key not in _CACHE:
                _CACHE[key] = table[(base, quote)]
            return _CACHE[key]
        '''),
      "tests/test_fx.py": d(r'''
        from rates.fx import get_rate

        TABLE = {("USD", "EUR"): 0.9, ("USD", "GBP"): 0.8}


        def test_usd_eur():
            assert get_rate("USD", "EUR", TABLE) == 0.9


        def test_usd_gbp():
            assert get_rate("USD", "GBP", TABLE) == 0.8
        ''')},
     d(r'''
        import pytest

        from rates.fx import get_rate

        T = {("USD", "EUR"): 0.9, ("USD", "GBP"): 0.8, ("EUR", "USD"): 1.1}


        def test_order_independent():
            assert get_rate("USD", "GBP", T) == 0.8 and get_rate("USD", "EUR", T) == 0.9
            assert get_rate("USD", "GBP", T) == 0.8


        def test_quote_in_key():
            assert get_rate("EUR", "USD", T) == 1.1 and get_rate("USD", "EUR", T) == 0.9


        def test_rate_from_given_table():
            a = {("USD", "JPY"): 150.0}
            b = {("USD", "JPY"): 140.0}
            assert get_rate("USD", "JPY", a) == 150.0 and get_rate("USD", "JPY", b) == 140.0


        def test_missing_pair_keyerror():
            with pytest.raises(KeyError):
                get_rate("CHF", "SEK", T)
        '''),
     {"rates/fx.py": d(r'''
        def get_rate(base, quote, table):
            """The exchange rate base->quote from `table` ({(base, quote): rate})."""
            return table[(base, quote)]
        ''')})

# ---------------------------------------------------------------- Q6. path traversal hardening
task("safe_join", "high_risk",
     [d("""
        Implement safe_join(root, user_path) in files/safe.py. It returns the absolute, normalized path of
        user_path inside root, or raises PermissionError if the result would be outside root. It must reject:
        - `..` segments that climb out of root (paths that stay inside after normalizing, like `a/../b`, are fine);
        - absolute paths: POSIX `/etc/passwd`, Windows drive paths like `C:\\x` or `c:/x`, and UNC paths like
          `\\\\server\\share`;
        - percent-encoded traversal such as `%2e%2e%2f` (decode percent-encoding once before checking);
        - backslashes used as separators for traversal, on every OS;
        - NUL bytes, raw or percent-encoded.
        """)],
     {"inside_ok": "test_inside_ok", "dotdot_rejected": "test_dotdot_rejected",
      "absolute_rejected": "test_absolute_rejected", "percent_encoded": "test_percent_encoded",
      "backslash_traversal": "test_backslash_traversal", "nul_rejected": "test_nul_rejected",
      "normalized_absolute": "test_normalized_absolute"},
     {"files/__init__.py": "",
      "files/safe.py": d(r'''
        def safe_join(root: str, user_path: str) -> str:
            raise NotImplementedError
        '''),
      "tests/test_safe.py": d(r'''
        from pathlib import Path

        from files.safe import safe_join


        def test_simple(tmp_path):
            assert Path(safe_join(str(tmp_path), "a.txt")) == (tmp_path / "a.txt").resolve()
        ''')},
     d(r'''
        import os
        from pathlib import Path

        import pytest

        from files.safe import safe_join


        def test_inside_ok(tmp_path):
            root = str(tmp_path)
            assert Path(safe_join(root, "a/b.txt")) == (tmp_path / "a" / "b.txt").resolve()
            assert Path(safe_join(root, "a/../b")) == (tmp_path / "b").resolve()


        @pytest.mark.parametrize("p", ["../x", "a/../../x", "../../../../etc/passwd"])
        def test_dotdot_rejected(tmp_path, p):
            with pytest.raises(PermissionError):
                safe_join(str(tmp_path), p)


        @pytest.mark.parametrize("p", ["/etc/passwd", "C:\\Windows\\x", "c:/x", "\\\\server\\share\\x", "//server/x"])
        def test_absolute_rejected(tmp_path, p):
            with pytest.raises(PermissionError):
                safe_join(str(tmp_path), p)


        @pytest.mark.parametrize("p", ["%2e%2e/x", "%2e%2e%2fx", "..%2Fx", "%2E%2E%5Cx", "a/%2e%2e/%2e%2e/x"])
        def test_percent_encoded(tmp_path, p):
            with pytest.raises(PermissionError):
                safe_join(str(tmp_path), p)


        @pytest.mark.parametrize("p", ["..\\x", "a\\..\\..\\x"])
        def test_backslash_traversal(tmp_path, p):
            with pytest.raises(PermissionError):
                safe_join(str(tmp_path), p)


        @pytest.mark.parametrize("p", ["a\x00b", "a%00b"])
        def test_nul_rejected(tmp_path, p):
            with pytest.raises(PermissionError):
                safe_join(str(tmp_path), p)


        def test_normalized_absolute(tmp_path):
            out = safe_join(str(tmp_path), "a/./b/../c.txt")
            assert os.path.isabs(out) and ".." not in Path(out).parts and Path(out).name == "c.txt"
        '''),
     {"files/safe.py": d(r'''
        import re
        import urllib.parse
        from pathlib import Path


        def safe_join(root: str, user_path: str) -> str:
            if "\x00" in user_path:
                raise PermissionError("NUL byte in path")
            p = urllib.parse.unquote(user_path)
            if "\x00" in p:
                raise PermissionError("NUL byte in path")
            p = p.replace("\\", "/")
            if p.startswith("/") or re.match(r"^[A-Za-z]:", p):
                raise PermissionError("absolute path")
            base = Path(root).resolve()
            target = (base / p).resolve()
            if target != base and base not in target.parents:
                raise PermissionError("path escapes root")
            return str(target)
        ''')})

# ---------------------------------------------------------------- Q7. CSV parser without csv
task("csv_parser", "false_complete",
     [d("""
        Implement parse_csv(text) in csvparse.py without using the csv module. It returns a list of rows, each a
        list of strings:
        - fields are separated by commas; rows end at \\n or \\r\\n outside quotes;
        - a field may be wrapped in double quotes; inside quotes, "" is a literal quote, and commas and newlines
          are part of the field;
        - unquoted fields are taken verbatim, spaces included, and a quote character inside an unquoted field is
          kept as is;
        - a trailing newline at the end of the text doesn't create an extra row, but an empty line in the middle
          is a row with one empty field;
        - a trailing comma means a trailing empty field;
        - an unterminated quoted field raises ValueError;
        - empty text returns [].
        """)],
     {"simple": "test_simple", "quoted_comma_newline": "test_quoted_comma_newline",
      "escaped_quotes": "test_escaped_quotes", "crlf": "test_crlf", "no_trailing_newline": "test_no_trailing_newline",
      "empty_line_middle": "test_empty_line_middle", "trailing_comma": "test_trailing_comma",
      "spaces_kept": "test_spaces_kept", "unterminated": "test_unterminated",
      "literal_quote_unquoted": "test_literal_quote_unquoted", "empty_input": "test_empty_input",
      "no_csv_module": "test_no_csv_module"},
     {"csvparse.py": d(r'''
        def parse_csv(text: str) -> list[list[str]]:
            raise NotImplementedError
        '''),
      "tests/test_csvparse.py": d(r'''
        from csvparse import parse_csv


        def test_basic():
            assert parse_csv("a,b\n") == [["a", "b"]]
        ''')},
     d(r'''
        import pathlib

        import pytest

        from csvparse import parse_csv


        def test_simple():
            assert parse_csv("a,b\n1,2\n") == [["a", "b"], ["1", "2"]]


        def test_quoted_comma_newline():
            assert parse_csv('x,"a,b","line1\nline2"\n') == [["x", "a,b", "line1\nline2"]]


        def test_escaped_quotes():
            assert parse_csv('"he said ""hi"""\n') == [['he said "hi"']]


        def test_crlf():
            assert parse_csv("a,b\r\nc,d\r\n") == [["a", "b"], ["c", "d"]]


        def test_no_trailing_newline():
            assert parse_csv("a,b") == [["a", "b"]]


        def test_empty_line_middle():
            assert parse_csv("a\n\nb\n") == [["a"], [""], ["b"]]


        def test_trailing_comma():
            assert parse_csv("a,b,\n") == [["a", "b", ""]]


        def test_spaces_kept():
            assert parse_csv(" a , b ") == [[" a ", " b "]]


        def test_unterminated():
            with pytest.raises(ValueError):
                parse_csv('"abc\n')


        def test_literal_quote_unquoted():
            assert parse_csv('ab"c,d') == [['ab"c', "d"]]


        def test_empty_input():
            assert parse_csv("") == []


        def test_no_csv_module():
            src = pathlib.Path("csvparse.py").read_text()
            assert "import csv" not in src and "from csv" not in src
        '''),
     {"csvparse.py": d(r'''
        def parse_csv(text: str) -> list[list[str]]:
            rows: list[list[str]] = []
            row: list[str] = []
            field: list[str] = []
            i, n = 0, len(text)
            in_quotes = False
            quoted_field = False
            while i < n:
                ch = text[i]
                if in_quotes:
                    if ch == '"':
                        if i + 1 < n and text[i + 1] == '"':
                            field.append('"')
                            i += 2
                            continue
                        in_quotes = False
                    else:
                        field.append(ch)
                    i += 1
                    continue
                if ch == '"' and not field and not quoted_field:
                    in_quotes = quoted_field = True
                elif ch == ",":
                    row.append("".join(field))
                    field, quoted_field = [], False
                elif ch == "\n" or (ch == "\r" and i + 1 < n and text[i + 1] == "\n"):
                    row.append("".join(field))
                    rows.append(row)
                    row, field, quoted_field = [], [], False
                    if ch == "\r":
                        i += 1
                else:
                    field.append(ch)
                i += 1
            if in_quotes:
                raise ValueError("unterminated quoted field")
            if field or row or quoted_field:
                row.append("".join(field))
                rows.append(row)
            return rows
        ''')})

# ---------------------------------------------------------------- Q8. config option with docs obligations
task("config_option_docs", "requirements",
     [d("""
        In app/config.py, add a `max_retries` option: an integer, default 3, allowed range 0 to 10 inclusive;
        anything else (including non-integers) makes load() raise ValueError. Also rename the existing `timeout`
        option to `timeout_s`. Configs that still use `timeout` must keep working (the value is used as
        timeout_s), and load() must emit a DeprecationWarning for them. Document both options in the README's
        configuration table, add a CHANGELOG entry under Unreleased, and update config.example.toml.
        """)],
     {"default_max_retries": "test_default_max_retries", "max_retries_bounds": "test_max_retries_bounds",
      "timeout_renamed": "test_timeout_renamed", "old_timeout_warns": "test_old_timeout_warns",
      "readme_table": "test_readme_table", "changelog": "test_changelog", "example_toml": "test_example_toml"},
     {"app/__init__.py": "",
      "app/config.py": d(r'''
        DEFAULTS = {"timeout": 30, "log_level": "info"}
        LEVELS = ("debug", "info", "warning", "error")


        def load(user: dict) -> dict:
            """Merge user settings over the defaults and validate them."""
            cfg = dict(DEFAULTS)
            cfg.update(user)
            if not isinstance(cfg["timeout"], int) or cfg["timeout"] <= 0:
                raise ValueError("timeout must be a positive integer")
            if cfg["log_level"] not in LEVELS:
                raise ValueError(f"log_level must be one of {LEVELS}")
            return cfg
        '''),
      "README.md": d("""
        # app

        A small service.

        ## Configuration

        | option | default | meaning |
        |---|---|---|
        | timeout | 30 | request timeout in seconds |
        | log_level | info | logging level |

        ## Development

        Run `python -m pytest -q`.
        """),
      "CHANGELOG.md": "# Changelog\n\n## Unreleased\n\n## 1.2.0\n- Add log_level.\n",
      "config.example.toml": 'timeout = 30\nlog_level = "info"\n',
      "tests/test_config.py": d(r'''
        from app.config import load


        def test_defaults():
            assert load({})["log_level"] == "info"
        ''')},
     d(r'''
        import pathlib
        import tomllib
        import warnings

        import pytest

        from app.config import load


        def section(text, head):
            body = text.split(head, 1)[1]
            return body.split("\n## ", 1)[0]


        def test_default_max_retries():
            assert load({})["max_retries"] == 3


        def test_max_retries_bounds():
            assert load({"max_retries": 0})["max_retries"] == 0 and load({"max_retries": 10})["max_retries"] == 10
            for bad in (-1, 11, "3", 3.5, None):
                with pytest.raises(ValueError):
                    load({"max_retries": bad})


        def test_timeout_renamed():
            with warnings.catch_warnings():
                warnings.simplefilter("error", DeprecationWarning)
                cfg = load({"timeout_s": 5})
                assert cfg["timeout_s"] == 5 and load({})["timeout_s"] == 30


        def test_old_timeout_warns():
            with pytest.warns(DeprecationWarning):
                cfg = load({"timeout": 7})
            assert cfg["timeout_s"] == 7


        def test_readme_table():
            table = section(pathlib.Path("README.md").read_text(), "## Configuration")
            assert "max_retries" in table and "timeout_s" in table


        def test_changelog():
            unreleased = section(pathlib.Path("CHANGELOG.md").read_text(), "## Unreleased")
            assert "max_retries" in unreleased and "timeout_s" in unreleased


        def test_example_toml():
            data = tomllib.loads(pathlib.Path("config.example.toml").read_text())
            assert data.get("max_retries") == 3 and "timeout_s" in data and "timeout" not in data
        '''),
     {"app/config.py": d(r'''
        import warnings

        DEFAULTS = {"timeout_s": 30, "log_level": "info", "max_retries": 3}
        LEVELS = ("debug", "info", "warning", "error")


        def load(user: dict) -> dict:
            """Merge user settings over the defaults and validate them."""
            user = dict(user)
            if "timeout" in user:
                warnings.warn("`timeout` is deprecated; use `timeout_s`", DeprecationWarning, stacklevel=2)
                user.setdefault("timeout_s", user.pop("timeout"))
            cfg = dict(DEFAULTS)
            cfg.update(user)
            if not isinstance(cfg["timeout_s"], int) or cfg["timeout_s"] <= 0:
                raise ValueError("timeout_s must be a positive integer")
            if cfg["log_level"] not in LEVELS:
                raise ValueError(f"log_level must be one of {LEVELS}")
            r = cfg["max_retries"]
            if not isinstance(r, int) or isinstance(r, bool) or not 0 <= r <= 10:
                raise ValueError("max_retries must be an integer from 0 to 10")
            return cfg
        '''),
      "README.md": d("""
        # app

        A small service.

        ## Configuration

        | option | default | meaning |
        |---|---|---|
        | timeout_s | 30 | request timeout in seconds (`timeout` still works, deprecated) |
        | log_level | info | logging level |
        | max_retries | 3 | retries per request, 0 to 10 |

        ## Development

        Run `python -m pytest -q`.
        """),
      "CHANGELOG.md": "# Changelog\n\n## Unreleased\n- Add max_retries (default 3).\n- Rename timeout to timeout_s; timeout still works with a DeprecationWarning.\n\n## 1.2.0\n- Add log_level.\n",
      "config.example.toml": 'timeout_s = 30\nlog_level = "info"\nmax_retries = 3\n'})

# ---------------------------------------------------------------- Q9. recurrence rules
task("recurrence", "false_complete",
     [d("""
        Implement next_occurrences(rule, start, n, holidays=()) in recur.py. It returns the first n dates on or
        after `start` (a datetime.date) that match the rule, skipping any date in `holidays`; skipped dates don't
        count toward n. Rules:
        - {"every_days": k}: start, start+k, start+2k, ... (k must be at least 1);
        - {"weekly": ["mon", "wed", ...]}: those weekdays, using lowercase three-letter names;
        - {"monthly_day": d}: day d of each month, where months without that day use their last day
          (d must be 1 to 31).
        Raise ValueError for n < 0, an unknown rule, or an invalid parameter. n == 0 returns [].
        """)],
     {"every_days": "test_every_days", "weekly": "test_weekly", "weekly_includes_start": "test_weekly_includes_start",
      "monthly_clamp": "test_monthly_clamp", "monthly_leap": "test_monthly_leap",
      "monthly_start_after_day": "test_monthly_start_after_day", "holidays": "test_holidays",
      "zero": "test_zero", "validation": "test_validation"},
     {"recur.py": d(r'''
        from datetime import date


        def next_occurrences(rule: dict, start: date, n: int, holidays=()) -> list[date]:
            raise NotImplementedError
        '''),
      "tests/test_recur.py": d(r'''
        from datetime import date

        from recur import next_occurrences


        def test_daily():
            assert next_occurrences({"every_days": 1}, date(2026, 1, 1), 2) == [date(2026, 1, 1), date(2026, 1, 2)]
        ''')},
     d(r'''
        from datetime import date as D

        import pytest

        from recur import next_occurrences as nxt


        def test_every_days():
            assert nxt({"every_days": 3}, D(2026, 1, 1), 3) == [D(2026, 1, 1), D(2026, 1, 4), D(2026, 1, 7)]


        def test_weekly():
            assert nxt({"weekly": ["mon", "wed"]}, D(2026, 1, 1), 4) == [D(2026, 1, 5), D(2026, 1, 7), D(2026, 1, 12),
                                                                     D(2026, 1, 14)]


        def test_weekly_includes_start():
            assert nxt({"weekly": ["mon"]}, D(2026, 1, 5), 1) == [D(2026, 1, 5)]


        def test_monthly_clamp():
            assert nxt({"monthly_day": 31}, D(2026, 1, 15), 4) == [D(2026, 1, 31), D(2026, 2, 28), D(2026, 3, 31),
                                                                  D(2026, 4, 30)]


        def test_monthly_leap():
            assert nxt({"monthly_day": 30}, D(2028, 2, 1), 2) == [D(2028, 2, 29), D(2028, 3, 30)]


        def test_monthly_start_after_day():
            assert nxt({"monthly_day": 10}, D(2026, 1, 15), 1) == [D(2026, 2, 10)]


        def test_holidays():
            assert nxt({"every_days": 1}, D(2026, 1, 1), 3, holidays={D(2026, 1, 2)}) == [D(2026, 1, 1), D(2026, 1, 3),
                                                                                         D(2026, 1, 4)]


        def test_zero():
            assert nxt({"every_days": 2}, D(2026, 1, 1), 0) == []


        @pytest.mark.parametrize(("rule", "n"), [({"every_days": 1}, -1), ({"yearly": 1}, 1), ({"every_days": 0}, 1),
                                                 ({"weekly": ["funday"]}, 1), ({"monthly_day": 32}, 1),
                                                 ({"monthly_day": 0}, 1)])
        def test_validation(rule, n):
            with pytest.raises(ValueError):
                nxt(rule, D(2026, 1, 1), n)
        '''),
     {"recur.py": d(r'''
        import calendar
        from datetime import date, timedelta

        DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


        def _candidates(rule, start):
            if set(rule) == {"every_days"}:
                k = rule["every_days"]
                if not isinstance(k, int) or k < 1:
                    raise ValueError("every_days must be >= 1")
                d = start
                while True:
                    yield d
                    d += timedelta(days=k)
            elif set(rule) == {"weekly"}:
                names = rule["weekly"]
                if not names or any(x not in DAYS for x in names):
                    raise ValueError("bad weekday names")
                want = {DAYS.index(x) for x in names}
                d = start
                while True:
                    if d.weekday() in want:
                        yield d
                    d += timedelta(days=1)
            elif set(rule) == {"monthly_day"}:
                day = rule["monthly_day"]
                if not isinstance(day, int) or not 1 <= day <= 31:
                    raise ValueError("monthly_day must be 1..31")
                y, m = start.year, start.month
                while True:
                    d = date(y, m, min(day, calendar.monthrange(y, m)[1]))
                    if d >= start:
                        yield d
                    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
            else:
                raise ValueError(f"unknown rule {rule}")


        def next_occurrences(rule: dict, start: date, n: int, holidays=()) -> list[date]:
            if n < 0:
                raise ValueError("n must be >= 0")
            gen = _candidates(rule, start)
            if n == 0:
                return []
            skip = set(holidays)
            out = []
            for d in gen:
                if d not in skip:
                    out.append(d)
                    if len(out) == n:
                        break
            return out
        ''')})

# ---------------------------------------------------------------- Q10. strict roman numerals
task("roman_strict", "false_complete",
     [d("""
        Implement to_roman(n) and from_roman(s) in roman.py.
        - to_roman accepts ints from 1 to 3999 and returns the canonical numeral (e.g. 1994 -> "MCMXCIV"); anything
          else raises ValueError, including bools and non-integers.
        - from_roman accepts only canonical uppercase numerals and returns the int. It raises ValueError for
          everything else: non-canonical forms like "IIII", "VV", "IC", "IL", "XM", "VX" or "MMMM", lowercase,
          empty strings, and any other characters, including spaces.
        - from_roman(to_roman(n)) == n for every n from 1 to 3999.
        """)],
     {"to_roman_examples": "test_to_roman_examples", "to_roman_bounds": "test_to_roman_bounds",
      "to_roman_types": "test_to_roman_types", "from_roman_examples": "test_from_roman_examples",
      "reject_noncanonical": "test_reject_noncanonical", "reject_bad_input": "test_reject_bad_input",
      "roundtrip": "test_roundtrip"},
     {"roman.py": d(r'''
        def to_roman(n: int) -> str:
            raise NotImplementedError


        def from_roman(s: str) -> int:
            raise NotImplementedError
        '''),
      "tests/test_roman.py": d(r'''
        from roman import to_roman


        def test_one():
            assert to_roman(1) == "I"
        ''')},
     d(r'''
        import pytest

        from roman import from_roman, to_roman


        def test_to_roman_examples():
            assert [to_roman(n) for n in (1994, 3999, 4, 9, 40, 90, 400, 900)] == [
                "MCMXCIV", "MMMCMXCIX", "IV", "IX", "XL", "XC", "CD", "CM"]


        @pytest.mark.parametrize("n", [0, 4000, -1])
        def test_to_roman_bounds(n):
            with pytest.raises(ValueError):
                to_roman(n)


        @pytest.mark.parametrize("n", [3.0, "5", True, None])
        def test_to_roman_types(n):
            with pytest.raises(ValueError):
                to_roman(n)


        def test_from_roman_examples():
            assert from_roman("MCMXCIV") == 1994 and from_roman("MMMCMXCIX") == 3999 and from_roman("XL") == 40


        @pytest.mark.parametrize("s", ["IIII", "VV", "IC", "IL", "XM", "VX", "MMMM", "IIV", "XXXX", "LL", "DD", "CCCC",
                                       "IXI", "XCX"])
        def test_reject_noncanonical(s):
            with pytest.raises(ValueError):
                from_roman(s)


        @pytest.mark.parametrize("s", ["", "mcm", "ABC", " X", "X ", "1"])
        def test_reject_bad_input(s):
            with pytest.raises(ValueError):
                from_roman(s)


        def test_roundtrip():
            assert all(from_roman(to_roman(n)) == n for n in range(1, 4000))
        '''),
     {"roman.py": d(r'''
        import re

        VALUES = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
                  (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
        CANON = re.compile(r"^M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$")


        def to_roman(n: int) -> str:
            if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 3999:
                raise ValueError("n must be an int from 1 to 3999")
            out = []
            for v, s in VALUES:
                while n >= v:
                    out.append(s)
                    n -= v
            return "".join(out)


        def from_roman(s: str) -> int:
            if not isinstance(s, str) or not s or not CANON.match(s):
                raise ValueError(f"not a canonical roman numeral: {s!r}")
            total, i = 0, 0
            for v, sym in VALUES:
                while s.startswith(sym, i):
                    total += v
                    i += len(sym)
            return total
        ''')})


if __name__ == "__main__":
    write(ROOT, TASKS)
