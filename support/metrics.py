from collections import Counter

from django.conf import settings
from django.db.models import Count, Q

from .models import Ticket, TicketEvent

S = Ticket.Status


def support_metrics():
    counts = dict(Ticket.objects.values_list("status").annotate(n=Count("id")))
    resolved = counts.get(S.RESOLVED, 0)
    escalated = counts.get(S.ESCALATED, 0)
    failed = counts.get(S.FAILED, 0)
    closed = resolved + escalated + failed

    def rate(n):
        return n / closed if closed else None

    escalation_rate = rate(escalated + failed)
    baseline = settings.SUPPORT_BASELINE_ESCALATION_RATE
    escalation_delta = None
    if baseline and escalation_rate is not None and float(baseline) > 0:
        escalation_delta = (escalation_rate - float(baseline)) / float(baseline)

    durations = [
        t.handling_seconds
        for t in Ticket.objects.filter(started_at__isnull=False, finished_at__isnull=False).only(
            "started_at", "finished_at"
        )
    ]

    categories = (
        Ticket.objects.exclude(category="")
        .values("category")
        .annotate(total=Count("id"), resolved=Count("id", filter=Q(status=S.RESOLVED)))
        .order_by("-total")
    )
    tool_calls = Counter(
        TicketEvent.objects.filter(phase__in=["act", "outcome"])
        .exclude(actor="worker")
        .values_list("actor", flat=True)
    ).most_common(10)

    return {
        "total": sum(counts.values()),
        "open": counts.get(S.QUEUED, 0) + counts.get(S.PROCESSING, 0),
        "resolved": resolved,
        "escalated": escalated,
        "failed": failed,
        "closed": closed,
        "resolution_rate": rate(resolved),
        "escalation_rate": escalation_rate,
        "baseline_escalation_rate": float(baseline) if baseline else None,
        "escalation_delta": escalation_delta,
        "avg_handling_seconds": sum(durations) / len(durations) if durations else None,
        "categories": [
            {**c, "rate": c["resolved"] / c["total"] if c["total"] else 0} for c in categories
        ],
        "tool_calls": tool_calls,
    }
