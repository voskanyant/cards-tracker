from datetime import date, timedelta, datetime, time
from decimal import Decimal
from collections import defaultdict

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Sum
from django.db.models.deletion import ProtectedError
from django.shortcuts import render
from django.utils.dateparse import parse_date
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from .forms import CardForm, CardGroupForm, ClientForm, TransactionForm
from .models import Card, CardGroup, Client, Transaction, Withdrawal

DATE_DISPLAY_FORMAT = "%d/%m/%Y"
DATE_PARSE_FORMATS = ["%d/%m/%Y", "%Y-%m-%d"]


def _withdrawal_actual_amount(wd, cache=None):
    if cache is None:
        cache = {}
    if wd.fully_withdrawn:
        key = (wd.card_id, wd.date)
        if key not in cache:
            card = wd.card if hasattr(wd, "card") else Card.objects.get(pk=wd.card_id)
            cache[key] = _should_have(card, wd.date)
        return cache[key]
    return wd.withdrawn_rub or Decimal("0")

def _closing_before(card: Card, day: date) -> Decimal:
    """
    Remaining balance carried into 'day' = last (should_have - withdrawn - commission)
    saved in Withdrawals before 'day'. If none, 0.
    """
    last = (
        Withdrawal.objects.filter(card=card, date__lt=day)
        .order_by("-date")
        .first()
    )
    if not last:
        return Decimal("0")

    # compute last day's remaining:
    should = _should_have(card, last.date)
    if last.fully_withdrawn:
        withdrawn = should
    else:
        withdrawn = last.withdrawn_rub or Decimal("0")
    commission = last.commission_rub or Decimal("0")
    remaining = should - withdrawn - commission
    return remaining if remaining > 0 else Decimal("0")


def _received_today(card: Card, day: date) -> Decimal:
    start = timezone.make_aware(datetime.combine(day, time.min))
    end = timezone.make_aware(datetime.combine(day + timedelta(days=1), time.min))

    agg = (
        Transaction.objects.filter(
            card=card,
            timestamp__gte=start,
            timestamp__lt=end,
        )
        .aggregate(total=Sum("amount_rub"))
    )
    return agg["total"] or Decimal("0")



def _should_have(card: Card, day: date) -> Decimal:
    return _closing_before(card, day) + _received_today(card, day)


def _card_display(card: Card) -> str:
    name = card.name
    last4 = ""
    if card.card_number:
        stripped = card.card_number.replace(" ", "")
        if len(stripped) >= 4:
            last4 = stripped[-4:]
        elif len(card.card_number) >= 4:
            last4 = card.card_number[-4:]
    label = name
    if last4:
        label = f"{label} *{last4}"
    bank = (card.bank or "").strip()
    if bank:
        label = f"{bank} {label}"
    return label.strip()


def _bank_name_list():
    return list(
        Card.objects.exclude(bank__isnull=True)
        .exclude(bank="")
        .order_by("bank")
        .values_list("bank", flat=True)
        .distinct()
    )


