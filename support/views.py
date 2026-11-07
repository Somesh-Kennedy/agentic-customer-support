from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .forms import TicketForm
from .metrics import support_metrics
from .models import Customer, Ticket


def submit_ticket(request):
    if request.method == "POST":
        form = TicketForm(request.POST)
        if form.is_valid():
            ticket = form.save(commit=False)
            ticket.customer = Customer.objects.filter(email__iexact=ticket.email).first()
            ticket.save()
            return redirect("ticket_detail", pk=ticket.pk)
    else:
        form = TicketForm()
    return render(request, "support/submit.html", {"form": form})


def _trace(ticket):
    events = list(ticket.events.all())
    return {
        "reason": [e for e in events if e.phase == "reason"],
        "act": [e for e in events if e.phase == "act"],
        "outcome": [e for e in events if e.phase in ("outcome", "system")],
    }


def ticket_detail(request, pk):
    ticket = get_object_or_404(Ticket.objects.select_related("customer"), pk=pk)
    return render(request, "support/ticket_detail.html", {"ticket": ticket, "trace": _trace(ticket)})


def ticket_status(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)
    return JsonResponse(
        {"id": ticket.pk, "status": ticket.status, "events": ticket.events.count(), "open": ticket.is_open}
    )


def dashboard(request):
    return render(
        request,
        "support/dashboard.html",
        {"m": support_metrics(), "tickets": Ticket.objects.all()[:25]},
    )
