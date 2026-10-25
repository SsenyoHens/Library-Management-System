from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import make_password
from django.db import connection
import json
import csv
from django.http import HttpResponse
from datetime import datetime, date, timedelta
from django.db import transaction
from django.contrib import messages
from functools import wraps


def role_required(allowed_roles):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')
            if request.user.role not in allowed_roles and not request.user.is_superuser:
                return render(request, 'librarybooks/access_denied.html', {'user': request.user})
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def dictfetchall(cursor):
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def login_view(request):
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')
        user = authenticate(request, username=email, password=password)
        if user is not None:
            login(request, user)
            return redirect('dashboard')
        else:
            return render(request, 'librarybooks/login.html', {'error': 'Invalid email or password'})
    return render(request, 'librarybooks/login.html')


def logout_view(request):
    logout(request)
    return redirect('login')


# ==========================================
# DASHBOARD — Accessible to ALL staff
# ==========================================
@login_required(login_url='login')
def dashboard_view(request):
    stats = {}
    recent_loans = []
    overdue_loans = []

    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM books;")
        stats['total_books'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM book_copies;")
        stats['total_copies'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM members WHERE is_active = 1;")
        stats['total_members'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM loans WHERE return_date IS NULL;")
        stats['active_loans'] = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(*) FROM loans 
            WHERE return_date IS NULL AND due_date < CURDATE();
        """)
        stats['overdue_loans'] = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COALESCE(SUM(fine_amount), 0) FROM fines 
            WHERE payment_status IN ('Unpaid', 'Partially Paid');
        """)
        stats['unpaid_fines'] = cursor.fetchone()[0]

        cursor.execute("""
            SELECT l.loan_id, b.title, m.first_name, m.last_name, l.issue_date, l.due_date
            FROM loans l
            JOIN book_copies bc ON l.copy_id = bc.copy_id
            JOIN books b ON bc.book_id = b.book_id
            JOIN members m ON l.member_id = m.member_id
            ORDER BY l.issue_date DESC
            LIMIT 5;
        """)
        recent_loans = dictfetchall(cursor)

        cursor.execute("""
            SELECT l.loan_id, b.title, m.first_name, m.last_name, 
                   l.due_date, DATEDIFF(CURDATE(), l.due_date) AS days_overdue
            FROM loans l
            JOIN book_copies bc ON l.copy_id = bc.copy_id
            JOIN books b ON bc.book_id = b.book_id
            JOIN members m ON l.member_id = m.member_id
            WHERE l.return_date IS NULL AND l.due_date < CURDATE()
            ORDER BY days_overdue DESC
            LIMIT 5;
        """)
        overdue_loans = dictfetchall(cursor)

    return render(request, 'librarybooks/dashboard.html', {
        'stats': stats,
        'recent_loans': recent_loans,
        'overdue_loans': overdue_loans,
        'user': request.user,
    })


# ==========================================
# SEARCH — Accessible to ALL staff
# ==========================================
@login_required(login_url='login')
def book_search_view(request):
    query = request.GET.get('q', '').strip()
    results = []
    search_performed = False

    if query:
        search_performed = True
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT b.book_id, b.title, b.isbn, b.publication_year,
                       c.category_name, p.publisher_name,
                       GROUP_CONCAT(a.author_name SEPARATOR ', ') AS authors,
                       (SELECT COUNT(*) FROM book_copies bc 
                        WHERE bc.book_id = b.book_id AND bc.status = 'Available') AS available_copies,
                       (SELECT COUNT(*) FROM book_copies bc 
                        WHERE bc.book_id = b.book_id) AS total_copies
                FROM books b
                LEFT JOIN categories c ON b.category_id = c.category_id
                LEFT JOIN publishers p ON b.publisher_id = p.publisher_id
                LEFT JOIN book_authors ba ON b.book_id = ba.book_id
                LEFT JOIN authors a ON ba.author_id = a.author_id
                WHERE b.title LIKE %s 
                   OR b.isbn LIKE %s
                   OR a.author_name LIKE %s
                   OR c.category_name LIKE %s
                GROUP BY b.book_id
                ORDER BY b.title
                LIMIT 50;
            """, [f'%{query}%', f'%{query}%', f'%{query}%', f'%{query}%'])
            results = dictfetchall(cursor)

    return render(request, 'librarybooks/book_search.html', {
        'query': query,
        'results': results,
        'search_performed': search_performed,
        'user': request.user,
    })


# ==========================================
# CHECKOUT — Accessible to ALL staff
# ==========================================
@login_required(login_url='login')
def checkout_view(request):
    error = None
    success = None

    if request.method == 'POST':
        copy_id = request.POST.get('copy_id', '').strip()
        member_id = request.POST.get('member_id', '').strip()
        due_date_str = request.POST.get('due_date', '').strip()

        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("""
                        SELECT bc.copy_id, bc.status, b.title
                        FROM book_copies bc
                        JOIN books b ON bc.book_id = b.book_id
                        WHERE bc.copy_id = %s;
                    """, [copy_id])
                    copy = cursor.fetchone()
                    if not copy:
                        raise Exception(f"Copy ID #{copy_id} does not exist.")
                    if copy[1] != 'Available':
                        raise Exception(f"Copy #{copy_id} ('{copy[2]}') is currently '{copy[1]}' and cannot be checked out.")

                    cursor.execute("""
                        SELECT member_id, first_name, last_name, is_active
                        FROM members WHERE member_id = %s;
                    """, [member_id])
                    member = cursor.fetchone()
                    if not member:
                        raise Exception(f"Member ID #{member_id} does not exist.")
                    if not member[3]:
                        raise Exception(f"Member {member[1]} {member[2]} is deactivated and cannot borrow.")

                    cursor.execute("""
                        SELECT COUNT(*) FROM loans
                        WHERE member_id = %s AND return_date IS NULL AND due_date < CURDATE();
                    """, [member_id])
                    if cursor.fetchone()[0] > 0:
                        raise Exception(f"Member {member[1]} {member[2]} has overdue books. Please return them first.")

                    cursor.execute("""
                        SELECT COUNT(*) FROM loans
                        WHERE member_id = %s AND return_date IS NULL;
                    """, [member_id])
                    active_count = cursor.fetchone()[0]
                    if active_count >= 2:
                        raise Exception(f"Member {member[1]} {member[2]} already has {active_count} active loans. Maximum is 2.")

                    issue_date = date.today()
                    cursor.execute("""
                        INSERT INTO loans (copy_id, member_id, processed_by, issue_date, due_date)
                        VALUES (%s, %s, %s, %s, %s);
                    """, [copy_id, member_id, request.user.staff_id, issue_date, due_date_str])

                    cursor.execute("UPDATE book_copies SET status = 'On Loan' WHERE copy_id = %s;", [copy_id])

                    success = f"✅ '{copy[2]}' (Copy #{copy_id}) checked out to {member[1]} {member[2]}. Due: {due_date_str}."
        except Exception as e:
            error = str(e)

    default_due = (date.today() + timedelta(days=14)).isoformat()
    return render(request, 'librarybooks/checkout.html', {
        'error': error, 'success': success, 'default_due': default_due, 'user': request.user,
    })


# ==========================================
# RETURN — Accessible to ALL staff
# ==========================================
@login_required(login_url='login')
def return_view(request):
    error = None
    success = None
    pending_loans = []

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT l.loan_id, l.copy_id, b.title, m.member_id, m.first_name, m.last_name,
                   l.issue_date, l.due_date, DATEDIFF(CURDATE(), l.due_date) AS days_late
            FROM loans l
            JOIN book_copies bc ON l.copy_id = bc.copy_id
            JOIN books b ON bc.book_id = b.book_id
            JOIN members m ON l.member_id = m.member_id
            WHERE l.return_date IS NULL
            ORDER BY l.due_date;
        """)
        pending_loans = dictfetchall(cursor)

    if request.method == 'POST':
        copy_id = request.POST.get('copy_id', '').strip()

        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("""
                        SELECT l.loan_id, l.due_date, b.title, m.first_name, m.last_name
                        FROM loans l
                        JOIN book_copies bc ON l.copy_id = bc.copy_id
                        JOIN books b ON bc.book_id = b.book_id
                        JOIN members m ON l.member_id = m.member_id
                        WHERE l.copy_id = %s AND l.return_date IS NULL;
                    """, [copy_id])
                    loan = cursor.fetchone()
                    if not loan:
                        raise Exception(f"No active loan found for Copy #{copy_id}.")

                    loan_id, due_date, title, first_name, last_name = loan
                    return_date = date.today()

                    cursor.execute("""
                        UPDATE loans
                        SET return_date = %s, returned_processed_by = %s
                        WHERE loan_id = %s;
                    """, [return_date, request.user.staff_id, loan_id])

                    cursor.execute("UPDATE book_copies SET status = 'Available' WHERE copy_id = %s;", [copy_id])

                    days_late = (return_date - due_date).days
                    fine_message = ""
                    if days_late > 0:
                        fine_amount = days_late * 500
                        cursor.execute("""
                            INSERT INTO fines (loan_id, fine_amount, payment_status)
                            VALUES (%s, %s, 'Unpaid');
                        """, [loan_id, fine_amount])
                        fine_message = f" ⚠️ Overdue by {days_late} day(s). Fine of {fine_amount:,} UGX added."

                    success = f"✅ '{title}' (Copy #{copy_id}) returned by {first_name} {last_name}.{fine_message}"
        except Exception as e:
            error = str(e)

    return render(request, 'librarybooks/return.html', {
        'error': error, 'success': success, 'pending_loans': pending_loans, 'user': request.user,
    })


# ==========================================
# REPORTS — Librarian + Admin ONLY
# ==========================================
@login_required(login_url='login')
@role_required(['Librarian', 'Admin'])
def reports_view(request):
    reports = {}

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT b.title, a.author_name, COUNT(l.loan_id) AS times_borrowed
            FROM loans l
            JOIN book_copies bc ON l.copy_id = bc.copy_id
            JOIN books b ON bc.book_id = b.book_id
            LEFT JOIN book_authors ba ON b.book_id = ba.book_id
            LEFT JOIN authors a ON ba.author_id = a.author_id
            GROUP BY b.book_id, b.title, a.author_name
            ORDER BY times_borrowed DESC
            LIMIT 10;
        """)
        reports['most_borrowed'] = dictfetchall(cursor)

        cursor.execute("""
            SELECT m.member_id, m.first_name, m.last_name, m.email,
                   COUNT(l.loan_id) AS overdue_count
            FROM loans l
            JOIN members m ON l.member_id = m.member_id
            WHERE l.return_date IS NULL AND l.due_date < CURDATE()
            GROUP BY m.member_id, m.first_name, m.last_name, m.email
            ORDER BY overdue_count DESC;
        """)
        reports['overdue_members'] = dictfetchall(cursor)

        cursor.execute("""
            SELECT payment_method, 
                   COUNT(payment_id) AS num_payments,
                   SUM(amount_paid) AS total_amount
            FROM payments
            GROUP BY payment_method
            ORDER BY total_amount DESC;
        """)
        reports['fines_by_method'] = dictfetchall(cursor)

        cursor.execute("""
            SELECT c.category_name, 
                   COUNT(l.loan_id) AS total_loans,
                   COUNT(DISTINCT b.book_id) AS distinct_books_borrowed
            FROM categories c
            LEFT JOIN books b ON c.category_id = b.category_id
            LEFT JOIN book_copies bc ON b.book_id = bc.book_id
            LEFT JOIN loans l ON bc.copy_id = l.copy_id
            GROUP BY c.category_id, c.category_name
            HAVING total_loans > 0
            ORDER BY total_loans DESC;
        """)
        reports['category_popularity'] = dictfetchall(cursor)

        cursor.execute("""
            SELECT DATE(l.issue_date) AS day, 
                   COUNT(l.loan_id) AS books_issued
            FROM loans l
            WHERE l.issue_date >= CURDATE() - INTERVAL 7 DAY
            GROUP BY DATE(l.issue_date)
            ORDER BY day DESC;
        """)
        reports['daily_activity'] = dictfetchall(cursor)

    context = {
        'reports': reports,
        'user': request.user,
        'chart_books': json.dumps({
            'labels': [b['title'] for b in reports['most_borrowed']],
            'data': [b['times_borrowed'] for b in reports['most_borrowed']],
        }),
        'chart_fines': json.dumps({
            'labels': [f['payment_method'] for f in reports['fines_by_method']],
            'data': [float(f['total_amount']) for f in reports['fines_by_method']],
        }),
        'chart_daily': json.dumps({
            'labels': [str(d['day']) for d in reversed(reports['daily_activity'])],
            'data': [d['books_issued'] for d in reversed(reports['daily_activity'])],
        }),
    }
    return render(request, 'librarybooks/reports.html', context)


# ==========================================
# ADD BOOK — Librarian + Admin ONLY
# ==========================================
@login_required(login_url='login')
@role_required(['Librarian', 'Admin'])
def add_book_view(request):
    error = None
    success = None
    categories = []
    publishers = []

    with connection.cursor() as cursor:
        cursor.execute("SELECT category_id, category_name FROM categories ORDER BY category_name;")
        categories = dictfetchall(cursor)
        cursor.execute("SELECT publisher_id, publisher_name FROM publishers ORDER BY publisher_name;")
        publishers = dictfetchall(cursor)

    if request.method == 'POST':
        isbn = request.POST.get('isbn', '').strip()
        title = request.POST.get('title', '').strip()
        pub_year = request.POST.get('publication_year', '').strip()
        cost = request.POST.get('replacement_cost', '').strip()
        category_id = request.POST.get('category_id', '').strip()
        publisher_id = request.POST.get('publisher_id', '').strip()

        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT COUNT(*) FROM books WHERE isbn = %s;", [isbn])
                    if cursor.fetchone()[0] > 0:
                        raise Exception(f"A book with ISBN '{isbn}' already exists.")

                    cursor.execute("""
                        INSERT INTO books (isbn, title, publication_year, replacement_cost, category_id, publisher_id)
                        VALUES (%s, %s, %s, %s, %s, %s);
                    """, [isbn, title, pub_year, cost if cost else None, category_id, publisher_id])

                success = f"✅ Book '{title}' successfully added to the catalog."
        except Exception as e:
            error = str(e)

    return render(request, 'librarybooks/add_book.html', {
        'categories': categories, 'publishers': publishers,
        'error': error, 'success': success, 'user': request.user,
    })


# ==========================================
# ADD MEMBER — Librarian + Admin ONLY
# ==========================================
@login_required(login_url='login')
@role_required(['Librarian', 'Admin'])
def add_member_view(request):
    error = None
    success = None

    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip()
        phone = request.POST.get('phone_number', '').strip()
        member_type = request.POST.get('member_type', '').strip()
        date_joined = request.POST.get('date_joined', '').strip()

        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT COUNT(*) FROM members WHERE email = %s;", [email])
                    if cursor.fetchone()[0] > 0:
                        raise Exception(f"A member with email '{email}' already exists.")

                    cursor.execute("""
                        INSERT INTO members (first_name, last_name, email, phone_number, member_type, date_joined, is_active)
                        VALUES (%s, %s, %s, %s, %s, %s, 1);
                    """, [first_name, last_name, email, phone, member_type, date_joined])

                success = f"✅ Member '{first_name} {last_name}' registered successfully."
        except Exception as e:
            error = str(e)

    return render(request, 'librarybooks/add_member.html', {
        'error': error, 'success': success, 'user': request.user,
    })


# ==========================================
# PROCESS PAYMENT — Librarian + Admin ONLY
# ==========================================
@login_required(login_url='login')
@role_required(['Librarian', 'Admin'])
def process_payment_view(request):
    error = None
    success = None
    unpaid_fines = []

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT f.fine_id, f.loan_id, f.fine_amount, f.payment_status,
                   m.member_id, m.first_name, m.last_name, m.email,
                   b.title,
                   COALESCE((SELECT SUM(amount_paid) FROM payments p WHERE p.fine_id = f.fine_id), 0) AS amount_paid_so_far
            FROM fines f
            JOIN loans l ON f.loan_id = l.loan_id
            JOIN members m ON l.member_id = m.member_id
            JOIN book_copies bc ON l.copy_id = bc.copy_id
            JOIN books b ON bc.book_id = b.book_id
            WHERE f.payment_status IN ('Unpaid', 'Partially Paid')
            ORDER BY f.fine_id;
        """)
        unpaid_fines = dictfetchall(cursor)

    if request.method == 'POST':
        fine_id = request.POST.get('fine_id', '').strip()
        amount = request.POST.get('amount_paid', '').strip()
        method = request.POST.get('payment_method', '').strip()

        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("""
                        SELECT fine_amount, payment_status FROM fines WHERE fine_id = %s;
                    """, [fine_id])
                    fine = cursor.fetchone()
                    if not fine:
                        raise Exception(f"Fine ID #{fine_id} does not exist.")
                    
                    fine_total, current_status = fine
                    if current_status in ('Paid', 'Waived'):
                        raise Exception(f"Fine #{fine_id} is already '{current_status}' and cannot accept more payments.")

                    cursor.execute("""
                        SELECT COALESCE(SUM(amount_paid), 0) FROM payments WHERE fine_id = %s;
                    """, [fine_id])
                    already_paid = cursor.fetchone()[0]
                    remaining = float(fine_total) - float(already_paid)
                    
                    if float(amount) > remaining:
                        raise Exception(f"Overpayment! Remaining balance is only {remaining:,.0f} UGX.")

                    cursor.execute("""
                        INSERT INTO payments (fine_id, amount_paid, payment_method, processed_by)
                        VALUES (%s, %s, %s, %s);
                    """, [fine_id, amount, method, request.user.staff_id])

                success = f"✅ Payment of {float(amount):,.0f} UGX ({method}) processed for Fine #{fine_id}."

                with connection.cursor() as cursor:
                    cursor.execute("""
                        SELECT f.fine_id, f.loan_id, f.fine_amount, f.payment_status,
                               m.member_id, m.first_name, m.last_name, m.email, b.title,
                               COALESCE((SELECT SUM(amount_paid) FROM payments p WHERE p.fine_id = f.fine_id), 0) AS amount_paid_so_far
                        FROM fines f
                        JOIN loans l ON f.loan_id = l.loan_id
                        JOIN members m ON l.member_id = m.member_id
                        JOIN book_copies bc ON l.copy_id = bc.copy_id
                        JOIN books b ON bc.book_id = b.book_id
                        WHERE f.payment_status IN ('Unpaid', 'Partially Paid')
                        ORDER BY f.fine_id;
                    """)
                    unpaid_fines = dictfetchall(cursor)

        except Exception as e:
            error = str(e)

    return render(request, 'librarybooks/payments.html', {
        'unpaid_fines': unpaid_fines,
        'error': error,
        'success': success,
        'user': request.user,
    })


