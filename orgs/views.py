from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from .models import Institution, Organization, Project, CapitalRequest, AuditLog, BulletinPost
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


def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def log_action(request, organization, action, details=None):
    AuditLog.objects.create(
        organization=organization,
        user=request.user if request.user.is_authenticated else None,
        action=action,
        ip_address=get_client_ip(request),
        details=details
    )


@login_required
def dashboard(request):
    user = request.user
    if user.is_superuser:
        organizations = Organization.objects.all().prefetch_related('projects', 'audit_logs')
        role = 'superuser'
    elif Organization.objects.filter(admins=user).exists():
        organizations = Organization.objects.filter(admins=user).prefetch_related('projects', 'audit_logs')
        role = 'admin'
    else:
        organizations = user.officer_of.all().prefetch_related('projects', 'audit_logs')
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
    bulletins = project.bulletin_posts.all().order_by('-created_at')
    is_admin = is_admin_for_org(request.user, org)

    return render(request, 'orgs/project_detail.html', {
        'org': org,
        'project': project,
        'requests': req_qs,
        'bulletins': bulletins,
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
            log_action(
                request, org,
                action=f"Submitted capital request #{req.id} for ${amount:,.2f} under project '{project.name}'.",
                details={'amount': str(amount), 'project_id': project.id, 'request_id': req.id}
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
            # Security: Fetch org with select_for_update to handle concurrent approvals safely
            org = Organization.objects.select_for_update().get(id=req.organization.id)
            
            if action == 'approve':
                # Security: Check for sufficient organization budget before approving
                if req.amount > org.budget:
                    messages.error(request, f"Cannot approve: Request exceeds organization budget (Available: ${org.budget:,.2f})")
                    return redirect('review_request', req_id=req_id)

                req.status = 'APPROVED'
                # Debit directly from the organization budget
                org.budget -= req.amount
                org.save()
            else:
                req.status = 'REJECTED'

            req.admin_note = escape(note) # Safeguard notes
            req.save()

            log_action(
                request, org,
                action=f"{action.capitalize()}d capital request #{req.id} for ${req.amount:,.2f}. Note: {note or 'None'}",
                details={'action': action, 'amount': str(req.amount), 'request_id': req.id, 'note': note}
            )

        messages.success(request, f"Request #{req.id} has been {req.status.lower()}.")
        return redirect('all_requests')

    return render(request, 'orgs/review_request.html', {'req': req})




@login_required
def create_org(request):
    """Superuser only: Create a new organization."""
    if not request.user.is_superuser:
        return redirect('dashboard')
    
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        budget_raw = request.POST.get('budget', '0')
        
        try:
            budget = Decimal(budget_raw)
        except (InvalidOperation, ValueError):
            budget = Decimal('0.00')

        org = Organization.objects.create(
            name=strip_tags(name),
            description=strip_tags(description),
            budget=budget
        )
        log_action(
            request, org,
            action=f"Created organization '{org.name}' with initial budget ${budget:,.2f}.",
            details={'name': org.name, 'budget': str(budget)}
        )
        messages.success(request, f"Organization '{org.name}' created.")
        return redirect('dashboard')
    
    return render(request, 'orgs/create_org.html')


@login_required
def edit_org(request, org_id):
    """Admins can edit description; Superusers can also edit name and budget."""
    org = get_object_or_404(Organization, id=org_id)
    if not is_admin_for_org(request.user, org):
        return redirect('dashboard')

    if request.method == 'POST':
        org.description = strip_tags(request.POST.get('description', '').strip())
        
        # Identity-sensitive fields restricted to Superusers
        if request.user.is_superuser:
            org.name = strip_tags(request.POST.get('name', '').strip())
            try:
                org.budget = Decimal(request.POST.get('budget', '0'))
            except (InvalidOperation, ValueError):
                pass
        
        org.save()
        log_action(
            request, org,
            action=f"Updated organization settings.",
            details={'description': org.description, 'name': org.name, 'budget': str(org.budget)}
        )
        messages.success(request, "Organization settings updated.")
        return redirect('org_detail', org_id=org.id)

    return render(request, 'orgs/edit_org.html', {'org': org})


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

        log_action(
            request, org,
            action=f"Granted admin rights to '{target_user.username}' for organization '{org.name}'.",
            details={'user_id': target_user.id, 'username': target_user.username}
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

        log_action(
            request, org,
            action=f"Revoked admin rights from '{target_user.username}' for organization '{org.name}'.",
            details={'user_id': target_user.id, 'username': target_user.username}
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
            log_action(
                request, org,
                action=f"Created project '{project.name}'.",
                details={'project_id': project.id, 'name': project.name}
            )

        messages.success(request, f"Project '{project.name}' created successfully.")
        return redirect('project_detail', org_id=org.id, project_id=project.id)

    return render(request, 'orgs/create_project.html', {'org': org})
@login_required
def post_bulletin(request, org_id, project_id):
    org = get_object_or_404(Organization, id=org_id)
    if not can_access_org(request.user, org):
        return redirect('dashboard')

    project = get_object_or_404(Project, id=project_id, organization=org)

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        content = request.POST.get('content', '').strip()

        if content:
            with transaction.atomic():
                BulletinPost.objects.create(
                    project=project,
                    author=request.user,
                    title=title,
                    content=content
                )
                log_action(
                    request, org,
                    action=f"Posted to '{project.name}' bulletin.",
                    details={'project_id': project.id, 'title': title}
                )
            messages.success(request, "Bulletin posted.")
        else:
            messages.error(request, "Bulletin content cannot be empty.")

    return redirect('project_detail', org_id=org.id, project_id=project.id)


@login_required
def audit_log(request, org_id=None):
    """View detailed audit logs. Superusers see all, Admins see theirs."""
    user = request.user

    if org_id:
        org = get_object_or_404(Organization, id=org_id)
        if not is_admin_for_org(user, org):
            return redirect('dashboard')
        logs = AuditLog.objects.filter(organization=org).order_by('-timestamp')
        title = f"Audit Log – {org.name}"
    else:
        if not user.is_superuser:
            # If not superuser, maybe they can see logs for all orgs they admin?
            orgs = Organization.objects.filter(admins=user)
            logs = AuditLog.objects.filter(organization__in=orgs).order_by('-timestamp')
            title = "My Organizations' Audit Logs"
        else:
            logs = AuditLog.objects.all().order_by('-timestamp')
            title = "Global Institution Audit Log"

    return render(request, 'orgs/audit_log.html', {
        'logs': logs,
        'page_title': title,
        'is_superuser': user.is_superuser
    })
