from django.contrib.auth.models import User
from .models import Institution, Organization

def get_user_institution(user):
    """
    Returns the Institution associated with the user.
    1. If the user is an Institution superuser, return that institution.
    2. If the user is an admin/officer of an organization, return that institution.
    3. Otherwise return None.
    """
    if user.is_anonymous:
        return None
    
    # Check if they are an institution superuser
    try:
        if hasattr(user, 'superuser_of'):
            return user.superuser_of
    except Exception:
        pass

    # Check if they are an admin or officer of any organization
    # Assuming one user belongs to one institution for simplicity in this context
    org = Organization.objects.filter(admins=user).first() or Organization.objects.filter(officers=user).first()
    if org:
        return org.institution

    return None

def is_platform_admin(user):
    """
    A platform admin is a superuser who is NOT assigned as an institution superuser.
    """
    return user.is_superuser and not hasattr(user, 'superuser_of')

def is_any_superuser(user):
    """Check if the user is a platform admin or an institution superuser."""
    if is_platform_admin(user):
        return True
    institution = get_user_institution(user)
    if institution and user == institution.superuser:
        return True
    return False

def is_admin_for_org(user, org):
    """Check if user is a platform admin, an institution superuser, or an org admin."""
    if is_platform_admin(user):
        return True
    
    institution = get_user_institution(user)
    if institution and user == institution.superuser and org.institution == institution:
        return True
        
    return user in org.admins.all()

def is_officer_for_org(user, org):
    """Check if user is an officer of the org."""
    return user in org.officers.all()

def can_access_org(user, org):
    """Check if user has any level of access to the org."""
    return is_admin_for_org(user, org) or is_officer_for_org(user, org)
