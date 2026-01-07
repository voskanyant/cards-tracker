from datetime import datetime, date, time, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from core.models import Card, Client, Transaction, Withdrawal
from core.views import _closing_before, _withdraw_rows_for_day


class WithdrawalLogicTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(name="Client A")
        self.card = Card.objects.create(name="Card A")

    def _make_tx(self, day, amount):
        ts = timezone.make_aware(datetime.combine(day, time(10, 0)))
        return Transaction.objects.create(
            timestamp=ts,
            card=self.card,
            client=self.client_obj,
            amount_rub=Decimal(amount),
            amount_usd=Decimal("0"),
        )

    def test_closing_before_carries_remaining_without_full(self):
        day1 = date(2026, 1, 6)
        day2 = day1 + timedelta(days=1)
        self._make_tx(day1, "1000")
        Withdrawal.objects.create(
            date=day1,
            card=self.card,
            fully_withdrawn=False,
            withdrawn_rub=Decimal("200"),
            commission_rub=Decimal("50"),
        )
        remaining = _closing_before(self.card, day2)
        self.assertEqual(remaining, Decimal("750"))

    def test_closing_before_resets_after_full(self):
        day1 = date(2026, 1, 6)
        day2 = day1 + timedelta(days=1)
        self._make_tx(day1, "1000")
        Withdrawal.objects.create(
            date=day1,
            card=self.card,
            fully_withdrawn=True,
            withdrawn_rub=None,
            commission_rub=Decimal("0"),
        )
        remaining = _closing_before(self.card, day2)
        self.assertEqual(remaining, Decimal("0"))

    def test_withdraw_rows_does_not_create_records(self):
        day1 = date(2026, 1, 6)
        self._make_tx(day1, "500")
        _withdraw_rows_for_day(day1)
        self.assertEqual(Withdrawal.objects.count(), 0)
