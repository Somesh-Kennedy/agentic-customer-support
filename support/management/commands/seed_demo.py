from decimal import Decimal

from django.core.management.base import BaseCommand

from support.models import Customer, KnowledgeArticle, Order, Ticket

CUSTOMERS = [
    ("Ava Patel", "ava@example.com", "premium"),
    ("Liam Chen", "liam@example.com", "standard"),
    ("Sofia Garcia", "sofia@example.com", "standard"),
    ("Noah Johnson", "noah@example.com", "premium"),
]

ORDERS = [
    # email, number, status, total, items, address, tracking
    ("ava@example.com", "ORD-1001", "delivered", "89.99",
     [{"sku": "HDP-200", "name": "Wireless Headphones", "qty": 1, "price": "89.99"}],
     "12 Elm St, Austin, TX 78701", "1Z999AA10123456784"),
    ("ava@example.com", "ORD-1002", "processing", "45.00",
     [{"sku": "CBL-USB", "name": "USB-C Cable 2-pack", "qty": 3, "price": "15.00"}],
     "12 Elm St, Austin, TX 78701", ""),
    ("liam@example.com", "ORD-2001", "shipped", "129.50",
     [{"sku": "KBD-MX", "name": "Mechanical Keyboard", "qty": 1, "price": "129.50"}],
     "88 Pine Ave, Seattle, WA 98101", "9400111899223344556677"),
    ("sofia@example.com", "ORD-3001", "delivered", "649.00",
     [{"sku": "MON-27", "name": "27in 4K Monitor", "qty": 1, "price": "649.00"}],
     "5 Lake Rd, Chicago, IL 60601", "1Z999AA10198765432"),
    ("noah@example.com", "ORD-4001", "processing", "210.00",
     [{"sku": "CHR-ERG", "name": "Ergonomic Chair Cushion", "qty": 2, "price": "105.00"}],
     "301 Main St, Denver, CO 80202", ""),
]

ARTICLES = [
    ("refund-policy", "Refund policy",
     "Delivered items can be refunded within 30 days of delivery. Damaged or defective items are "
     "refunded in full without needing a return. Change-of-mind refunds require the item be returned "
     "unused. Refunds go back to the original payment method within 5-7 business days. Agents may "
     "approve refunds up to $200; larger refunds need a supervisor.",
     "refund money back return damaged defective broken"),
    ("cancellation-policy", "Cancelling an order",
     "Orders can be cancelled free of charge while they are still processing. Once an order has "
     "shipped it cannot be cancelled; the customer can refuse delivery or start a return instead.",
     "cancel cancellation stop order"),
    ("shipping-address-changes", "Changing a shipping address",
     "The shipping address can be changed while the order is processing. After shipment the address "
     "is locked; the customer may contact the carrier with the tracking number to redirect.",
     "address shipping change move redirect"),
    ("shipping-times", "Shipping times and tracking",
     "Standard shipping takes 3-5 business days after dispatch; premium members get 2-day shipping. "
     "Tracking numbers are emailed when the order ships. Packages not delivered 10 business days "
     "after shipping are considered lost and are reshipped or refunded.",
     "shipping delivery late where tracking lost package"),
    ("warranty", "Product warranty",
     "Electronics carry a 1-year manufacturer warranty. Warranty claims after 30 days are handled by "
     "the warranty team and need a human agent.",
     "warranty broken stopped working repair"),
    ("account-help", "Account and password help",
     "Customers can reset their password from the sign-in page using 'Forgot password'. Email address "
     "changes require identity verification by a human agent.",
     "account password login email reset"),
]

SAMPLE_TICKETS = [
    ("ava@example.com", "ORD-1001", "Headphones arrived broken",
     "The left ear cup on my headphones is cracked and makes no sound. Can I get my money back?"),
    ("ava@example.com", "ORD-1002", "Please cancel my cable order",
     "I ordered the USB-C cables by mistake, please cancel before it ships."),
    ("liam@example.com", "ORD-2001", "Where is my keyboard?",
     "My keyboard shipped a couple of days ago. When will it arrive?"),
    ("sofia@example.com", "ORD-3001", "Monitor has dead pixels",
     "My new monitor has a cluster of dead pixels. I want a full refund. This is unacceptable."),
    ("noah@example.com", "ORD-4001", "Wrong address on my order",
     "I just moved. Please ship to 47 Oak Blvd, Apt 3, Denver, CO 80203 instead."),
]


class Command(BaseCommand):
    help = "Load demo customers, orders and knowledge-base articles (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--tickets", action="store_true", help="Also queue sample tickets")

    def handle(self, *args, tickets=False, **options):
        customers = {}
        for name, email, tier in CUSTOMERS:
            customers[email], _ = Customer.objects.update_or_create(
                email=email, defaults={"name": name, "tier": tier}
            )
        for email, number, status, total, items, address, tracking in ORDERS:
            Order.objects.update_or_create(
                order_number=number,
                defaults={
                    "customer": customers[email], "status": status, "total": Decimal(total),
                    "items": items, "shipping_address": address, "tracking_number": tracking,
                },
            )
        for slug, title, body, tags in ARTICLES:
            KnowledgeArticle.objects.update_or_create(
                slug=slug, defaults={"title": title, "body": body, "tags": tags}
            )
        self.stdout.write(
            f"Seeded {len(CUSTOMERS)} customers, {len(ORDERS)} orders, {len(ARTICLES)} articles"
        )

        if tickets:
            for email, order_number, subject, body in SAMPLE_TICKETS:
                Ticket.objects.create(
                    customer=customers.get(email), email=email, order_number=order_number,
                    subject=subject, body=body,
                )
            self.stdout.write(f"Queued {len(SAMPLE_TICKETS)} sample tickets")
