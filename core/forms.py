from django import forms
from django.utils import timezone
from .models import Card, Client, Transaction


class CardForm(forms.ModelForm):
    class Meta:
        model = Card
        fields = ["name", "bank", "card_number", "pin", "status", "notes"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def clean(self):
        cleaned = super().clean()
        name = cleaned.get("name")
        bank = (cleaned.get("bank") or "").strip()
        card_number = (cleaned.get("card_number") or "").strip()
        if name:
            qs = Card.objects.filter(name=name, bank=bank, card_number=card_number)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError(
                    "A card with the same name, bank, and card number already exists."
                )
        return cleaned


class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ["name", "status", "notes"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
        }


TIMESTAMP_DISPLAY_FORMAT = "%d/%m/%Y %H:%M"
TIMESTAMP_INPUT_FORMATS = [
    "%d/%m/%Y %H:%M",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M",
]


class TransactionForm(forms.ModelForm):
    timestamp = forms.DateTimeField(
        input_formats=TIMESTAMP_INPUT_FORMATS,
        widget=forms.TextInput(
            attrs={
                "placeholder": "dd/mm/yyyy HH:MM",
                "autocomplete": "off",
                "inputmode": "numeric",
                "class": "date-input js-datetime-input",
            }
        ),
    )

    class Meta:
        model = Transaction
        fields = ["timestamp", "card", "client", "amount_rub", "amount_usd", "notes"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["timestamp"].widget.attrs.setdefault("autocapitalize", "none")
        self.fields["timestamp"].widget.attrs.setdefault("spellcheck", "false")
        if not self.is_bound:
            current = timezone.localtime(timezone.now())
            self.initial.setdefault("timestamp", current.strftime(TIMESTAMP_DISPLAY_FORMAT))
