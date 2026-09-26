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
