from django.contrib import admin
from .models import Card, Client, Transaction, Withdrawal


@admin.register(Card)
class CardAdmin(admin.ModelAdmin):
    list_display = ("name", "bank", "card_number", "pin", "status")
    search_fields = ("name", "bank", "card_number")
    list_filter = ("status", "bank")


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("name", "status")
    search_fields = ("name",)
    list_filter = ("status",)


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "card", "client", "amount_rub", "amount_usd", "rate")
    search_fields = ("card__name", "client__name", "notes")
    list_filter = ("card", "client",)
    date_hierarchy = "timestamp"
    ordering = ("-timestamp",)


@admin.register(Withdrawal)
class WithdrawalAdmin(admin.ModelAdmin):
    list_display = ("date", "card", "withdrawn_rub", "commission_rub", "note")
    search_fields = ("card__name", "note")
    list_filter = ("card",)
    date_hierarchy = "date"
    ordering = ("-date",)
