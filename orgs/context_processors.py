from .utils import is_platform_admin, get_user_institution, is_any_superuser

def platform_context(request):
    if request.user.is_anonymous:
        return {
            'is_platform_admin': False,
            'user_institution': None,
            'is_any_superuser': False,
        }
    
    return {
        'is_platform_admin': is_platform_admin(request.user),
        'user_institution': get_user_institution(request.user),
        'is_any_superuser': is_any_superuser(request.user),
    }
