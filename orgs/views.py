from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from .models import Institution, Organization, Project, CapitalRequest, AuditLog
from django.db import transaction
from decimal import Decimal, InvalidOperation
from django.utils.html import strip_tags, escape


def is_superuser(user):
    return user.is_superuser


def is_admin_for_org(user, org):
    """Check if user is a superuser, or explicitly an admin of the org."""
    return user.is_superuser or user in org.admins.all()


def is_officer_for_org(user, org):
    return user in org.officers.all()


def can_access_org(user, org):
    return is_admin_for_org(user, org) or is_officer_for_org(user, org)


@login_required
def dashboard(request):
    user = request.user
    if user.is_superuser:
        organizations = Organization.objects.all()
        role = 'superuser'
    elif Organization.objects.filter(admins=user).exists():
        organizations = Organization.objects.filter(admins=user)
        role = 'admin'
    else:
        organizations = user.officer_of.all()
        role = 'officer'

    return render(request, 'orgs/dashboard.html', {
        'organizations': organizations,
        'role': role,
        'is_admin': role in ('superuser', 'admin'),
    })


@login_required
def org_detail(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    if not can_access_org(request.user, org):
        return redirect('dashboard')

    is_admin = is_admin_for_org(request.user, org)
    projects = org.projects.all()
    req_qs = org.requests.all().order_by('-created_at')
    logs = org.audit_logs.all().order_by('-timestamp')

    return render(request, 'orgs/org_detail.html', {
        'org': org,
        'projects': projects,
        'requests': req_qs,
        'logs': logs,
        'is_admin': is_admin,
        'is_superuser': request.user.is_superuser,
    })


@login_required
def project_detail(request, org_id, project_id):
    org = get_object_or_404(Organization, id=org_id)
    if not can_access_org(request.user, org):
        return redirect('dashboard')

    project = get_object_or_404(Project, id=project_id, organization=org)
    req_qs = project.requests.all().order_by('-created_at')
    is_admin = is_admin_for_org(request.user, org)

    return render(request, 'orgs/project_detail.html', {
        'org': org,
        'project': project,
        'requests': req_qs,
        'is_admin': is_admin,
    })


@login_required
def all_requests(request):
    """Show capital requests scoped strictly to what the user has access to."""
    user = request.user
    if user.is_superuser:
        req_qs = CapitalRequest.objects.all().order_by('-created_at')
    elif Organization.objects.filter(admins=user).exists():
        # Admins only see requests from orgs they admin — not every org
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

        try:
            amount = Decimal(raw_amount)
            if amount <= 0:
                raise ValueError("Amount must be positive.")
        except (InvalidOperation, ValueError, TypeError):
            return render(request, 'orgs/submit_request.html', {
                'org': org, 'project': project,
                'error': "Please enter a valid positive dollar amount."
            })

        with transaction.atomic():
            req = CapitalRequest.objects.create(
                organization=org,
                project=project,
                submitted_by=request.user,
                amount=amount,
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

    return render(request, 'orgs/submit_request.html', {'org': org, 'project': project})


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
            if action == 'approve':
                # Security: Check for sufficient organization budget before approving
                if req.amount > req.organization.budget:
                    messages.error(request, f"Cannot approve: Request exceeds organization budget (Available: ${req.organization.budget:,.2f})")
                    return redirect('review_request', req_id=req_id)

                req.status = 'APPROVED'
                # Debit directly from the organization budget
                req.organization.budget -= req.amount
                req.organization.save()
            else:
                req.status = 'REJECTED'

            req.admin_note = escape(note) # Safeguard notes
            req.save()

            AuditLog.objects.create(
                organization=req.organization,
                user=request.user,
                action=f"{action.capitalize()}d capital request #{req.id} for ${req.amount:,.2f}. Note: {note or 'None'}"
            )

        messages.success(request, f"Request #{req.id} has been {req.status.lower()}.")
        return redirect('all_requests')

    return render(request, 'orgs/review_request.html', {'req': req})




# ── Superuser views ────────────────────────────────────────────────────────────

@login_required
def superuser_dashboard(request):
    if not request.user.is_superuser:
        return redirect('dashboard')

    try:
        institution = Institution.objects.get(superuser=request.user)
    except Institution.DoesNotExist:
        institution = None

    all_users = User.objects.all().order_by('username')
    all_orgs = Organization.objects.all()
    return render(request, 'orgs/superuser_dashboard.html', {
        'institution': institution,
        'all_users': all_users,
        'all_orgs': all_orgs,
    })


@login_required
def make_admin(request, user_id):
    """Superuser promotes a user to admin of an org."""
    if not request.user.is_superuser:
        return redirect('dashboard')

    if request.method == 'POST':
        target_user = get_object_or_404(User, id=user_id)
        org_id = request.POST.get('org_id')
        org = get_object_or_404(Organization, id=org_id)

        target_user.is_staff = True
        target_user.save()
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
    if not request.user.is_superuser:
        return redirect('dashboard')

    if request.method == 'POST':
        target_user = get_object_or_404(User, id=user_id)
        org_id = request.POST.get('org_id')
        org = get_object_or_404(Organization, id=org_id)

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
    if not request.user.is_superuser:
        return redirect('dashboard')

    institution = get_object_or_404(Institution, superuser=request.user)

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
