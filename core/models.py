from django.db import models


class Card(models.Model):
    name = models.CharField(max_length=120)
    bank = models.CharField(max_length=80, blank=True)
    card_number = models.CharField(max_length=32, blank=True)
    pin = models.CharField(max_length=16, blank=True)
    status = models.CharField(
        max_length=20,
        default="active",
        choices=[("active", "Active"), ("broken", "Broken"), ("hold", "Hold")],
    )
    notes = models.TextField(blank=True)

    def __str__(self):
        return self.name

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["name", "bank", "card_number"],
                name="unique_card_identity",
            )
        ]


class Client(models.Model):
    name = models.CharField(max_length=120, unique=True)  # client_name is the ID
    status = models.CharField(
        max_length=20,
        default="active",
        choices=[("active", "Active"), ("blocked", "Blocked"), ("hold", "Hold")],
    )
    notes = models.TextField(blank=True)

    def __str__(self):
        return self.name


class Transaction(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)  # when record added
    timestamp = models.DateTimeField()  # when payment happened
    card = models.ForeignKey(Card, on_delete=models.PROTECT, related_name="transactions")
    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name="transactions")
    amount_rub = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    amount_usd = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    rate = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    notes = models.TextField(blank=True)

    def save(self, *args, **kwargs):
        # auto-calc rate when both are present
        if self.amount_rub and self.amount_usd and self.amount_usd != 0:
            self.rate = self.amount_rub / self.amount_usd
        else:
            self.rate = None
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.timestamp.date()} | {self.card.name} | {self.client.name}"


class Withdrawal(models.Model):
    """
    One row per (date, card) for what the ATM guy did.
    """
    date = models.DateField()
    card = models.ForeignKey(Card, on_delete=models.PROTECT, related_name="withdrawals")
    fully_withdrawn = models.BooleanField(default=False)
    withdrawn_rub = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    commission_rub = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    note = models.TextField(blank=True)

    class Meta:
        unique_together = [("date", "card")]

    def __str__(self):
        return f"{self.date} | {self.card.name}"
