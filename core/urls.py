from django.urls import path
from . import views

urlpatterns = [
    path("", views.transactions_list, name="root_transactions"),

    path("cards/", views.cards_list, name="cards_list"),
    path("cards/export/", views.cards_export, name="cards_export"),
    path("cards/search/", views.cards_search, name="cards_search"),
    path("cards/add/", views.card_add, name="card_add"),
    path("cards/<int:pk>/edit/", views.card_edit, name="card_edit"),
    path("cards/<int:pk>/delete/", views.card_delete, name="card_delete"),
    path("cards/<int:pk>/history/", views.card_history, name="card_history"),
    path("cards/<int:pk>/history/search/", views.card_history_search, name="card_history_search"),


    path("clients/", views.clients_list, name="clients_list"),
    path("clients/export/", views.clients_export, name="clients_export"),
    path("clients/search/", views.clients_search, name="clients_search"),
    path("clients/add/", views.client_add, name="client_add"),
    path("clients/<int:pk>/edit/", views.client_edit, name="client_edit"),
    path("clients/<int:pk>/delete/", views.client_delete, name="client_delete"),

    path("groups/create/", views.group_create, name="group_create"),
    path("groups/<int:pk>/rename/", views.group_rename, name="group_rename"),
    path("groups/<int:pk>/delete/", views.group_delete, name="group_delete"),

    path("transactions/", views.transactions_list, name="transactions_list"),
    path("transactions/export/", views.transactions_export, name="transactions_export"),
    path("transactions/search/", views.transactions_search, name="transactions_search"),
    path("transactions/add/", views.transaction_add, name="transaction_add"),
    path("transactions/<int:pk>/edit/", views.transaction_edit, name="transaction_edit"),
    path("transactions/<int:pk>/delete/", views.transaction_delete, name="transaction_delete"),
    path("transactions/<int:pk>/note/", views.transaction_update_note, name="transaction_update_note"),
    path("payments/", views.payments_summary, name="payments_summary"),
    path("payments/export/", views.payments_export, name="payments_export"),
    path("payments/search/", views.payments_search, name="payments_search"),


    path("withdraw/", views.withdraw_today, name="withdraw_today"),
    path("withdraw/search/", views.withdraw_search, name="withdraw_search"),
    path("withdraw/save/", views.withdraw_today_save, name="withdraw_today_save"),
    path("withdraw/<int:pk>/timestamp/", views.withdraw_update_time, name="withdraw_update_time"),
]
