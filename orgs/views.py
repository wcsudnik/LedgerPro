from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from .models import Institution, Organization, Project, CapitalRequest, AuditLog, CreditEvent
from django.db import models, transaction
from django.db.models import Q
from decimal import Decimal, InvalidOperation
from django.utils.html import strip_tags, escape
from .utils import get_user_institution, is_platform_admin, is_any_superuser, is_admin_for_org, can_access_org
from django.utils import timezone
from django.db.models.functions import ExtractYear, ExtractMonth
from django.db.models import Sum
from fpdf import FPDF
import io
from django.http import HttpResponse
from django.utils.text import slugify
import datetime


# Permission helpers are imported from utils.py


def landing(request):
    """The public landing page."""
    if request.user.is_authenticated:
        return redirect('dashboard')
    return render(request, 'orgs/landing.html')


@login_required
def dashboard(request):
    user = request.user
    institution = get_user_institution(user)
    
    if is_platform_admin(user):
        organizations = Organization.objects.all()
        role = 'platform_admin'
    elif institution and user == institution.superuser:
        organizations = Organization.objects.filter(institution=institution)
        role = 'institution_superuser'
    elif Organization.objects.filter(admins=user).exists():
        organizations = Organization.objects.filter(admins=user)
        role = 'admin'
    else:
        organizations = user.officer_of.all()
        role = 'officer'

    return render(request, 'orgs/dashboard.html', {
        'organizations': organizations,
        'role': role,
        'is_admin': role in ('platform_admin', 'institution_superuser', 'admin'),
        'institution': institution,
    })


