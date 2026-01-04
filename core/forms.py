from django import forms
from django.utils import timezone
from .models import Card, CardGroup, Client, Transaction


class CardForm(forms.ModelForm):
    group_name = forms.CharField(
        required=False,
        label="Group",
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "placeholder": "Start typing group...",
                "class": "combo-input",
                "autocorrect": "off",
                "autocapitalize": "none",
                "spellcheck": "false",
            }
        ),
    )

    class Meta:
        model = Card
        fields = ["name", "bank", "card_number", "pin", "status", "notes"]
        widgets = {
            "bank": forms.TextInput(
                attrs={
                    "placeholder": "Start typing bank...",
                    "autocomplete": "off",
                    "class": "combo-input",
                    "autocorrect": "off",
                    "autocapitalize": "none",
                    "spellcheck": "false",
                }
            ),
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.order_fields(["name", "bank", "group_name", "card_number", "pin", "status", "notes"])
        if not self.is_bound and self.instance.pk and self.instance.group:
            self.fields["group_name"].initial = self.instance.group.name

    def clean_group_name(self):
        return (self.cleaned_data.get("group_name") or "").strip()

    def save(self, commit=True):
        instance = super().save(commit=False)
        group_name = (self.cleaned_data.get("group_name") or "").strip()
        group_obj = None
        if group_name:
            group_obj = CardGroup.objects.filter(name__iexact=group_name).first()
            if not group_obj:
                group_obj = CardGroup.objects.create(name=group_name)
        instance.group = group_obj
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ["name", "status", "notes"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
        }


class CardGroupForm(forms.ModelForm):
    class Meta:
        model = CardGroup
        fields = ["name"]


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
            if self.instance.pk and self.instance.timestamp:
                display = timezone.localtime(self.instance.timestamp)
                self.initial["timestamp"] = display.strftime(TIMESTAMP_DISPLAY_FORMAT)
            else:
                current = timezone.localtime(timezone.now())
                self.initial["timestamp"] = current.strftime(TIMESTAMP_DISPLAY_FORMAT)
