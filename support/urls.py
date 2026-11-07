from django.urls import path

from . import views

urlpatterns = [
    path("", views.submit_ticket, name="submit_ticket"),
    path("tickets/<int:pk>/", views.ticket_detail, name="ticket_detail"),
    path("tickets/<int:pk>/status/", views.ticket_status, name="ticket_status"),
    path("dashboard/", views.dashboard, name="dashboard"),
]