@login_required
def org_detail(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    if not can_access_org(request.user, org):
        return redirect('dashboard')

    is_admin = is_admin_for_org(request.user, org)
    is_any_super = is_any_superuser(request.user)
    projects = org.projects.all()
    req_qs = org.requests.all().order_by('-created_at')
    logs = org.audit_logs.all().order_by('-timestamp')

    return render(request, 'orgs/org_detail.html', {
        'org': org,
        'projects': projects,
        'requests': req_qs,
        'logs': logs,
        'is_admin': is_admin,
        'is_superuser': is_any_super,
    })


@login_required
def project_detail(request, org_id, project_id):
    org = get_object_or_404(Organization, id=org_id)
    if not can_access_org(request.user, org):
        return redirect('dashboard')

    project = get_object_or_404(Project, id=project_id, organization=org)
    req_qs = project.requests.all().order_by('-created_at')
    credits = project.credits.all().order_by('-created_at')
    is_admin = is_admin_for_org(request.user, org)

    return render(request, 'orgs/project_detail.html', {
        'org': org,
        'project': project,
        'requests': req_qs,
        'credits': credits,
        'is_admin': is_admin,
        'sources': CreditEvent.SOURCE_CHOICES,
    })


@login_required
def record_credit(request, org_id, project_id):
    org = get_object_or_404(Organization, id=org_id)
    if not is_admin_for_org(request.user, org):
        return redirect('dashboard')
    
    project = get_object_or_404(Project, id=project_id, organization=org)
    
    if request.method == 'POST':
        description = request.POST.get('description', '').strip()
        source = request.POST.get('source', 'OTHER')
        raw_amount = request.POST.get('amount')
        
        try:
            amount = Decimal(raw_amount)
            if amount <= 0:
                raise ValueError
        except (InvalidOperation, ValueError, TypeError):
             messages.error(request, "Please enter a valid positive dollar amount for the credit.")
             return redirect('project_detail', org_id=org.id, project_id=project.id)

        with transaction.atomic():
            locked_project = Project.objects.select_for_update().get(id=project.id)
            CreditEvent.objects.create(
                project=locked_project,
                amount=amount,
                source=source,
                description=escape(description)
            )
            
            # Increase the project's available budget
            locked_project.allocated_budget += amount
            locked_project.save()
            
            AuditLog.objects.create(
                organization=org,
                user=request.user,
                action=f"Recorded positive credit event for project '{locked_project.name}': {description} (${amount:,.2f})"
            )
        
        messages.success(request, f"Credit of ${amount:,.2f} recorded for {project.name}.")
        
    return redirect('project_detail', org_id=org.id, project_id=project.id)


@login_required
def all_requests(request):
    """Show capital requests scoped strictly to what the user has access to."""
    user = request.user
    institution = get_user_institution(user)

    if is_platform_admin(user):
        req_qs = CapitalRequest.objects.all().order_by('-created_at')
    elif institution and user == institution.superuser:
        # Institution superuser sees all requests in their institution
        req_qs = CapitalRequest.objects.filter(organization__institution=institution).order_by('-created_at')
    elif Organization.objects.filter(admins=user).exists():
        # Admins only see requests from orgs they admin
        orgs = Organization.objects.filter(admins=user)
        req_qs = CapitalRequest.objects.filter(organization__in=orgs).order_by('-created_at')
    else:
        # Officers only see requests from their own orgs
        orgs = user.officer_of.all()
        req_qs = CapitalRequest.objects.filter(organization__in=orgs).order_by('-created_at')

    return render(request, 'orgs/all_requests.html', {'requests': req_qs})


@login_required
def submit_request(request, org_id, project_id):
    org = get_object_or_404(Organization, id=org_id)
    if not can_access_org(request.user, org):
        return redirect('dashboard')

    project = get_object_or_404(Project, id=project_id, organization=org)

    if request.method == 'POST':
        raw_amount = request.POST.get('amount')
        purpose = request.POST.get('purpose', '').strip()
        category = request.POST.get('category', 'MISC')

        try:
            amount = Decimal(raw_amount)
            if amount <= 0:
                raise ValueError("Amount must be positive.")
        except (InvalidOperation, ValueError, TypeError):
            return render(request, 'orgs/submit_request.html', {
                'org': org, 'project': project,
                'error': "Please enter a valid positive dollar amount.",
                'categories': CapitalRequest.CATEGORY_CHOICES
            })

        with transaction.atomic():
            req = CapitalRequest.objects.create(
                organization=org,
                project=project,
                submitted_by=request.user,
                amount=amount,
                category=category,
                # Escape the purpose text to satisfy the 'no code' requirement
                purpose=escape(purpose),
            )
            AuditLog.objects.create(
                organization=org,
                user=request.user,
                action=f"Submitted capital request #{req.id} for ${amount:,.2f} under project '{project.name}'."
            )
        messages.success(request, "Capital request submitted for admin review.")
        return redirect('project_detail', org_id=org.id, project_id=project.id)

    return render(request, 'orgs/submit_request.html', {
        'org': org, 
        'project': project,
        'categories': CapitalRequest.CATEGORY_CHOICES
    })


@login_required
def review_request(request, req_id):
    req = get_object_or_404(CapitalRequest, id=req_id)
    if not is_admin_for_org(request.user, req.organization):
        return redirect('dashboard')

    if request.method == 'POST':
        if req.status != 'PENDING':
            messages.warning(request, "This request has already been reviewed.")
            return redirect('all_requests')

        action = request.POST.get('action')
        note = request.POST.get('note', '').strip()

        if action not in ['approve', 'reject']:
            return redirect('review_request', req_id=req_id)

        with transaction.atomic():
            locked_req = CapitalRequest.objects.select_for_update().get(id=req.id)
            
            if locked_req.status != 'PENDING':
                messages.warning(request, "This request has already been reviewed.")
                return redirect('all_requests')

            if action == 'approve':
                locked_project = Project.objects.select_for_update().get(id=locked_req.project_id)
                # Security: Check for sufficient balance before approving
                if locked_req.amount > locked_project.allocated_budget:
                    messages.error(request, f"Cannot approve: Request exceeds project budget (Available: ${locked_project.allocated_budget:,.2f})")
                    return redirect('review_request', req_id=req_id)

                locked_req.status = 'APPROVED'
                # Debit from project budget. 
                # Note: We do NOT subtract from organization.budget here because 
                # that was already deducted when the funds were originally allocated to the project.
                locked_project.allocated_budget -= locked_req.amount
                locked_project.save()
            else:
                locked_req.status = 'REJECTED'

            locked_req.admin_note = escape(note) # Safeguard notes
            locked_req.save()

            AuditLog.objects.create(
                organization=locked_req.organization,
                user=request.user,
                action=f"{action.capitalize()}d capital request #{locked_req.id} for ${locked_req.amount:,.2f}. Note: {note or 'None'}"
            )

        messages.success(request, f"Request #{req.id} has been {req.status.lower()}.")
        return redirect('all_requests')

    return render(request, 'orgs/review_request.html', {'req': req})


@login_required
def allocate_funds(request, org_id, project_id):
    """Transfer funds from the org's main budget to a project's allocated budget."""
    org = get_object_or_404(Organization, id=org_id)
    if not is_admin_for_org(request.user, org):
        return redirect('dashboard')

    project = get_object_or_404(Project, id=project_id, organization=org)

    if request.method == 'POST':
        raw_amount = request.POST.get('amount')
        try:
            amount = Decimal(raw_amount)
            if amount <= 0:
                raise ValueError()
        except (InvalidOperation, ValueError, TypeError):
            return render(request, 'orgs/allocate_funds.html', {
                'org': org, 'project': project,
                'error': "Please enter a valid positive dollar amount."
            })

        with transaction.atomic():
            locked_org = Organization.objects.select_for_update().get(id=org_id)
            locked_project = Project.objects.select_for_update().get(id=project_id)

            if amount > locked_org.budget:
                return render(request, 'orgs/allocate_funds.html', {
                    'org': locked_org, 'project': locked_project,
                    'error': f"Insufficient org budget. Available: ${locked_org.budget:,.2f}"
                })

            locked_org.budget -= amount
            locked_org.save()
            locked_project.allocated_budget += amount
            locked_project.save()
            AuditLog.objects.create(
                organization=locked_org,
                user=request.user,
                action=f"Allocated ${amount:,.2f} from org budget to project '{locked_project.name}'."
            )

        messages.success(request, f"${amount:,.2f} allocated to {project.name}.")
        return redirect('project_detail', org_id=org.id, project_id=project.id)

    return render(request, 'orgs/allocate_funds.html', {'org': org, 'project': project})


# ── Superuser views ────────────────────────────────────────────────────────────

@login_required
def superuser_dashboard(request):
    user = request.user
    institution = get_user_institution(user)
    
    # Platform Admin case
    if is_platform_admin(user):
        all_users = User.objects.all().order_by('username')
        all_orgs = Organization.objects.all()
        institutions = Institution.objects.all()
        
        # Simple search for platform admin
        q = request.GET.get('q')
        if q:
            all_orgs = all_orgs.filter(Q(name__icontains=q) | Q(description__icontains=q))

        return render(request, 'orgs/superuser_dashboard.html', {
            'institution': None,
            'institutions': institutions,
            'all_users': all_users,
            'all_orgs': all_orgs,
            'is_platform_admin': True,
            'search_query': q or ''
        })

    # Institution Superuser case
    if not institution or user != institution.superuser:
        return redirect('dashboard')

    # Users belonging to this institution's organizations
    institution_users = User.objects.filter(
        Q(officer_of__institution=institution) | 
        Q(org_admin_of__institution=institution)
    ).distinct()
    
    # Also include the superuser themselves
    all_users = institution_users.union(User.objects.filter(id=user.id)).order_by('username')

    all_orgs = Organization.objects.filter(institution=institution)
    
    # Organization Search
    q = request.GET.get('q')
    if q:
        all_orgs = all_orgs.filter(Q(name__icontains=q) | Q(description__icontains=q))

    return render(request, 'orgs/superuser_dashboard.html', {
        'institution': institution,
        'all_users': all_users,
        'all_orgs': all_orgs,
        'is_platform_admin': False,
        'search_query': q or ''
    })


@login_required
def create_org_frontend(request):
    """Allow Institution Superusers to create an organization via the dashboard."""
    user = request.user
    institution = get_user_institution(user)
    
    if not is_any_superuser(user):
        return redirect('dashboard')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        budget = request.POST.get('budget', '0')
        
        # For platform admin, they might need to pick an institution
        # But we focus on the Institution Superuser requirements first.
        target_inst = institution
        if is_platform_admin(user):
            inst_id = request.POST.get('institution_id')
            if not inst_id:
                messages.error(request, "Institution is required.")
                return redirect('superuser_dashboard')
            target_inst = get_object_or_404(Institution, id=inst_id)

        if not name:
            messages.error(request, "Organization name is required.")
            return redirect('superuser_dashboard')

        try:
            budget_val = Decimal(budget)
        except InvalidOperation:
            budget_val = Decimal('0.00')

        org = Organization.objects.create(
            institution=target_inst,
            name=strip_tags(name),
            description=strip_tags(description),
            budget=budget_val
        )
        AuditLog.objects.create(
            organization=org,
            user=user,
            action=f"Created organization '{org.name}' for institution '{target_inst.name}'."
        )
        messages.success(request, f"Organization '{org.name}' created.")

    return redirect('superuser_dashboard')


@login_required
def create_user_frontend(request):
    """Allow Institution Superusers to create a user via the dashboard."""
    user = request.user
    institution = get_user_institution(user)
    
    if not is_any_superuser(user):
        return redirect('dashboard')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '').strip()

        if not username or not password:
            messages.error(request, "Username and password are required.")
            return redirect('superuser_dashboard')

        if User.objects.filter(username=username).exists():
            messages.error(request, "Username already exists.")
            return redirect('superuser_dashboard')

        new_user = User.objects.create_user(username=username, email=email, password=password)
        messages.success(request, f"User '{username}' created successfully.")

    return redirect('superuser_dashboard')


