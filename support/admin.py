from django.contrib import admin

from .models import Customer, KnowledgeArticle, Order, Refund, Ticket, TicketEvent


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "tier", "created_at")
    search_fields = ("name", "email")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("order_number", "customer", "status", "total", "created_at")
    list_filter = ("status",)
    search_fields = ("order_number", "customer__email")


@admin.register(KnowledgeArticle)
class KnowledgeArticleAdmin(admin.ModelAdmin):
    list_display = ("title", "slug", "tags")
    prepopulated_fields = {"slug": ("title",)}


class TicketEventInline(admin.TabularInline):
    model = TicketEvent
    extra = 0
    readonly_fields = ("phase", "actor", "summary", "payload", "created_at")


@admin.action(description="Re-queue for the agent crew")
def requeue(modeladmin, request, queryset):
    queryset.update(status=Ticket.Status.QUEUED, started_at=None, finished_at=None)


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ("id", "subject", "email", "status", "category", "priority", "created_at")
    list_filter = ("status", "category", "priority")
    search_fields = ("subject", "email", "body")
    inlines = [TicketEventInline]
    actions = [requeue]


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = ("order", "amount", "ticket", "created_at")
