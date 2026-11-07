from django import forms

from .models import Ticket


class TicketForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = ["email", "order_number", "subject", "body"]
        labels = {"body": "How can we help?", "order_number": "Order number (optional)"}
        widgets = {
            "email": forms.EmailInput(attrs={"placeholder": "you@example.com"}),
            "order_number": forms.TextInput(attrs={"placeholder": "e.g. ORD-1001"}),
            "subject": forms.TextInput(attrs={"placeholder": "Short summary"}),
            "body": forms.Textarea(attrs={"rows": 6, "placeholder": "Describe the issue"}),
        }

    def clean_order_number(self):
        return self.cleaned_data["order_number"].strip().upper()