@login_required
def make_admin(request, user_id):
    """Superuser promotes a user to admin of an org."""
    if not is_any_superuser(request.user):
        return redirect('dashboard')

    if request.method == 'POST':
        target_user = get_object_or_404(User, id=user_id)
        org_id = request.POST.get('org_id')
        org = get_object_or_404(Organization, id=org_id)

        if not is_platform_admin(request.user):
            institution = get_user_institution(request.user)
            if org.institution != institution:
                return redirect('dashboard')

        # We no longer grant is_staff so they can't access the admin panel
        org.admins.add(target_user)

        AuditLog.objects.create(
            organization=org,
            user=request.user,
            action=f"Granted admin rights to '{target_user.username}' for organization '{org.name}'."
        )
        messages.success(request, f"{target_user.username} is now an admin of {org.name}.")

    return redirect('superuser_dashboard')


@login_required
def revoke_admin(request, user_id):
    """Superuser revokes admin from a user."""
    if not is_any_superuser(request.user):
        return redirect('dashboard')

    if request.method == 'POST':
        target_user = get_object_or_404(User, id=user_id)
        org_id = request.POST.get('org_id')
        org = get_object_or_404(Organization, id=org_id)

        if not is_platform_admin(request.user):
            institution = get_user_institution(request.user)
            if org.institution != institution:
                return redirect('dashboard')

        org.admins.remove(target_user)
        # Only revoke staff if they aren't admin of any other org
        if not target_user.org_admin_of.exists():
            target_user.is_staff = False
            target_user.save()

        AuditLog.objects.create(
            organization=org,
            user=request.user,
            action=f"Revoked admin rights from '{target_user.username}' for organization '{org.name}'."
        )
        messages.success(request, f"Admin access revoked from {target_user.username}.")

    return redirect('superuser_dashboard')