def _parse_user_date(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in DATE_PARSE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    parsed = parse_date(raw)
    return parsed


def _format_user_date(day: date) -> str:
    return day.strftime(DATE_DISPLAY_FORMAT)


@login_required
def withdraw_today(request):
    day_raw = (request.GET.get("date") or "").strip()
    parsed_day = _parse_user_date(day_raw)
    day = parsed_day or date.today()
    bank_filter = (request.GET.get("bank") or "").strip()
    day_display = _format_user_date(parsed_day or day)

    # IMPORTANT: this view must always return render() at the end
    # We no longer use POST here (autosave uses a separate endpoint),
    # but if your browser posts to it, we still safely ignore it.

    rows = []
    totals = {
        "should": Decimal("0"),
        "withdrawn": Decimal("0"),
        "commission": Decimal("0"),
        "remaining": Decimal("0"),
    }
    banks = []
    for card in Card.objects.filter(status="active").order_by("name"):
        carry_in = _closing_before(card, day)
        received = _received_today(card, day)
        should = carry_in + received

        if should > 0:
            wd = Withdrawal.objects.filter(date=day, card=card).first()

            last4 = card.card_number[-4:] if card.card_number and len(card.card_number) >= 4 else ""
            card_label = f"{card.name} *{last4}" if last4 else card.name
            bank_label = (card.bank or "").strip()
            if bank_label:
                card_label = f"{bank_label} {card_label}".strip()
                if bank_label not in banks:
                    banks.append(bank_label)

            commission = Decimal("0")
            withdrawn_amount = Decimal("0")
            if wd:
                commission = wd.commission_rub or Decimal("0")
                if wd.fully_withdrawn:
                    withdrawn_amount = should
                elif wd.withdrawn_rub:
                    withdrawn_amount = wd.withdrawn_rub

            remaining = should - withdrawn_amount - commission
            if remaining < 0:
                remaining = Decimal("0")

            rows.append(
                {
                    "card_id": card.id,
                    "card_label": card_label,
                    "pin": card.pin,
                    "should_have": should,
                    "withdrawal": wd,
                    "remaining": remaining,
                    "bank": bank_label,
                    "withdrawn_value": withdrawn_amount,
                    "commission_value": commission,
                }
            )
            totals["should"] += should

            totals["commission"] += commission
            totals["withdrawn"] += withdrawn_amount
            totals["remaining"] += remaining

    rows.sort(key=lambda r: r["card_label"])
    banks.sort()

    if bank_filter:
        filter_lower = bank_filter.lower()
        exact_rows = [r for r in rows if (r["bank"] or "").lower() == filter_lower]
        filtered_rows = exact_rows or [
            r for r in rows if filter_lower in (r["bank"] or "").lower()
        ]
        rows = filtered_rows
        totals = {
            "should": Decimal("0"),
            "withdrawn": Decimal("0"),
            "commission": Decimal("0"),
            "remaining": Decimal("0"),
        }
        for r in rows:
            totals["should"] += r["should_have"]
            totals["withdrawn"] += r["withdrawn_value"]
            totals["commission"] += r["commission_value"]
            totals["remaining"] += r["remaining"]
        if rows:
            bank_filter = rows[0]["bank"]

    return render(
        request,
        "core/withdraw_today.html",
        {
            "day": day,
            "day_display": day_display,
            "rows": rows,
            "totals": totals,
            "banks": banks,
            "selected_bank": bank_filter,
        },
    )

@login_required
@require_POST
def withdraw_today_save(request):
    # We do NOT rely on "id" anymore because on first edit there is no row yet.
    day_str = request.POST.get("date")
    card_id = request.POST.get("card_id")

    if not day_str or not card_id:
        return JsonResponse({"ok": False, "error": "Missing date or card_id"}, status=400)

    day = _parse_user_date(day_str)
    if not day:
        return JsonResponse({"ok": False, "error": "Bad date format"}, status=400)

    wd, _ = Withdrawal.objects.get_or_create(date=day, card_id=int(card_id))

    # IMPORTANT: always set fully_withdrawn explicitly
    wd.fully_withdrawn = (request.POST.get("fully_withdrawn") == "true")

    if wd.fully_withdrawn:
        wd.withdrawn_rub = None
    else:
        val = request.POST.get("withdrawn_rub")
        wd.withdrawn_rub = val if val not in (None, "") else None

    comm = request.POST.get("commission_rub")
    wd.commission_rub = comm if comm not in (None, "") else 0

    wd.note = request.POST.get("note") or ""
    wd.save()

    return JsonResponse({"ok": True, "id": wd.id})


@login_required
def cards_list(request):
    cards = Card.objects.select_related("group").all().order_by("name")
    groups = CardGroup.objects.order_by("name")
    banks = _bank_name_list()

    start_raw = (request.GET.get("start") or "").strip()
    end_raw = (request.GET.get("end") or "").strip()
    bank_filter = (request.GET.get("bank") or "").strip()
    group_filter = (request.GET.get("group") or "").strip()
    start_date = _parse_user_date(start_raw)
    end_date = _parse_user_date(end_raw)
    if start_date:
        start_raw = _format_user_date(start_date)
    if end_date:
        end_raw = _format_user_date(end_date)

    if bank_filter:
        cards = cards.filter(bank__icontains=bank_filter)
    if group_filter:
        cards = cards.filter(group__name__icontains=group_filter)

    tx_filter = {}
    wd_filter = {}
    if start_date:
        tx_filter["timestamp__date__gte"] = start_date
        wd_filter["date__gte"] = start_date
    if end_date:
        tx_filter["timestamp__date__lte"] = end_date
        wd_filter["date__lte"] = end_date

    received_map = {
        row["card_id"]: row["total"] or Decimal("0")
        for row in Transaction.objects.filter(**tx_filter)
        .values("card_id")
        .annotate(total=Sum("amount_rub"))
    }

    withdraw_map = defaultdict(lambda: {"amount": Decimal("0"), "commission": Decimal("0")})
    cache = {}
    for wd in Withdrawal.objects.filter(**wd_filter).select_related("card"):
        actual = _withdrawal_actual_amount(wd, cache)
        withdraw_map[wd.card_id]["amount"] += actual
        withdraw_map[wd.card_id]["commission"] += wd.commission_rub or Decimal("0")

    overall = {
        "received": Decimal("0"),
        "withdrawn": Decimal("0"),
        "commission": Decimal("0"),
        "balance": Decimal("0"),
    }

    for card in cards:
        received = received_map.get(card.id, Decimal("0"))
        withdrawn = withdraw_map[card.id]["amount"]
        commission = withdraw_map[card.id]["commission"]
        card.received_total = received
        card.withdrawn_total = withdrawn
        card.commission_total = commission
        card.balance_total = received - withdrawn - commission
        overall["received"] += received
        overall["withdrawn"] += withdrawn
        overall["commission"] += commission
        overall["balance"] += card.balance_total

    form = CardForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("cards_list")
    return render(
        request,
        "core/cards_list.html",
        {
            "cards": cards,
            "form": form,
            "start": start_raw,
            "end": end_raw,
            "bank_filter": bank_filter,
            "group_filter": group_filter,
            "overall": overall,
            "groups": groups,
            "banks": banks,
        },
    )


@login_required
def clients_list(request):
    clients = Client.objects.all().order_by("name")
    query = (request.GET.get("q") or "").strip()
    if query:
        clients = clients.filter(name__icontains=query)
    form = ClientForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("clients_list")
    paginator = Paginator(clients, 50)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    return render(
        request,
        "core/clients_list.html",
        {"page_obj": page_obj, "form": form, "query": query},
    )

@login_required
def clients_search(request):
    query = (request.GET.get("q") or "").strip()
    if not query:
        return JsonResponse({"results": []})
    clients = Client.objects.filter(name__icontains=query).order_by("name")
    data = [{"id": c.id, "name": c.name, "status": c.status} for c in clients]
    return JsonResponse({"results": data})


@login_required
def transactions_list(request):
    txs = Transaction.objects.select_related("card", "client").order_by("-timestamp")

    cards = Card.objects.all().order_by("name")
    clients = Client.objects.all().order_by("name")
    for card in cards:
        card.display_label = _card_display(card)

    clear_filters = "clear" in request.GET
    if clear_filters:
        request.session.pop("tx_start", None)
        request.session.pop("tx_end", None)

    start_raw = (request.GET.get("start") or "").strip()
    end_raw = (request.GET.get("end") or "").strip()
    start_date = _parse_user_date(start_raw)
    end_date = _parse_user_date(end_raw)
    if start_raw:
        if start_date:
            start_raw = _format_user_date(start_date)
            request.session["tx_start"] = start_raw
        else:
            request.session.pop("tx_start", None)
    elif not clear_filters:
        stored = request.session.get("tx_start")
        if stored:
            start_raw = stored
            start_date = _parse_user_date(stored)

    if end_raw:
        if end_date:
            end_raw = _format_user_date(end_date)
            request.session["tx_end"] = end_raw
        else:
            request.session.pop("tx_end", None)
    elif not clear_filters:
        stored_end = request.session.get("tx_end")
        if stored_end:
            end_raw = stored_end
            end_date = _parse_user_date(stored_end)

    if start_date:
        txs = txs.filter(timestamp__date__gte=start_date)
    if end_date:
        txs = txs.filter(timestamp__date__lte=end_date)

    if request.method == "POST":
        form = TransactionForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect(request.get_full_path())
    else:
        form = TransactionForm()

    paginator = Paginator(txs, 50)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "page_obj": page_obj,
        "start": start_raw,
        "end": end_raw,
        "form": form,
        "cards": cards,
        "clients": clients,
        "card_lookup": {str(c.id): c.display_label for c in cards},
        "client_lookup": {str(c.id): c.name for c in clients},
    }
    return render(request, "core/transactions_list.html", context)