# ==========================================
# DATA EXPLORER — Admin ONLY
# ==========================================
@login_required(login_url='login')
@role_required(['Admin'])
def table_explorer_view(request, table_name):
    allowed_tables = {
        'members': 'Members',
        'staff': 'Staff',
        'books': 'Books',
        'book_copies': 'Book Copies',
        'authors': 'Authors',
        'book_authors': 'Book Authors (Junction)',
        'categories': 'Categories',
        'publishers': 'Publishers',
        'loans': 'Loans',
        'fines': 'Fines',
        'payments': 'Payments',
        'reservations': 'Reservations',
    }

    if table_name not in allowed_tables:
        return redirect('dashboard')

    rows = []
    columns = []
    
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT * FROM `{table_name}` LIMIT 100;")
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()

    return render(request, 'librarybooks/explorer.html', {
        'tables': allowed_tables,
        'current_table': table_name,
        'current_table_label': allowed_tables[table_name],
        'columns': columns,
        'rows': rows,
        'user': request.user,
    })


# ==========================================
# CSV EXPORTS — Librarian + Admin ONLY
# ==========================================
@login_required(login_url='login')
@role_required(['Librarian', 'Admin'])
def export_loans_csv(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="loans_report_{datetime.now().strftime("%Y%m%d")}.csv"'
    
    writer = csv.writer(response)
    writer.writerow(['Loan ID', 'Member Name', 'Member Email', 'Book Title', 'Issue Date', 'Due Date', 'Return Date', 'Status'])
    
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT l.loan_id, m.first_name, m.last_name, m.email, b.title, l.issue_date, l.due_date, l.return_date
            FROM loans l
            JOIN members m ON l.member_id = m.member_id
            JOIN book_copies bc ON l.copy_id = bc.copy_id
            JOIN books b ON bc.book_id = b.book_id
            ORDER BY l.loan_id DESC;
        """)
        for row in cursor.fetchall():
            if row[7] is not None:
                status = 'Returned'
            elif row[6] < datetime.now().date():
                status = 'Overdue'
            else:
                status = 'Active'
            
            writer.writerow([row[0], f"{row[1]} {row[2]}", row[3], row[4], row[5], row[6], row[7] or 'Not Returned', status])
            
    return response


@login_required(login_url='login')
@role_required(['Librarian', 'Admin'])
def export_fines_csv(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="fines_report_{datetime.now().strftime("%Y%m%d")}.csv"'
    
    writer = csv.writer(response)
    writer.writerow(['Fine ID', 'Loan ID', 'Member Name', 'Book Title', 'Fine Amount (UGX)', 'Status', 'Amount Paid', 'Balance'])
    
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT f.fine_id, f.loan_id, m.first_name, m.last_name, b.title, f.fine_amount, f.payment_status,
                   COALESCE((SELECT SUM(amount_paid) FROM payments p WHERE p.fine_id = f.fine_id), 0) AS paid
            FROM fines f
            JOIN loans l ON f.loan_id = l.loan_id
            JOIN members m ON l.member_id = m.member_id
            JOIN book_copies bc ON l.copy_id = bc.copy_id
            JOIN books b ON bc.book_id = b.book_id
            ORDER BY f.fine_id DESC;
        """)
        for row in cursor.fetchall():
            balance = float(row[5]) - float(row[7])
            writer.writerow([row[0], row[1], f"{row[2]} {row[3]}", row[4], f"{row[5]:,.2f}", row[6], f"{row[7]:,.2f}", f"{balance:,.2f}"])
            
    return response


