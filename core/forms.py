from django import forms
from django.utils import timezone
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal, InvalidOperation
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
    tz_offset = forms.IntegerField(
        required=False,
        widget=forms.HiddenInput(attrs={"class": "js-tz-offset"}),
    )
    original_timestamp = forms.CharField(
        required=False,
        widget=forms.HiddenInput(attrs={"class": "js-original-timestamp"}),
    )
    timestamp_initial = forms.CharField(
        required=False,
        widget=forms.HiddenInput(attrs={"class": "js-timestamp-initial"}),
    )
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
            "amount_rub": forms.TextInput(
                attrs={"inputmode": "decimal", "autocomplete": "off"}
            ),
            "amount_usd": forms.TextInput(
                attrs={"inputmode": "decimal", "autocomplete": "off"}
            ),
        }

    def clean_timestamp(self):
        ts = self.cleaned_data.get("timestamp")
        if not ts:
            return ts
        initial_display = (self.cleaned_data.get("timestamp_initial") or "").strip()
        current_display = (self.data.get(self.add_prefix("timestamp")) or "").strip()
        original_raw = (self.cleaned_data.get("original_timestamp") or "").strip()
        if original_raw and initial_display and current_display == initial_display:
            try:
                original = datetime.fromisoformat(original_raw)
            except ValueError:
                original = None
            if original:
                if timezone.is_naive(original):
                    original = original.replace(tzinfo=dt_timezone.utc)
                return original
        offset = self.cleaned_data.get("tz_offset")
        if offset in (None, "") and getattr(self, "request", None):
            offset = self.request.COOKIES.get("tz_offset")
        try:
            offset = int(offset)
        except (TypeError, ValueError):
            return ts
        if timezone.is_aware(ts):
            # Treat user input as local time regardless of server timezone.
            ts = ts.replace(tzinfo=None)
        user_tz = timezone.get_fixed_timezone(-offset)
        ts = timezone.make_aware(ts, user_tz)
        return ts.astimezone(dt_timezone.utc)

    def _clean_money_value(self, value, field_name):
        if value in (None, ""):
            return value
        if isinstance(value, Decimal):
            return value
        raw = str(value).replace(" ", "").replace(",", ".")
        try:
            return Decimal(raw)
        except (InvalidOperation, ValueError):
            raise forms.ValidationError(f"Enter a valid number for {field_name}.")

    def clean_amount_rub(self):
        return self._clean_money_value(self.cleaned_data.get("amount_rub"), "RUB")

    def clean_amount_usd(self):
        return self._clean_money_value(self.cleaned_data.get("amount_usd"), "USD")

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)
        self.fields["timestamp"].widget.attrs.setdefault("autocapitalize", "none")
        self.fields["timestamp"].widget.attrs.setdefault("spellcheck", "false")
        self.order_fields(["timestamp", "client", "card", "amount_rub", "amount_usd", "notes"])
        if not self.is_bound:
            self.fields["timestamp"].widget.attrs["data-utc"] = "1"
            if not self.instance.pk:
                self.initial["amount_rub"] = ""
                self.initial["amount_usd"] = ""
            if self.instance.pk and self.instance.timestamp:
                display = self.instance.timestamp
                if timezone.is_aware(display):
                    display = display.astimezone(dt_timezone.utc)
                self.initial["timestamp"] = display.strftime(TIMESTAMP_DISPLAY_FORMAT)
                self.initial["timestamp_initial"] = self.initial["timestamp"]
                self.initial["original_timestamp"] = self.instance.timestamp.isoformat()
            else:
                current = timezone.now()
                if timezone.is_aware(current):
                    current = current.astimezone(dt_timezone.utc)
                self.initial["timestamp"] = current.strftime(TIMESTAMP_DISPLAY_FORMAT)
        else:
            self.fields["timestamp"].widget.attrs.pop("data-utc", None)