@login_required
def card_add(request):
    form = CardForm(request.POST or None)
    groups = CardGroup.objects.order_by("name")
    banks = _bank_name_list()
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("cards_list")
    return render(
        request,
        "core/card_form.html",
        {"form": form, "title": "Add Card", "groups": groups, "banks": banks},
    )


@login_required
def card_edit(request, pk: int):
    card = get_object_or_404(Card, pk=pk)
    form = CardForm(request.POST or None, instance=card)
    groups = CardGroup.objects.order_by("name")
    banks = _bank_name_list()
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("cards_list")
    return render(
        request,
        "core/card_form.html",
        {"form": form, "title": "Edit Card", "groups": groups, "banks": banks},
    )


@login_required
@require_POST
def card_delete(request, pk: int):
    card = get_object_or_404(Card, pk=pk)
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse("cards_list")
    if not next_url or not next_url.startswith("/"):
        next_url = reverse("cards_list")
    if card.transactions.exists():
        messages.error(request, "Cannot delete card with existing transactions. Delete them first.")
        return redirect(next_url)
    # clean up withdrawals (user can't remove them elsewhere)
    card.withdrawals.all().delete()
    try:
        card.delete()
        messages.success(request, f"Card '{card.name}' deleted.")
    except ProtectedError:
        messages.error(request, "Cannot delete card due to linked records.")
    return redirect(next_url)


