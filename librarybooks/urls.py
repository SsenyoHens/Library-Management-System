from django.urls import path
from . import views

urlpatterns = [
    path('', views.login_view, name='login'),
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('logout/', views.logout_view, name='logout'),
    path('books/search/', views.book_search_view, name='book_search'),
    path('circulation/checkout/', views.checkout_view, name='checkout'),
    path('circulation/return/', views.return_view, name='return_book'),
    path('reports/', views.reports_view, name='reports'),
    path('books/add/', views.add_book_view, name='add_book'),
    path('members/add/', views.add_member_view, name='add_member'),
    path('payments/process/', views.process_payment_view, name='process_payment'),
    path('explorer/<str:table_name>/', views.table_explorer_view, name='explorer'),
    path('export/loans/csv/', views.export_loans_csv, name='export_loans_csv'),
    path('export/fines/csv/', views.export_fines_csv, name='export_fines_csv'),
    path('export/books/csv/', views.export_books_csv, name='export_books_csv'),
    path('reports/period/', views.period_report_view, name='period_report'),
    path('staff/add/', views.add_staff_view, name='add_staff'),
    path('reports/inventory/', views.inventory_report_view, name='inventory_report'),
]