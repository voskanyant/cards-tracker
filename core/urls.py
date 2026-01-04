from django.urls import path
from . import views

urlpatterns = [
    path("", views.payments_summary, name="payments_summary_root"),

    path("cards/", views.cards_list, name="cards_list"),
    path("cards/add/", views.card_add, name="card_add"),
    path("cards/<int:pk>/edit/", views.card_edit, name="card_edit"),
    path("cards/<int:pk>/history/", views.card_history, name="card_history"),


    path("clients/", views.clients_list, name="clients_list"),
    path("clients/add/", views.client_add, name="client_add"),
    path("clients/<int:pk>/edit/", views.client_edit, name="client_edit"),

    path("transactions/", views.transactions_list, name="transactions_list"),
    path("transactions/add/", views.transaction_add, name="transaction_add"),
    path("transactions/<int:pk>/edit/", views.transaction_edit, name="transaction_edit"),
    path("payments/", views.payments_summary, name="payments_summary"),


    path("withdraw/", views.withdraw_today, name="withdraw_today"),
    path("withdraw/save/", views.withdraw_today_save, name="withdraw_today_save"),
]
