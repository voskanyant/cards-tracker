from django import forms
from django.utils import timezone
from .models import Card, Client, Transaction


class CardForm(forms.ModelForm):
    class Meta:
        model = Card
        fields = ["name", "bank", "card_number", "pin", "status", "notes"]


class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ["name", "status", "notes"]


class TransactionForm(forms.ModelForm):
    class Meta:
        model = Transaction
        fields = ["timestamp", "card", "client", "amount_rub", "amount_usd", "notes"]
        widgets = {
            "timestamp": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            current = timezone.localtime(timezone.now())
            self.initial.setdefault("timestamp", current.strftime("%Y-%m-%dT%H:%M"))