@login_required(login_url='login')
@role_required(['Librarian', 'Admin'])
def export_books_csv(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="books_catalog_{datetime.now().strftime("%Y%m%d")}.csv"'
    
    writer = csv.writer(response)
    writer.writerow(['Book ID', 'ISBN', 'Title', 'Author(s)', 'Category', 'Publisher', 'Year', 'Total Copies', 'Available Copies'])
    
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT b.book_id, b.isbn, b.title, 
                   GROUP_CONCAT(a.author_name SEPARATOR ', ') AS authors,
                   c.category_name, p.publisher_name, b.publication_year,
                   (SELECT COUNT(*) FROM book_copies bc WHERE bc.book_id = b.book_id) AS total_copies,
                   (SELECT COUNT(*) FROM book_copies bc WHERE bc.book_id = b.book_id AND bc.status = 'Available') AS avail_copies
            FROM books b
            LEFT JOIN categories c ON b.category_id = c.category_id
            LEFT JOIN publishers p ON b.publisher_id = p.publisher_id
            LEFT JOIN book_authors ba ON b.book_id = ba.book_id
            LEFT JOIN authors a ON ba.author_id = a.author_id
            GROUP BY b.book_id
            ORDER BY b.title;
        """)
        for row in cursor.fetchall():
            writer.writerow(row)
            
    return response


# ==========================================
# PERIOD REPORT — Librarian + Admin ONLY
# ==========================================
@login_required(login_url='login')
@role_required(['Librarian', 'Admin'])
def period_report_view(request):
    start_date = request.GET.get('start_date', '')
    end_date = request.GET.get('end_date', '')
    report_type = request.GET.get('report_type', 'loans')
    
    results = []
    title = ""
    columns = []

    if start_date and end_date:
        with connection.cursor() as cursor:
            if report_type == 'loans':
                title = f"Loans Issued Between {start_date} and {end_date}"
                columns = ['Loan ID', 'Member Name', 'Book Title', 'Issue Date', 'Due Date', 'Status']
                cursor.execute("""
                    SELECT l.loan_id, CONCAT(m.first_name, ' ', m.last_name) AS member_name, 
                           b.title, l.issue_date, l.due_date, 
                           CASE WHEN l.return_date IS NULL THEN 'Active' ELSE 'Returned' END AS status
                    FROM loans l
                    JOIN members m ON l.member_id = m.member_id
                    JOIN book_copies bc ON l.copy_id = bc.copy_id
                    JOIN books b ON bc.book_id = b.book_id
                    WHERE l.issue_date BETWEEN %s AND %s
                    ORDER BY l.issue_date;
                """, [start_date, end_date])
                results = cursor.fetchall()
            else:
                title = f"Fines Generated Between {start_date} and {end_date}"
                columns = ['Fine ID', 'Member Name', 'Book Title', 'Fine Amount (UGX)', 'Status']
                cursor.execute("""
                    SELECT f.fine_id, CONCAT(m.first_name, ' ', m.last_name) AS member_name, 
                           b.title, f.fine_amount, f.payment_status
                    FROM fines f
                    JOIN loans l ON f.loan_id = l.loan_id
                    JOIN members m ON l.member_id = m.member_id
                    JOIN book_copies bc ON l.copy_id = bc.copy_id
                    JOIN books b ON bc.book_id = b.book_id
                    WHERE l.issue_date BETWEEN %s AND %s
                    ORDER BY l.issue_date;
                """, [start_date, end_date])
                results = cursor.fetchall()

    return render(request, 'librarybooks/period_report.html', {
        'start_date': start_date,
        'end_date': end_date,
        'report_type': report_type,
        'title': title,
        'columns': columns,
        'results': results,
        'user': request.user,
    })


# ==========================================
# ADD STAFF — Admin ONLY
# ==========================================
@login_required(login_url='login')
@role_required(['Admin'])
def add_staff_view(request):
    error = None
    success = None

    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip()
        phone = request.POST.get('phone_number', '').strip()
        role = request.POST.get('role', '').strip()
        hire_date = request.POST.get('hire_date', '').strip()
        password = request.POST.get('password', '').strip()

        try:
            if len(password) < 6:
                raise Exception("Password must be at least 6 characters long.")

            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT COUNT(*) FROM staff WHERE email = %s OR username = %s;", [email, email])
                    if cursor.fetchone()[0] > 0:
                        raise Exception(f"A staff member with email '{email}' already exists.")

                    hashed_password = make_password(password)

                    cursor.execute("""
                        INSERT INTO staff (first_name, last_name, email, phone_number, role, hire_date, is_active, is_staff, password, username, date_joined)
                        VALUES (%s, %s, %s, %s, %s, %s, 1, 1, %s, %s, NOW());
                    """, [first_name, last_name, email, phone, role, hire_date, hashed_password, email])

                success = f"✅ Staff member {first_name} {last_name} created successfully. They can now log in with their email and password."

        except Exception as e:
            error = str(e)

    return render(request, 'librarybooks/add_staff.html', {
        'error': error, 'success': success, 'user': request.user,
    })

@login_required(login_url='login')
@role_required(['Librarian', 'Admin'])
def inventory_report_view(request):
    category_breakdown = []
    status_breakdown = []
    top_books = []
    totals = {}

    with connection.cursor() as cursor:
        # 1. Category breakdown — titles, physical copies, total asset value
        cursor.execute("""
            SELECT c.category_name,
                   COUNT(DISTINCT b.book_id) AS total_titles,
                   COUNT(bc.copy_id) AS total_copies,
                   COALESCE(SUM(b.replacement_cost), 0) AS total_value
            FROM categories c
            LEFT JOIN books b ON c.category_id = b.category_id
            LEFT JOIN book_copies bc ON b.book_id = bc.book_id
            GROUP BY c.category_id, c.category_name
            HAVING total_copies > 0
            ORDER BY total_copies DESC;
        """)
        category_breakdown = dictfetchall(cursor)

        # 2. Physical status breakdown
        cursor.execute("""
            SELECT status, COUNT(*) AS copy_count
            FROM book_copies
            GROUP BY status
            ORDER BY copy_count DESC;
        """)
        status_breakdown = dictfetchall(cursor)

        # 3. Top 10 most stocked books (title + how many physical copies)
        cursor.execute("""
            SELECT b.title, c.category_name,
                   COUNT(bc.copy_id) AS copy_count,
                   SUM(CASE WHEN bc.status = 'Available' THEN 1 ELSE 0 END) AS available,
                   SUM(CASE WHEN bc.status = 'On Loan' THEN 1 ELSE 0 END) AS on_loan
            FROM books b
            JOIN categories c ON b.category_id = c.category_id
            LEFT JOIN book_copies bc ON b.book_id = bc.book_id
            GROUP BY b.book_id, b.title, c.category_name
            ORDER BY copy_count DESC
            LIMIT 10;
        """)
        top_books = dictfetchall(cursor)

        # 4. Summary totals
        cursor.execute("SELECT COUNT(*) FROM books;")
        totals['total_titles'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM book_copies;")
        totals['total_copies'] = cursor.fetchone()[0]

        cursor.execute("SELECT COALESCE(SUM(replacement_cost), 0) FROM books;")
        totals['catalog_value'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM book_copies WHERE status = 'Available';")
        totals['available_copies'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM book_copies WHERE status = 'On Loan';")
        totals['on_loan_copies'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM book_copies WHERE status IN ('Lost', 'Withdrawn');")
        totals['lost_or_withdrawn'] = cursor.fetchone()[0]

    # JSON data for the charts
    context = {
        'category_breakdown': category_breakdown,
        'status_breakdown': status_breakdown,
        'top_books': top_books,
        'totals': totals,
        'user': request.user,
        'chart_categories': json.dumps({
            'labels': [c['category_name'] for c in category_breakdown],
            'data': [c['total_copies'] for c in category_breakdown],
        }),
        'chart_status': json.dumps({
            'labels': [s['status'] for s in status_breakdown],
            'data': [s['copy_count'] for s in status_breakdown],
        }),
    }
    return render(request, 'librarybooks/inventory_report.html', context)