@login_required
def transfer_superuser(request):
    """Transfer the institution superuser role to another email/user."""
    if not is_any_superuser(request.user):
        return redirect('dashboard')

    institution = get_user_institution(request.user)
    if not institution or institution.superuser != request.user:
        return redirect('dashboard')

    if request.method == 'POST':
        new_email = request.POST.get('email', '').strip()
        try:
            new_user = User.objects.get(email=new_email)
        except User.DoesNotExist:
            return render(request, 'orgs/transfer_superuser.html', {
                'institution': institution,
                'error': f"No account found with email '{new_email}'."
            })

        if new_user == request.user:
            return render(request, 'orgs/transfer_superuser.html', {
                'institution': institution,
                'error': "You cannot transfer the role to yourself."
            })

        institution.transfer_superuser(new_user)
        messages.success(request, f"Superuser role transferred to {new_user.username}. You have been logged out of elevated access.")
        return redirect('dashboard')

    return render(request, 'orgs/transfer_superuser.html', {'institution': institution})


@login_required
def create_project(request, org_id):
    """Allow officers (and admins) to create a new project for their org."""
    org = get_object_or_404(Organization, id=org_id)
    if not can_access_org(request.user, org):
        return redirect('dashboard')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        bulletin = request.POST.get('bulletin', '').strip()

        if not name:
            return render(request, 'orgs/create_project.html', {
                'org': org, 'error': "Project name is required."
            })

        with transaction.atomic():
            project = Project.objects.create(
                organization=org,
                # Security: Strip tags to satisfy the 'no code' requirement
                name=strip_tags(name),
                description=strip_tags(description),
                bulletin=strip_tags(bulletin),
            )
            AuditLog.objects.create(
                organization=org,
                user=request.user,
                action=f"Created project '{project.name}'."
            )

        messages.success(request, f"Project '{project.name}' created successfully.")
        return redirect('project_detail', org_id=org.id, project_id=project.id)

    return render(request, 'orgs/create_project.html', {'org': org})
