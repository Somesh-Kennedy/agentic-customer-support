"""Support domain schema.

Django owns these tables (and their migrations). The MCP server in
``mcp_server/`` reads and writes the same tables with plain SQL, so every
model pins an explicit ``db_table`` and stores enums as plain strings.
"""

from django.db import models


class Customer(models.Model):
    class Tier(models.TextChoices):
        STANDARD = "standard"
        PREMIUM = "premium"

    name = models.CharField(max_length=200)
    email = models.EmailField(unique=True)
    tier = models.CharField(max_length=20, choices=Tier.choices, default=Tier.STANDARD)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "customers"

    def __str__(self):
        return f"{self.name} <{self.email}>"


class Order(models.Model):
    class Status(models.TextChoices):
        PROCESSING = "processing"
        SHIPPED = "shipped"
        DELIVERED = "delivered"
        CANCELLED = "cancelled"

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="orders")
    order_number = models.CharField(max_length=32, unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROCESSING)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    items = models.JSONField(default=list)
    shipping_address = models.TextField()
    tracking_number = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "orders"
        ordering = ["-created_at"]

    def __str__(self):
        return self.order_number


class KnowledgeArticle(models.Model):
    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=200)
    body = models.TextField()
    tags = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "kb_articles"

    def __str__(self):
        return self.title


class Ticket(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued"
        PROCESSING = "processing"
        RESOLVED = "resolved"
        ESCALATED = "escalated"
        FAILED = "failed"

    class Priority(models.TextChoices):
        LOW = "low"
        NORMAL = "normal"
        HIGH = "high"
        URGENT = "urgent"

    customer = models.ForeignKey(
        Customer, null=True, blank=True, on_delete=models.SET_NULL, related_name="tickets"
    )
    email = models.EmailField()
    subject = models.CharField(max_length=200)
    body = models.TextField()
    order_number = models.CharField(max_length=32, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    category = models.CharField(max_length=40, blank=True)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    resolution = models.TextField(blank=True, help_text="Reply sent to the customer")
    escalation_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "tickets"
        ordering = ["-created_at"]

    def __str__(self):
        return f"#{self.pk} {self.subject}"

    @property
    def is_open(self):
        return self.status in (self.Status.QUEUED, self.Status.PROCESSING)

    @property
    def handling_seconds(self):
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None


class TicketEvent(models.Model):
    """One step of the agent trace: REASON (crew), ACT (MCP tool call), OUTCOME."""

    class Phase(models.TextChoices):
        REASON = "reason"
        ACT = "act"
        OUTCOME = "outcome"
        SYSTEM = "system"

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="events")
    phase = models.CharField(max_length=10, choices=Phase.choices)
    actor = models.CharField(max_length=100)
    summary = models.TextField()
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ticket_events"
        ordering = ["created_at", "id"]


class Refund(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="refunds")
    ticket = models.ForeignKey(Ticket, null=True, on_delete=models.SET_NULL, related_name="refunds")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "refunds"
