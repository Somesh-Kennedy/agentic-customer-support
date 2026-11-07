import time

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from support.models import Ticket


def claim_next_ticket():
    """Atomically move the oldest queued ticket to PROCESSING (safe with several workers)."""
    with transaction.atomic():
        ticket = (
            Ticket.objects.select_for_update(skip_locked=True)
            .filter(status=Ticket.Status.QUEUED)
            .order_by("created_at")
            .first()
        )
        if ticket:
            ticket.status = Ticket.Status.PROCESSING
            ticket.started_at = timezone.now()
            ticket.save(update_fields=["status", "started_at"])
        return ticket


class Command(BaseCommand):
    help = "Poll for queued tickets and run the CrewAI support crew on each."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process the queue once, then exit")
        parser.add_argument("--interval", type=float, default=2.0, help="Seconds between polls")

    def handle(self, *args, once=False, interval=2.0, **options):
        from support_crew.runner import process_ticket

        self.stdout.write("Support worker started")
        while True:
            ticket = claim_next_ticket()
            if ticket:
                self.stdout.write(f"Processing ticket #{ticket.pk}: {ticket.subject}")
                result = process_ticket(ticket.pk)
                self.stdout.write(f"Ticket #{result.pk} -> {result.status}")
                continue
            if once:
                return
            time.sleep(interval)