@login_required
def client_add(request):
    form = ClientForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("clients_list")
    return render(request, "core/client_form.html", {"form": form, "title": "Add Client"})


@login_required
def client_edit(request, pk: int):
    client = get_object_or_404(Client, pk=pk)
    form = ClientForm(request.POST or None, instance=client)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("clients_list")
    return render(request, "core/client_form.html", {"form": form, "title": "Edit Client"})


@login_required
@require_POST
def client_delete(request, pk: int):
    client = get_object_or_404(Client, pk=pk)
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse("clients_list")
    if not next_url or not next_url.startswith("/"):
        next_url = reverse("clients_list")
    try:
        client.delete()
        messages.success(request, f"Client '{client.name}' deleted.")
    except ProtectedError:
        messages.error(request, "Cannot delete client with existing transactions.")
    return redirect(next_url)


@login_required
@require_POST
def group_create(request):
    name = (request.POST.get("name") or "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Name is required"}, status=400)
    group, created = CardGroup.objects.get_or_create(name=name)
    return JsonResponse({"ok": True, "id": group.id, "name": group.name, "created": created})


@login_required
@require_POST
def group_rename(request, pk: int):
    group = get_object_or_404(CardGroup, pk=pk)
    name = (request.POST.get("name") or "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Name is required"}, status=400)
    if CardGroup.objects.exclude(pk=group.pk).filter(name=name).exists():
        return JsonResponse({"ok": False, "error": "Group with this name already exists"}, status=400)
    group.name = name
    group.save()
    return JsonResponse({"ok": True, "id": group.id, "name": group.name})


@login_required
@require_POST
def group_delete(request, pk: int):
    group = get_object_or_404(CardGroup, pk=pk)
    group.cards.update(group=None)
    group.delete()
    return JsonResponse({"ok": True})


@login_required
def transaction_add(request):
    form = TransactionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("transactions_list")
    return render(request, "core/transaction_form.html", {"form": form, "title": "Add Transaction"})


@login_required
def transaction_edit(request, pk: int):
    tx = get_object_or_404(Transaction, pk=pk)
    form = TransactionForm(request.POST or None, instance=tx)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("transactions_list")
    return render(request, "core/transaction_form.html", {"form": form, "title": "Edit Transaction"})


@login_required
@require_POST
def transaction_delete(request, pk: int):
    tx = get_object_or_404(Transaction, pk=pk)
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse("transactions_list")
    if not next_url or not next_url.startswith("/"):
        next_url = reverse("transactions_list")
    tx.delete()
    messages.success(request, "Transaction deleted.")
    return redirect(next_url)


