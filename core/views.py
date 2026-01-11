from datetime import date, timedelta, datetime, time, timezone as dt_timezone
from decimal import Decimal, ROUND_DOWN, InvalidOperation
from collections import defaultdict
import csv
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Sum, Q
from django.db.models.functions import Coalesce
from django.db.models.deletion import ProtectedError
from django.shortcuts import render
from django.utils.dateparse import parse_date
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from .forms import CardForm, CardGroupForm, ClientForm, TransactionForm
from .models import BankColor, Card, CardGroup, Client, Transaction, Withdrawal

DATE_DISPLAY_FORMAT = "%d/%m/%Y"
DATE_PARSE_FORMATS = ["%d/%m/%Y", "%Y-%m-%d"]
DATETIME_PARSE_FORMATS = ["%d/%m/%Y %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"]
PER_PAGE_CHOICES = [10, 25, 50, 100]


def _parse_per_page(raw, default=50):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value in PER_PAGE_CHOICES else default


def _pagination_items(page_obj, window=2):
    total = page_obj.paginator.num_pages
    current = page_obj.number
    if total <= 1:
        return []
    start = max(2, current - window)
    end = min(total - 1, current + window)
    items = [1]
    if start > 2:
        items.append(None)
    items.extend(range(start, end + 1))
    if end < total - 1:
        items.append(None)
    if total > 1:
        items.append(total)
    return items


def _pagination_meta(paginator, page_obj):
    return {
        "total": paginator.count,
        "pages": paginator.num_pages,
        "page": page_obj.number,
        "per_page": paginator.per_page,
    }