@login_required
def org_analytics(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    if not is_admin_for_org(request.user, org):
        return redirect('dashboard')

    year = request.GET.get('year', datetime.datetime.now().year)
    try:
        year = int(year)
    except ValueError:
        year = datetime.datetime.now().year

    # Approved spent by category
    spending_by_cat_raw = CapitalRequest.objects.filter(
        organization=org, 
        status='APPROVED', 
        created_at__year=year
    ).values('category').annotate(total=Sum('amount')).order_by('-total')
    
    cat_map = dict(CapitalRequest.CATEGORY_CHOICES)
    spending_by_cat = [
        {'category': cat_map.get(item['category'], item['category']), 'total': item['total']}
        for item in spending_by_cat_raw
    ]

    # Income by source
    income_by_source_raw = CreditEvent.objects.filter(
        project__organization=org,
        created_at__year=year
    ).values('source').annotate(total=Sum('amount')).order_by('-total')
    
    src_map = dict(CreditEvent.SOURCE_CHOICES)
    income_by_source = [
        {'source': src_map.get(item['source'], item['source']), 'total': item['total']}
        for item in income_by_source_raw
    ]

    # Monthly trends
    monthly_spending = CapitalRequest.objects.filter(
        organization=org, status='APPROVED', created_at__year=year
    ).annotate(month=ExtractMonth('created_at')).values('month').annotate(total=Sum('amount')).order_by('month')

    # Available years for selection
    years = CapitalRequest.objects.filter(organization=org).annotate(year=ExtractYear('created_at')).values_list('year', flat=True).distinct()
    if not years:
        years = [year]

    context = {
        'org': org,
        'selected_year': year,
        'years': sorted(list(years), reverse=True),
        'spending_by_cat': spending_by_cat,
        'income_by_source': income_by_source,
        'monthly_spending': monthly_spending,
        'is_institution': False
    }
    return render(request, 'orgs/analytics.html', context)


@login_required
def institution_analytics(request):
    user = request.user
    institution = get_user_institution(user)
    
    if not is_any_superuser(user) or not institution:
        return redirect('dashboard')

    year = request.GET.get('year', datetime.datetime.now().year)
    try:
        year = int(year)
    except ValueError:
        year = datetime.datetime.now().year

    # Approved spent by category
    spending_by_cat_raw = CapitalRequest.objects.filter(
        organization__institution=institution, 
        status='APPROVED', 
        created_at__year=year
    ).values('category').annotate(total=Sum('amount')).order_by('-total')
    
    cat_map = dict(CapitalRequest.CATEGORY_CHOICES)
    spending_by_cat = [
        {'category': cat_map.get(item['category'], item['category']), 'total': item['total']}
        for item in spending_by_cat_raw
    ]

    # Income by source
    income_by_source_raw = CreditEvent.objects.filter(
        project__organization__institution=institution,
        created_at__year=year
    ).values('source').annotate(total=Sum('amount')).order_by('-total')
    
    src_map = dict(CreditEvent.SOURCE_CHOICES)
    income_by_source = [
        {'source': src_map.get(item['source'], item['source']), 'total': item['total']}
        for item in income_by_source_raw
    ]

    # Available years
    years = CapitalRequest.objects.filter(organization__institution=institution).annotate(year=ExtractYear('created_at')).values_list('year', flat=True).distinct()
    if not years:
        years = [year]

    context = {
        'institution': institution,
        'selected_year': year,
        'years': sorted(list(years), reverse=True),
        'spending_by_cat': spending_by_cat,
        'income_by_source': income_by_source,
        'is_institution': True
    }
    return render(request, 'orgs/analytics.html', context)


@login_required
def export_analytics_pdf(request):
    org_id = request.GET.get('org_id')
    year = int(request.GET.get('year', datetime.datetime.now().year))
    
    if org_id:
        org = get_object_or_404(Organization, id=org_id)
        if not is_admin_for_org(request.user, org):
             return HttpResponse("Unauthorized", status=403)
        title = f"Financial Report: {org.name} ({year})"
        spending = CapitalRequest.objects.filter(organization=org, status='APPROVED', created_at__year=year).values('category').annotate(total=Sum('amount'))
        income = CreditEvent.objects.filter(project__organization=org, created_at__year=year).values('source').annotate(total=Sum('amount'))
    else:
        institution = get_user_institution(request.user)
        if not is_any_superuser(request.user) or not institution:
             return HttpResponse("Unauthorized", status=403)
        title = f"Institution Financial Report: {institution.name} ({year})"
        spending = CapitalRequest.objects.filter(organization__institution=institution, status='APPROVED', created_at__year=year).values('category').annotate(total=Sum('amount'))
        income = CreditEvent.objects.filter(project__organization__institution=institution, created_at__year=year).values('source').annotate(total=Sum('amount'))

    # PDF Generation
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, title, ln=True, align='C')
    pdf.ln(10)

    # Spending Table
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(0, 10, "Expenditure by Category", ln=True)
    pdf.set_font("Arial", '', 10)
    pdf.cell(100, 8, "Category", 1)
    pdf.cell(40, 8, "Total Amount", 1, ln=True)
    
    total_spent = 0
    for s in spending:
        label = dict(CapitalRequest.CATEGORY_CHOICES).get(s['category'], 'Unknown')
        pdf.cell(100, 8, label, 1)
        pdf.cell(40, 8, f"${s['total']:,.2f}", 1, ln=True)
        total_spent += s['total']
    
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(100, 8, "TOTAL EXPENDITURE", 1)
    pdf.cell(40, 8, f"${total_spent:,.2f}", 1, ln=True)
    pdf.ln(10)

    # Income Table
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(0, 10, "Income by Source", ln=True)
    pdf.set_font("Arial", '', 10)
    pdf.cell(100, 8, "Source", 1)
    pdf.cell(40, 8, "Total Amount", 1, ln=True)
    
    total_income = 0
    for i in income:
        label = dict(CreditEvent.SOURCE_CHOICES).get(i['source'], 'Unknown')
        pdf.cell(100, 8, label, 1)
        pdf.cell(40, 8, f"${i['total']:,.2f}", 1, ln=True)
        total_income += i['total']
    
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(100, 8, "TOTAL INCOME", 1)
    pdf.cell(40, 8, f"${total_income:,.2f}", 1, ln=True)

    buffer = io.BytesIO()
    pdf_str = pdf.output(dest='S')
    if isinstance(pdf_str, str): # Handle older versions of fpdf2
        buffer.write(pdf_str.encode('latin1'))
    else:
        buffer.write(pdf_str)
        
    buffer.seek(0)
    response = HttpResponse(buffer, content_type='application/pdf')
    filename = slugify(title) + ".pdf"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# ── SEO: robots.txt & sitemap ──────────────────────────────────────────────────

def robots_txt(request):
    """Serve a robots.txt that allows all crawlers and points to the sitemap."""
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
        "Disallow: /accounts/",
        "Disallow: /dashboard/",
        "Disallow: /requests/",
        "Disallow: /org/",
        "Disallow: /superuser/",
        "",
        "Sitemap: https://ledger-pro.org/sitemap.xml",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")


def sitemap_xml(request):
    """Serve a minimal XML sitemap for the public landing page."""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://ledger-pro.org/</loc>
    <lastmod>2026-03-07</lastmod>
    <changefreq>monthly</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>https://ledger-pro.org/accounts/login/</loc>
    <lastmod>2026-03-07</lastmod>
    <changefreq>yearly</changefreq>
    <priority>0.5</priority>
  </url>
</urlset>"""
    return HttpResponse(xml, content_type="application/xml")