@login_required
def card_history(request, pk: int):
    card = get_object_or_404(Card, pk=pk)

    start_raw = (request.GET.get("start") or "").strip()
    end_raw = (request.GET.get("end") or "").strip()
    start_date = _parse_user_date(start_raw)
    end_date = _parse_user_date(end_raw)
    if start_date:
        start_raw = _format_user_date(start_date)
    if end_date:
        end_raw = _format_user_date(end_date)

    tx_filter = {"card": card}
    wd_filter = {"card": card}
    if start_date:
        tx_filter["timestamp__date__gte"] = start_date
        wd_filter["date__gte"] = start_date
    if end_date:
        tx_filter["timestamp__date__lte"] = end_date
        wd_filter["date__lte"] = end_date

    received = (
        Transaction.objects.filter(**tx_filter).aggregate(total=Sum("amount_rub"))["total"]
        or Decimal("0")
    )

    cache = {}
    withdrawals = []
    total_withdrawn = Decimal("0")
    total_commission = Decimal("0")
    for wd in (
        Withdrawal.objects.filter(**wd_filter)
        .select_related("card")
        .order_by("-date")
    ):
        actual = _withdrawal_actual_amount(wd, cache)
        if actual <= 0:
            continue
        commission = wd.commission_rub or Decimal("0")
        withdrawals.append(
            {
                "date": wd.date,
                "fully": wd.fully_withdrawn,
                "amount": actual,
                "commission": commission,
                "note": wd.note,
            }
        )
        total_withdrawn += actual
        total_commission += commission

    txs = Transaction.objects.filter(**tx_filter).select_related("client").order_by("-timestamp")[:100]

    context = {
        "card": card,
        "transactions": txs,
        "withdrawals": withdrawals,
        "received_total": received,
        "withdrawn_total": total_withdrawn,
        "commission_total": total_commission,
        "balance_total": received - total_withdrawn - total_commission,
        "start": start_raw,
        "end": end_raw,
    }
    return render(request, "core/card_history.html", context)
@login_required
def payments_summary(request):
    clear_filters = "clear" in request.GET
    if clear_filters:
        request.session.pop("pay_start", None)
        request.session.pop("pay_end", None)

    start_raw = (request.GET.get("start") or "").strip()
    end_raw = (request.GET.get("end") or "").strip()
    start_date = _parse_user_date(start_raw)
    end_date = _parse_user_date(end_raw)
    if start_raw:
        if start_date:
            start_raw = _format_user_date(start_date)
            request.session["pay_start"] = start_raw
        else:
            request.session.pop("pay_start", None)
    elif not clear_filters:
        stored_start = request.session.get("pay_start")
        if stored_start:
            start_raw = stored_start
            start_date = _parse_user_date(stored_start)

    if end_raw:
        if end_date:
            end_raw = _format_user_date(end_date)
            request.session["pay_end"] = end_raw
        else:
            request.session.pop("pay_end", None)
    elif not clear_filters:
        stored_end = request.session.get("pay_end")
        if stored_end:
            end_raw = stored_end
            end_date = _parse_user_date(stored_end)

    txs = Transaction.objects.select_related("client").order_by("-timestamp")
    if start_date:
        txs = txs.filter(timestamp__date__gte=start_date)
    if end_date:
        txs = txs.filter(timestamp__date__lte=end_date)

    summary = defaultdict(lambda: {"rub": Decimal("0"), "usd": Decimal("0")})
    for tx in txs:
        key = (tx.timestamp.date(), tx.client_id)
        summary[key]["rub"] += tx.amount_rub or Decimal("0")
        summary[key]["usd"] += tx.amount_usd or Decimal("0")

    client_cache = {}
    rows = []
    for (day, client_id), totals in summary.items():
        client = client_cache.setdefault(client_id, Client.objects.get(pk=client_id))
        rows.append({
            "date": day,
            "client": client,
            "rub": totals["rub"],
            "usd": totals["usd"],
        })
    rows.sort(key=lambda r: (r["date"], r["client"].name), reverse=True)

    return render(
        request,
        "core/payments_summary.html",
        {"rows": rows, "start": start_raw, "end": end_raw},
    )
@login_required
def groups_list(request):
    groups = CardGroup.objects.order_by("name")
    form = CardGroupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("groups_list")
    return render(request, "core/groups_list.html", {"groups": groups, "form": form})


@login_required
def group_edit(request, pk: int):
    group = get_object_or_404(CardGroup, pk=pk)
    form = CardGroupForm(request.POST or None, instance=group)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("groups_list")
    return render(request, "core/group_form.html", {"form": form, "title": "Edit Group"})


@login_required
@require_POST
def group_delete(request, pk: int):
    group = get_object_or_404(CardGroup, pk=pk)
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse("groups_list")
    if not next_url or not next_url.startswith("/"):
        next_url = reverse("groups_list")
    group.delete()
    return redirect(next_url)