def _format_spaced_number(value):
    if value in (None, ""):
        return ""
    try:
        dec = Decimal(value)
    except Exception:
        try:
            dec = Decimal(str(value))
        except Exception:
            return str(value)
    quantized = dec.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    raw = format(quantized, "f").rstrip("0").rstrip(".")
    if raw == "":
        raw = "0"
    sign = "-" if raw.startswith("-") else ""
    digits = raw[1:] if sign else raw
    int_part, dot, frac_part = digits.partition(".")
    groups = []
    while int_part:
        groups.append(int_part[-3:])
        int_part = int_part[:-3]
    spaced = " ".join(reversed(groups)) if groups else "0"
    frac_part = frac_part.rstrip("0")
    if frac_part:
        return f"{sign}{spaced}.{frac_part}"
    return f"{sign}{spaced}"


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
    Remaining balance carried into 'day' = total received since the last
    fully-withdrawn day minus withdrawn and commission in that period.
    """
    last_full = (
        Withdrawal.objects.filter(card=card, date__lt=day, fully_withdrawn=True)
        .order_by("-date")
        .first()
    )
    start_date = last_full.date + timedelta(days=1) if last_full else None

    txs = Transaction.objects.filter(card=card, timestamp__date__lt=day)
    wds = Withdrawal.objects.filter(card=card, date__lt=day, fully_withdrawn=False)
    if start_date:
        txs = txs.filter(timestamp__date__gte=start_date)
        wds = wds.filter(date__gte=start_date)

    received = txs.aggregate(total=Sum("amount_rub"))["total"] or Decimal("0")
    withdrawn = wds.aggregate(total=Sum("withdrawn_rub"))["total"] or Decimal("0")
    commission = wds.aggregate(total=Sum("commission_rub"))["total"] or Decimal("0")

    remaining = received - withdrawn - commission
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


def _bank_color_map():
    return {item.bank: item.color for item in BankColor.objects.all()}


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


def _parse_user_datetime(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in DATETIME_PARSE_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _apply_tz_offset(dt_value, offset):
    if not dt_value:
        return None
    try:
        offset = int(offset)
    except (TypeError, ValueError):
        return None
    if timezone.is_aware(dt_value):
        dt_value = timezone.make_naive(dt_value, timezone.get_current_timezone())
    user_tz = timezone.get_fixed_timezone(-offset)
    aware = timezone.make_aware(dt_value, user_tz)
    return aware.astimezone(dt_timezone.utc)

def _withdraw_totals(rows):
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
    return totals


def _cards_with_totals(cards, start_date=None, end_date=None):
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
    withdrawals = _dedupe_withdrawals_by_date(
        Withdrawal.objects.filter(**wd_filter).select_related("card")
    )
    for wd in withdrawals:
        actual = _withdrawal_actual_amount(wd, cache)
        withdraw_map[wd.card_id]["amount"] += actual
        withdraw_map[wd.card_id]["commission"] += wd.commission_rub or Decimal("0")

    overall = {
        "received": Decimal("0"),
        "withdrawn": Decimal("0"),
        "commission": Decimal("0"),
        "balance": Decimal("0"),
    }

    cards_list = list(cards)
    for card in cards_list:
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

    return cards_list, overall


def _withdraw_rows_for_day(day):
    rows = []
    banks = []
    bank_colors = _bank_color_map()
    for card in Card.objects.filter(status="active").order_by("name"):
        carry_in = _closing_before(card, day)
        received = _received_today(card, day)
        should = carry_in + received

        if should > 0:
            wd = (
                Withdrawal.objects.filter(date=day, card=card)
                .order_by("-timestamp", "-id")
                .first()
            )

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
                    "bank_color": bank_colors.get(bank_label, ""),
                    "withdrawn_value": withdrawn_amount,
                    "commission_value": commission,
                }
            )

    rows.sort(key=lambda r: r["card_label"])
    banks.sort()
    return rows, banks


def _payments_rows(start_date=None, end_date=None, query=None):
    txs = Transaction.objects.select_related("client").order_by("-timestamp")
    if start_date:
        txs = txs.filter(timestamp__date__gte=start_date)
    if end_date:
        txs = txs.filter(timestamp__date__lte=end_date)
    if query:
        txs = txs.filter(client__name__icontains=query)

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
    return rows


def _dedupe_withdrawals_by_date(withdrawals):
    seen = set()
    kept = []
    for wd in withdrawals.order_by("-date", "-timestamp", "-id"):
        key = (wd.card_id, wd.date)
        if key in seen:
            continue
        seen.add(key)
        kept.append(wd)
    kept.reverse()
    return kept


def _card_balance_before(card: Card, start_date: date | None) -> Decimal:
    if not start_date:
        return Decimal("0")
    received = (
        Transaction.objects.filter(card=card, timestamp__date__lt=start_date)
        .aggregate(total=Sum("amount_rub"))["total"]
        or Decimal("0")
    )
    withdrawn = Decimal("0")
    commission = Decimal("0")
    cache = {}
    withdrawals = _dedupe_withdrawals_by_date(
        Withdrawal.objects.filter(card=card, date__lt=start_date).select_related("card")
    )
    for wd in withdrawals:
        withdrawn += _withdrawal_actual_amount(wd, cache)
        commission += wd.commission_rub or Decimal("0")
    return received - withdrawn - commission


def _card_events(card: Card, start_date: date | None, end_date: date | None, kind_filter: str | None = None, query: str | None = None):
    tx_filter = {"card": card}
    wd_filter = {"card": card}
    if start_date:
        tx_filter["timestamp__date__gte"] = start_date
        wd_filter["date__gte"] = start_date
    if end_date:
        tx_filter["timestamp__date__lte"] = end_date
        wd_filter["date__lte"] = end_date

    events = []
    for tx in Transaction.objects.filter(**tx_filter).select_related("client").order_by("timestamp"):
        events.append(
            {
                "kind": "transaction",
                "time": tx.timestamp,
                "time_iso": tx.timestamp.isoformat(),
                "time_display": tx.timestamp.strftime("%d/%m/%Y %H:%M"),
                "client": tx.client.name,
                "rub": tx.amount_rub or Decimal("0"),
                "usd": tx.amount_usd or Decimal("0"),
                "withdrawn": None,
                "commission": None,
                "note": tx.notes or "",
            }
        )

    cache = {}
    withdrawals = _dedupe_withdrawals_by_date(
        Withdrawal.objects.filter(**wd_filter).select_related("card")
    )
    for wd in withdrawals:
        actual = _withdrawal_actual_amount(wd, cache)
        commission = wd.commission_rub or Decimal("0")
        if actual <= 0 and commission <= 0:
            continue
        event_time = wd.timestamp
        if not event_time:
            event_time = timezone.make_aware(datetime.combine(wd.date, time(23, 59, 59)))
        events.append(
            {
                "kind": "withdrawal",
                "time": event_time,
                "time_iso": event_time.isoformat() if event_time else "",
                "time_display": event_time.strftime("%d/%m/%Y %H:%M") if event_time else wd.date.strftime("%d/%m/%Y"),
                "client": "",
                "rub": None,
                "usd": None,
                "withdrawn": actual,
                "commission": commission,
                "note": wd.note or "",
                "withdrawal_id": wd.id,
            }
        )

    if kind_filter in ("transaction", "withdrawal"):
        events = [event for event in events if event["kind"] == kind_filter]

    if query:
        q = query.lower()
        filtered = []
        for event in events:
            haystack = []
            if event["kind"] == "transaction":
                haystack.append(event.get("client", ""))
                haystack.append(event.get("note", ""))
            else:
                haystack.append(event.get("note", ""))
            if any(q in (value or "").lower() for value in haystack):
                filtered.append(event)
        events = filtered

    events.sort(key=lambda e: e["time"])
    running = _card_balance_before(card, start_date)
    for event in events:
        if event["kind"] == "transaction":
            running += event["rub"] or Decimal("0")
        else:
            running -= (event["withdrawn"] or Decimal("0")) + (event["commission"] or Decimal("0"))
        event["balance"] = running
    events.reverse()
    return events


def _card_bucket(card: Card) -> str:
    group_name = ""
    if card.group and card.group.name:
        group_name = card.group.name.strip().lower()
    if "our" in group_name:
        return "our"
    return "clients"


def _dashboard_payload(request):
    today = timezone.localdate()
    month_start = today.replace(day=1)
    if month_start.month == 12:
        month_next = date(month_start.year + 1, 1, 1)
    else:
        month_next = date(month_start.year, month_start.month + 1, 1)

    start_raw = (request.GET.get("start") or "").strip()
    end_raw = (request.GET.get("end") or "").strip()
    start_date = _parse_user_date(start_raw)
    end_date = _parse_user_date(end_raw)

    if not start_date and not end_date:
        period_start = month_start
        period_end = month_next - timedelta(days=1)
        period_label = month_start.strftime("%B %Y")
    else:
        if not start_date and end_date:
            start_date = end_date.replace(day=1)
        if start_date and not end_date:
            end_date = today
        period_start = start_date or month_start
        period_end = end_date or today
        period_label = f"{_format_user_date(period_start)} - {_format_user_date(period_end)}"

    if start_date:
        start_raw = _format_user_date(start_date)
    if end_date:
        end_raw = _format_user_date(end_date)

    end_exclusive = (period_end + timedelta(days=1)) if period_end else month_next

    group_filter = (request.GET.get("group") or "our").strip().lower()
    if group_filter not in ("all", "our", "clients"):
        group_filter = "our"
    bank_filter = (request.GET.get("bank") or "").strip()
    sort_filter = (request.GET.get("sort") or "received_desc").strip().lower()
    if sort_filter not in ("received_desc", "received_asc", "name_asc"):
        sort_filter = "received_desc"

    cards = Card.objects.select_related("group").order_by("name")
    if bank_filter:
        cards = cards.filter(bank__iexact=bank_filter)
    cards_list, overall = _cards_with_totals(cards)

    balances = {"our": Decimal("0"), "clients": Decimal("0")}
    for card in cards_list:
        balances[_card_bucket(card)] += card.balance_total

    monthly_received = (
        Transaction.objects.filter(
            timestamp__date__gte=period_start,
            timestamp__date__lt=end_exclusive,
            card__in=cards_list,
        )
        .values("card_id")
        .annotate(total=Coalesce(Sum("amount_rub"), Decimal("0")))
    )
    card_map = {card.id: card for card in cards_list}
    received_by_card = {row["card_id"]: row["total"] or Decimal("0") for row in monthly_received}
    monthly_cards = {"our": [], "clients": []}
    monthly_totals = {
        "our": {"received": Decimal("0"), "withdrawn": Decimal("0"), "commission": Decimal("0")},
        "clients": {"received": Decimal("0"), "withdrawn": Decimal("0"), "commission": Decimal("0")},
    }
    bank_colors = _bank_color_map()

    for card in cards_list:
        bucket = _card_bucket(card)
        value = received_by_card.get(card.id, Decimal("0"))
        monthly_totals[bucket]["received"] += value
        monthly_cards[bucket].append(
            {
                "id": card.id,
                "label": _card_display(card),
                "value": value,
                "balance": card.balance_total,
                "bank": (card.bank or "").strip(),
                "status": card.status,
            }
        )

    cache = {}
    month_withdrawals = _dedupe_withdrawals_by_date(
        Withdrawal.objects.filter(date__gte=period_start, date__lt=end_exclusive, card__in=cards_list).select_related("card")
    )
    withdraw_by_card = defaultdict(lambda: {"withdrawn": Decimal("0"), "commission": Decimal("0")})
    for wd in month_withdrawals:
        bucket = _card_bucket(wd.card)
        actual = _withdrawal_actual_amount(wd, cache)
        commission = wd.commission_rub or Decimal("0")
        monthly_totals[bucket]["withdrawn"] += actual
        monthly_totals[bucket]["commission"] += commission
        withdraw_by_card[wd.card_id]["withdrawn"] += actual
        withdraw_by_card[wd.card_id]["commission"] += commission

    for bucket in ("our", "clients"):
        monthly_cards[bucket].sort(key=lambda row: row["value"], reverse=True)

    def pct(value, max_value):
        if not max_value:
            return 0
        try:
            value = Decimal(value)
        except Exception:
            return 0
        if max_value <= 0:
            return 0
        return int((value / max_value) * 100)

    if group_filter == "our":
        selected_totals = monthly_totals["our"]
        selected_cards = monthly_cards["our"]
    elif group_filter == "clients":
        selected_totals = monthly_totals["clients"]
        selected_cards = monthly_cards["clients"]
    else:
        selected_totals = {
            "received": monthly_totals["our"]["received"] + monthly_totals["clients"]["received"],
            "withdrawn": monthly_totals["our"]["withdrawn"] + monthly_totals["clients"]["withdrawn"],
            "commission": monthly_totals["our"]["commission"] + monthly_totals["clients"]["commission"],
        }
        selected_cards = monthly_cards["our"] + monthly_cards["clients"]
        selected_cards.sort(key=lambda row: row["value"], reverse=True)

    if sort_filter == "received_asc":
        selected_cards.sort(key=lambda row: row["value"])
    elif sort_filter == "name_asc":
        selected_cards.sort(key=lambda row: row["label"].lower())
    else:
        selected_cards.sort(key=lambda row: row["value"], reverse=True)

    summary_max = max(
        selected_totals["received"],
        selected_totals["withdrawn"],
        selected_totals["commission"],
        Decimal("0"),
    )
    summary_pct = {
        "received": pct(selected_totals["received"], summary_max),
        "withdrawn": pct(selected_totals["withdrawn"], summary_max),
        "commission": pct(selected_totals["commission"], summary_max),
    }

    active_cards = [row for row in selected_cards if row.get("status") == "active"]
    max_card_value = max([row["value"] for row in active_cards] or [Decimal("0")])
    card_list = []
    for row in active_cards:
        wd = withdraw_by_card.get(row.get("id"), {"withdrawn": Decimal("0"), "commission": Decimal("0")})
        card_list.append(
            {
                "label": row["label"],
                "value": row["value"],
                "pct": pct(row["value"], max_card_value),
                "balance": row.get("balance", Decimal("0")),
                "withdrawn": wd["withdrawn"],
                "commission": wd["commission"],
                "bank_color": bank_colors.get(row.get("bank") or "", ""),
            }
        )

    context = {
        "month_label": period_label,
        "start": start_raw,
        "end": end_raw,
        "group_filter": group_filter,
        "bank_filter": bank_filter,
        "sort_filter": sort_filter,
        "total_balance": overall["balance"],
        "balances": balances,
        "summary_totals": selected_totals,
        "summary_pct": summary_pct,
        "card_list": card_list,
        "bank_colors": bank_colors,
    }

    payload = {
        "month_label": period_label,
        "total_balance": _format_spaced_number(overall["balance"]),
        "balances": {
            "our": _format_spaced_number(balances["our"]),
            "clients": _format_spaced_number(balances["clients"]),
        },
        "summary_totals": {
            "received": _format_spaced_number(selected_totals["received"]),
            "withdrawn": _format_spaced_number(selected_totals["withdrawn"]),
            "commission": _format_spaced_number(selected_totals["commission"]),
        },
        "summary_pct": summary_pct,
        "card_list": [
            {
                "label": row["label"],
                "value": _format_spaced_number(row["value"]),
                "pct": row["pct"],
                "balance": _format_spaced_number(row["balance"]),
                "withdrawn": _format_spaced_number(row["withdrawn"]),
                "commission": _format_spaced_number(row["commission"]),
                "bank_color": row.get("bank_color") or "",
            }
            for row in card_list
        ],
    }

    return context, payload


@login_required
def dashboard(request):
    context, _ = _dashboard_payload(request)
    context["banks"] = _bank_name_list()
    return render(request, "core/dashboard.html", context)


@login_required
def dashboard_data(request):
    _, payload = _dashboard_payload(request)
    return JsonResponse(payload)


@login_required
def withdraw_today(request):
    day_raw = (request.GET.get("date") or "").strip()
    parsed_day = _parse_user_date(day_raw)
    day = parsed_day or date.today()
    bank_filter = (request.GET.get("bank") or "").strip()
    query = (request.GET.get("q") or "").strip()
    per_page = _parse_per_page(request.GET.get("per_page"), default=50)
    day_display = _format_user_date(parsed_day or day)

    # IMPORTANT: this view must always return render() at the end
    # We no longer use POST here (autosave uses a separate endpoint),
    # but if your browser posts to it, we still safely ignore it.

    rows, banks = _withdraw_rows_for_day(day)

    if bank_filter:
        filter_lower = bank_filter.lower()
        exact_rows = [r for r in rows if (r["bank"] or "").lower() == filter_lower]
        filtered_rows = exact_rows or [
            r for r in rows if filter_lower in (r["bank"] or "").lower()
        ]
        rows = filtered_rows
        totals = _withdraw_totals(rows)
        if rows:
            bank_filter = rows[0]["bank"]

    if query:
        q = query.lower()
        rows = [
            r
            for r in rows
            if q in r["card_label"].lower()
            or q in (r["bank"] or "").lower()
            or q in (r["pin"] or "").lower()
        ]
        totals = _withdraw_totals(rows)

    paginator = Paginator(rows, per_page)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    totals = _withdraw_totals(page_obj)

    return render(
        request,
        "core/withdraw_today.html",
        {
            "day": day,
            "day_display": day_display,
            "page_obj": page_obj,
            "page_items": _pagination_items(page_obj),
            "totals": totals,
            "banks": banks,
            "bank_colors": _bank_color_map(),
            "selected_bank": bank_filter,
            "query": query,
            "per_page": per_page,
            "per_page_choices": PER_PAGE_CHOICES,
        },
    )

@login_required
def withdraw_search(request):
    day_raw = (request.GET.get("date") or "").strip()
    parsed_day = _parse_user_date(day_raw)
    day = parsed_day or date.today()
    bank_filter = (request.GET.get("bank") or "").strip()
    query = (request.GET.get("q") or "").strip()

    rows, banks = _withdraw_rows_for_day(day)
    if bank_filter:
        filter_lower = bank_filter.lower()
        exact_rows = [r for r in rows if (r["bank"] or "").lower() == filter_lower]
        rows = exact_rows or [r for r in rows if filter_lower in (r["bank"] or "").lower()]
        if rows:
            bank_filter = rows[0]["bank"]

    if query:
        q = query.lower()
        rows = [
            r
            for r in rows
            if q in r["card_label"].lower()
            or q in (r["bank"] or "").lower()
            or q in (r["pin"] or "").lower()
        ]
    data = []
    for r in rows:
        wd = r["withdrawal"]
        data.append(
            {
                "card_id": r["card_id"],
                "card_label": r["card_label"],
                "bank_color": r.get("bank_color") or "",
                "pin": r["pin"] or "",
                "should_have": str(r["should_have"]),
                "remaining": str(r["remaining"]),
                "fully_withdrawn": bool(wd.fully_withdrawn) if wd else False,
                "withdrawn_rub": "" if not wd or wd.withdrawn_rub is None else str(wd.withdrawn_rub),
                "commission_rub": "" if not wd or wd.commission_rub is None else str(wd.commission_rub),
                "note": "" if not wd or not wd.note else wd.note,
            }
        )

    return JsonResponse({"results": data})

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

    existing = Withdrawal.objects.filter(date=day, card_id=int(card_id)).order_by("-timestamp", "-id")
    wd = existing.first()
    if not wd:
        wd = Withdrawal(date=day, card_id=int(card_id))

    raw_ts = request.POST.get("timestamp")
    offset = request.POST.get("tz_offset") or request.COOKIES.get("tz_offset")
    parsed_ts = _parse_user_datetime(raw_ts)
    applied_ts = _apply_tz_offset(parsed_ts, offset) if parsed_ts else None
    if applied_ts:
        wd.timestamp = applied_ts
        wd.date = parsed_ts.date()

    def parse_decimal(value, field):
        if value in (None, ""):
            return None
        raw = str(value).replace(" ", "").replace(",", ".")
        try:
            return Decimal(raw)
        except (InvalidOperation, ValueError):
            raise ValueError(f"Invalid {field}")

    # IMPORTANT: always set fully_withdrawn explicitly
    wd.fully_withdrawn = (request.POST.get("fully_withdrawn") == "true")

    if wd.fully_withdrawn:
        wd.withdrawn_rub = None
    else:
        val = request.POST.get("withdrawn_rub")
        try:
            wd.withdrawn_rub = parse_decimal(val, "withdrawn") if val not in (None, "") else None
        except ValueError as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    comm = request.POST.get("commission_rub")
    try:
        commission = parse_decimal(comm, "commission")
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    wd.commission_rub = commission if commission is not None else 0

    wd.note = request.POST.get("note") or ""
    wd.save()

    return JsonResponse({"ok": True, "id": wd.id})


@login_required
@require_POST
def withdraw_update_time(request, pk: int):
    wd = get_object_or_404(Withdrawal, pk=pk)
    raw_ts = request.POST.get("timestamp")
    offset = request.POST.get("tz_offset") or request.COOKIES.get("tz_offset")
    parsed_ts = _parse_user_datetime(raw_ts)
    applied_ts = _apply_tz_offset(parsed_ts, offset) if parsed_ts else None
    if not parsed_ts or not applied_ts:
        return JsonResponse({"ok": False, "error": "Invalid timestamp"}, status=400)
    wd.timestamp = applied_ts
    wd.date = parsed_ts.date()
    wd.save()
    return JsonResponse({"ok": True})


@login_required
def cards_list(request):
    total_cards = Card.objects.count()
    cards = Card.objects.select_related("group").all().order_by("name")
    groups = CardGroup.objects.order_by("name")
    banks = _bank_name_list()

    start_raw = (request.GET.get("start") or "").strip()
    end_raw = (request.GET.get("end") or "").strip()
    bank_filter = (request.GET.get("bank") or "").strip()
    group_filter = (request.GET.get("group") or "").strip()
    query = (request.GET.get("q") or "").strip()
    hide_zero = (request.GET.get("hide_zero") or "") == "1"
    per_page = _parse_per_page(request.GET.get("per_page"), default=50)
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
    if query:
        cards = cards.filter(
            Q(name__icontains=query)
            | Q(bank__icontains=query)
            | Q(card_number__icontains=query)
            | Q(pin__icontains=query)
            | Q(group__name__icontains=query)
            | Q(notes__icontains=query)
        )

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

    cards_list, overall = _cards_with_totals(cards, start_date, end_date)
    if hide_zero:
        cards_list = [card for card in cards_list if card.balance_total != Decimal("0")]
        overall = {
            "received": sum((card.received_total for card in cards_list), Decimal("0")),
            "withdrawn": sum((card.withdrawn_total for card in cards_list), Decimal("0")),
            "commission": sum((card.commission_total for card in cards_list), Decimal("0")),
            "balance": sum((card.balance_total for card in cards_list), Decimal("0")),
        }

    paginator = Paginator(cards_list, per_page)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    form = CardForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("cards_list")
    return render(
        request,
        "core/cards_list.html",
        {
            "page_obj": page_obj,
            "page_items": _pagination_items(page_obj),
            "form": form,
            "start": start_raw,
            "end": end_raw,
            "bank_filter": bank_filter,
            "group_filter": group_filter,
            "overall": overall,
            "groups": groups,
            "banks": banks,
            "bank_colors": _bank_color_map(),
            "query": query,
            "hide_zero": hide_zero,
            "per_page": per_page,
            "per_page_choices": PER_PAGE_CHOICES,
            "total_cards": total_cards,
        },
    )

@login_required
def cards_export(request):
    cards = Card.objects.select_related("group").all().order_by("name")
    start_raw = (request.GET.get("start") or "").strip()
    end_raw = (request.GET.get("end") or "").strip()
    bank_filter = (request.GET.get("bank") or "").strip()
    group_filter = (request.GET.get("group") or "").strip()
    query = (request.GET.get("q") or "").strip()
    hide_zero = (request.GET.get("hide_zero") or "") == "1"
    start_date = _parse_user_date(start_raw)
    end_date = _parse_user_date(end_raw)

    if bank_filter:
        cards = cards.filter(bank__icontains=bank_filter)
    if group_filter:
        cards = cards.filter(group__name__icontains=group_filter)
    if query:
        cards = cards.filter(
            Q(name__icontains=query)
            | Q(bank__icontains=query)
            | Q(card_number__icontains=query)
            | Q(pin__icontains=query)
            | Q(group__name__icontains=query)
            | Q(notes__icontains=query)
        )

    cards_list, overall = _cards_with_totals(cards, start_date, end_date)
    if hide_zero:
        cards_list = [card for card in cards_list if card.balance_total != Decimal("0")]

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="cards.csv"'
    writer = csv.writer(response)
    writer.writerow([
        "Name",
        "Bank",
        "Group",
        "Number",
        "PIN",
        "Received",
        "Withdrawn",
        "Commission",
        "Balance",
        "Status",
        "Notes",
    ])
    for card in cards_list:
        writer.writerow([
            card.name,
            card.bank,
            card.group.name if card.group else "",
            card.card_number,
            card.pin,
            card.received_total,
            card.withdrawn_total,
            card.commission_total,
            card.balance_total,
            card.status,
            card.notes,
        ])
    return response

@login_required
def cards_search(request):
    cards = Card.objects.select_related("group").all().order_by("name")
    start_raw = (request.GET.get("start") or "").strip()
    end_raw = (request.GET.get("end") or "").strip()
    bank_filter = (request.GET.get("bank") or "").strip()
    group_filter = (request.GET.get("group") or "").strip()
    query = (request.GET.get("q") or "").strip()
    hide_zero = (request.GET.get("hide_zero") or "") == "1"
    start_date = _parse_user_date(start_raw)
    end_date = _parse_user_date(end_raw)

    if bank_filter:
        cards = cards.filter(bank__icontains=bank_filter)
    if group_filter:
        cards = cards.filter(group__name__icontains=group_filter)
    if query:
        cards = cards.filter(
            Q(name__icontains=query)
            | Q(bank__icontains=query)
            | Q(card_number__icontains=query)
            | Q(pin__icontains=query)
            | Q(group__name__icontains=query)
            | Q(notes__icontains=query)
        )

    cards_list, overall = _cards_with_totals(cards, start_date, end_date)
    if hide_zero:
        cards_list = [card for card in cards_list if card.balance_total != Decimal("0")]
        overall = {
            "received": sum((card.received_total for card in cards_list), Decimal("0")),
            "withdrawn": sum((card.withdrawn_total for card in cards_list), Decimal("0")),
            "commission": sum((card.commission_total for card in cards_list), Decimal("0")),
            "balance": sum((card.balance_total for card in cards_list), Decimal("0")),
        }
    data = []
    for card in cards_list:
        data.append(
            {
                "id": card.id,
                "name": card.name,
                "bank": card.bank or "",
                "group": card.group.name if card.group else "",
                "card_number": card.card_number or "",
                "pin": card.pin or "",
                "received": _format_spaced_number(card.received_total),
                "withdrawn": _format_spaced_number(card.withdrawn_total),
                "commission": _format_spaced_number(card.commission_total),
                "balance": _format_spaced_number(card.balance_total),
                "status": card.status,
            }
        )
    totals = {
        "received": _format_spaced_number(overall["received"]),
        "withdrawn": _format_spaced_number(overall["withdrawn"]),
        "commission": _format_spaced_number(overall["commission"]),
        "balance": _format_spaced_number(overall["balance"]),
    }
    return JsonResponse({"results": data, "totals": totals})


@login_required
def clients_list(request):
    total_clients = Client.objects.count()
    clients = Client.objects.all().order_by("name")
    query = (request.GET.get("q") or "").strip()
    if query:
        clients = clients.filter(name__icontains=query)
    per_page = _parse_per_page(request.GET.get("per_page"), default=50)
    form = ClientForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("clients_list")
    paginator = Paginator(clients, per_page)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    return render(
        request,
        "core/clients_list.html",
        {
            "page_obj": page_obj,
            "page_items": _pagination_items(page_obj),
            "form": form,
            "query": query,
            "per_page": per_page,
            "per_page_choices": PER_PAGE_CHOICES,
            "total_clients": total_clients,
        },
    )

@login_required
def clients_export(request):
    query = (request.GET.get("q") or "").strip()
    clients = Client.objects.all().order_by("name")
    if query:
        clients = clients.filter(name__icontains=query)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="clients.csv"'
    writer = csv.writer(response)
    writer.writerow(["Name", "Status", "Notes"])
    for client in clients:
        writer.writerow([client.name, client.status, client.notes])
    return response

@login_required
def clients_search(request):
    query = (request.GET.get("q") or "").strip()
    if not query:
        return JsonResponse({"results": [], "total": 0, "pages": 0, "page": 1, "per_page": 0})
    clients = Client.objects.filter(name__icontains=query).order_by("name")
    page = request.GET.get("page")
    per_page = request.GET.get("per_page")
    if page or per_page:
        per_page_value = _parse_per_page(per_page, default=50)
        paginator = Paginator(clients, per_page_value)
        page_obj = paginator.get_page(page)
        data = [{"id": c.id, "name": c.name, "status": c.status} for c in page_obj]
        payload = {"results": data}
        payload.update(_pagination_meta(paginator, page_obj))
        return JsonResponse(payload)
    data = [{"id": c.id, "name": c.name, "status": c.status} for c in clients]
    return JsonResponse({"results": data, "total": clients.count(), "pages": 1, "page": 1, "per_page": clients.count()})


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

    query = (request.GET.get("q") or "").strip()
    if query:
        txs = txs.filter(
            Q(client__name__icontains=query)
            | Q(card__name__icontains=query)
            | Q(notes__icontains=query)
        )

    if request.method == "POST":
        form = TransactionForm(request.POST, request=request)
        if form.is_valid():
            form.save()
            return redirect(request.get_full_path())
    else:
        form = TransactionForm(request=request)

    per_page = _parse_per_page(request.GET.get("per_page"), default=50)
    paginator = Paginator(txs, per_page)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    for tx in page_obj:
        tx.card_display = _card_display(tx.card)

    context = {
        "page_obj": page_obj,
        "page_items": _pagination_items(page_obj),
        "start": start_raw,
        "end": end_raw,
        "query": query,
        "per_page": per_page,
        "per_page_choices": PER_PAGE_CHOICES,
        "form": form,
        "cards": cards,
        "clients": clients,
        "card_lookup": {str(c.id): c.display_label for c in cards},
        "client_lookup": {str(c.id): c.name for c in clients},
        "bank_colors": _bank_color_map(),
    }
    return render(request, "core/transactions_list.html", context)

@login_required
def transactions_export(request):
    txs = Transaction.objects.select_related("card", "client").order_by("-timestamp")
    start_raw = (request.GET.get("start") or "").strip()
    end_raw = (request.GET.get("end") or "").strip()
    start_date = _parse_user_date(start_raw)
    end_date = _parse_user_date(end_raw)
    if start_date:
        txs = txs.filter(timestamp__date__gte=start_date)
    if end_date:
        txs = txs.filter(timestamp__date__lte=end_date)

    query = (request.GET.get("q") or "").strip()
    if query:
        txs = txs.filter(
            Q(client__name__icontains=query)
            | Q(card__name__icontains=query)
            | Q(notes__icontains=query)
        )

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="transactions.csv"'
    writer = csv.writer(response)
    writer.writerow(["Time", "Client", "Card", "RUB", "USD", "Rate", "Notes"])
    for tx in txs:
        ts = tx.timestamp.strftime("%d/%m/%Y %H:%M")
        writer.writerow([
            ts,
            tx.client.name,
            tx.card.name,
            tx.amount_rub,
            tx.amount_usd,
            tx.rate,
            tx.notes,
        ])
    return response

@login_required
def transactions_search(request):
    txs = Transaction.objects.select_related("card", "client").order_by("-timestamp")
    bank_colors = _bank_color_map()
    start_raw = (request.GET.get("start") or "").strip()
    end_raw = (request.GET.get("end") or "").strip()
    start_date = _parse_user_date(start_raw)
    end_date = _parse_user_date(end_raw)
    if start_date:
        txs = txs.filter(timestamp__date__gte=start_date)
    if end_date:
        txs = txs.filter(timestamp__date__lte=end_date)

    query = (request.GET.get("q") or "").strip()
    if query:
        txs = txs.filter(
            Q(client__name__icontains=query)
            | Q(card__name__icontains=query)
            | Q(notes__icontains=query)
        )

    page = request.GET.get("page")
    per_page = request.GET.get("per_page")
    if page or per_page:
        per_page_value = _parse_per_page(per_page, default=50)
        paginator = Paginator(txs, per_page_value)
        page_obj = paginator.get_page(page)
        txs = page_obj
    else:
        paginator = None
        page_obj = None

    data = []
    for tx in txs:
        data.append(
            {
                "id": tx.id,
                "time_iso": tx.timestamp.isoformat(),
                "client": tx.client.name,
                "card": _card_display(tx.card),
                "bank_color": bank_colors.get((tx.card.bank or "").strip(), ""),
                "rub": _format_spaced_number(tx.amount_rub),
                "usd": _format_spaced_number(tx.amount_usd),
                "rate": _format_spaced_number(tx.rate),
                "note": tx.notes or "",
            }
        )
    payload = {"results": data}
    if paginator and page_obj:
        payload.update(_pagination_meta(paginator, page_obj))
    else:
        payload.update({"total": txs.count(), "pages": 1, "page": 1, "per_page": txs.count()})
    return JsonResponse(payload)

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
        {
            "form": form,
            "title": "Add Card",
            "groups": groups,
            "banks": banks,
            "bank_colors": _bank_color_map(),
        },
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
        {
            "form": form,
            "title": "Edit Card",
            "groups": groups,
            "banks": banks,
            "bank_colors": _bank_color_map(),
        },
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
    form = TransactionForm(request.POST or None, request=request)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("transactions_list")
    return render(request, "core/transaction_form.html", {"form": form, "title": "Add Transaction"})


@login_required
def transaction_edit(request, pk: int):
    tx = get_object_or_404(Transaction, pk=pk)
    form = TransactionForm(request.POST or None, instance=tx, request=request)
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
@require_POST
def transaction_update_note(request, pk: int):
    tx = get_object_or_404(Transaction, pk=pk)
    note = ""
    if request.content_type == "application/json":
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
            note = payload.get("note", "") or ""
        except json.JSONDecodeError:
            return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    else:
        note = request.POST.get("note") or ""
    tx.notes = note
    tx.save(update_fields=["notes"])
    return JsonResponse({"ok": True})


@login_required
def card_history(request, pk: int):
    card = get_object_or_404(Card, pk=pk)

    start_raw = (request.GET.get("start") or "").strip()
    end_raw = (request.GET.get("end") or "").strip()
    type_filter = (request.GET.get("type") or "").strip().lower()
    query = (request.GET.get("q") or "").strip()
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
    total_withdrawn = Decimal("0")
    total_commission = Decimal("0")
    withdrawals = _dedupe_withdrawals_by_date(
        Withdrawal.objects.filter(**wd_filter).select_related("card")
    )
    for wd in withdrawals:
        total_withdrawn += _withdrawal_actual_amount(wd, cache)
        total_commission += wd.commission_rub or Decimal("0")

    events = _card_events(card, start_date, end_date, type_filter, query)

    context = {
        "card": card,
        "events": events,
        "received_total": received,
        "withdrawn_total": total_withdrawn,
        "commission_total": total_commission,
        "balance_total": received - total_withdrawn - total_commission,
        "start": start_raw,
        "end": end_raw,
        "type_filter": type_filter,
        "query": query,
    }
    return render(request, "core/card_history.html", context)

@login_required
def card_history_search(request, pk: int):
    card = get_object_or_404(Card, pk=pk)
    start_raw = (request.GET.get("start") or "").strip()
    end_raw = (request.GET.get("end") or "").strip()
    type_filter = (request.GET.get("type") or "").strip().lower()
    query = (request.GET.get("q") or "").strip()
    start_date = _parse_user_date(start_raw)
    end_date = _parse_user_date(end_raw)

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
    total_withdrawn = Decimal("0")
    total_commission = Decimal("0")
    withdrawals = _dedupe_withdrawals_by_date(
        Withdrawal.objects.filter(**wd_filter).select_related("card")
    )
    for wd in withdrawals:
        total_withdrawn += _withdrawal_actual_amount(wd, cache)
        total_commission += wd.commission_rub or Decimal("0")

    events = _card_events(card, start_date, end_date, type_filter, query)
    data_events = []
    for event in events:
        data_events.append(
            {
                "time": event["time_display"],
                "time_iso": event.get("time_iso") or "",
                "type": "Transaction" if event["kind"] == "transaction" else "Withdrawal",
                "client": event["client"],
                "rub": _format_spaced_number(event["rub"]) if event["rub"] is not None else "",
                "usd": _format_spaced_number(event["usd"]) if event["usd"] is not None else "",
                "withdrawn": _format_spaced_number(event["withdrawn"])
                if event["withdrawn"] is not None
                else "",
                "commission": _format_spaced_number(event["commission"])
                if event["commission"] is not None
                else "",
                "balance": _format_spaced_number(event["balance"]),
                "note": event["note"] or "",
                "withdrawal_id": event.get("withdrawal_id"),
            }
        )

    totals = {
        "received": _format_spaced_number(received),
        "withdrawn": _format_spaced_number(total_withdrawn),
        "commission": _format_spaced_number(total_commission),
        "balance": _format_spaced_number(received - total_withdrawn - total_commission),
    }
    return JsonResponse({"totals": totals, "events": data_events})
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

    query = (request.GET.get("q") or "").strip()
    rows = _payments_rows(start_date, end_date, query)
    per_page = _parse_per_page(request.GET.get("per_page"), default=50)
    paginator = Paginator(rows, per_page)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "core/payments_summary.html",
        {
            "rows": page_obj,
            "page_obj": page_obj,
            "page_items": _pagination_items(page_obj),
            "per_page": per_page,
            "per_page_choices": PER_PAGE_CHOICES,
            "start": start_raw,
            "end": end_raw,
            "query": query,
        },
    )

@login_required
def payments_search(request):
    start_raw = (request.GET.get("start") or "").strip()
    end_raw = (request.GET.get("end") or "").strip()
    start_date = _parse_user_date(start_raw)
    end_date = _parse_user_date(end_raw)
    query = (request.GET.get("q") or "").strip()
    rows = _payments_rows(start_date, end_date, query)
    page = request.GET.get("page")
    per_page = request.GET.get("per_page")
    if page or per_page:
        per_page_value = _parse_per_page(per_page, default=50)
        paginator = Paginator(rows, per_page_value)
        page_obj = paginator.get_page(page)
        rows = page_obj
    else:
        paginator = None
        page_obj = None

    data = []
    for row in rows:
        data.append(
            {
                "date": row["date"].strftime("%d/%m/%Y"),
                "client": row["client"].name,
                "rub": _format_spaced_number(row["rub"]),
                "usd": _format_spaced_number(row["usd"]),
            }
        )
    payload = {"results": data}
    if paginator and page_obj:
        payload.update(_pagination_meta(paginator, page_obj))
    else:
        payload.update({"total": len(data), "pages": 1, "page": 1, "per_page": len(data)})
    return JsonResponse(payload)

@login_required
def payments_export(request):
    start_raw = (request.GET.get("start") or "").strip()
    end_raw = (request.GET.get("end") or "").strip()
    start_date = _parse_user_date(start_raw)
    end_date = _parse_user_date(end_raw)
    query = (request.GET.get("q") or "").strip()
    rows = _payments_rows(start_date, end_date, query)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="payments.csv"'
    writer = csv.writer(response)
    writer.writerow(["Date", "Client", "RUB", "USD"])
    for row in rows:
        writer.writerow([
            row["date"].strftime("%d/%m/%Y"),
            row["client"].name,
            row["rub"],
            row["usd"],
        ])
    return response
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
