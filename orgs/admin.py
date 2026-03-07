from django.contrib import admin
from django.contrib.auth.models import User, Permission
from django.contrib.auth.admin import UserAdmin as DefaultUserAdmin
from django.db.models import Q
from .models import Institution, Organization, Project, CapitalRequest, AuditLog
from .utils import get_user_institution, is_platform_admin


# Unregister default User admin to customize it for multi-tenancy
admin.site.unregister(User)

@admin.register(User)
class UserAdmin(DefaultUserAdmin):
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if is_platform_admin(request.user):
            return qs
        
        institution = get_user_institution(request.user)
        if institution:
            # Users belonging to this institution
            q_ours = Q(officer_of__institution=institution) | Q(org_admin_of__institution=institution) | Q(superuser_of=institution)
            
            # Users belonging to OTHER institutions
            # We check if they have any association with an institution that is NOT ours
            q_others = (Q(officer_of__institution__isnull=False) & ~Q(officer_of__institution=institution)) | \
                       (Q(org_admin_of__institution__isnull=False) & ~Q(org_admin_of__institution=institution)) | \
                       (Q(superuser_of__isnull=False) & ~Q(superuser_of=institution))
            
            # Show our users + users not assigned anywhere else
            # (Note: distinct is important here)
            return qs.filter(q_ours | ~q_others).distinct()
        return qs.none()


class BaseMultiTenantAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if is_platform_admin(request.user):
            return qs
        
        institution = get_user_institution(request.user)
        if institution:
            if self.model == Institution:
                return qs.filter(id=institution.id)
            if hasattr(self.model, 'institution'):
                return qs.filter(institution=institution)
            elif self.model == Project:
                return qs.filter(organization__institution=institution)
            elif self.model == CapitalRequest:
                return qs.filter(organization__institution=institution)
            elif self.model == AuditLog:
                return qs.filter(organization__institution=institution)
        
        return qs.none()

    def save_model(self, request, obj, form, change):
        if not is_platform_admin(request.user):
            institution = get_user_institution(request.user)
            if institution:
                if hasattr(obj, 'institution'):
                    obj.institution = institution
        super().save_model(request, obj, form, change)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not is_platform_admin(request.user):
            institution = get_user_institution(request.user)
            if institution:
                if db_field.name == "institution":
                    kwargs["queryset"] = Institution.objects.filter(id=institution.id)
                elif db_field.name == "organization":
                    kwargs["queryset"] = Organization.objects.filter(institution=institution)
                elif db_field.name == "project":
                    kwargs["queryset"] = Project.objects.filter(organization__institution=institution)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if not is_platform_admin(request.user):
            institution = get_user_institution(request.user)
            if institution:
                if db_field.name in ["officers", "admins"]:
                    q_ours = Q(officer_of__institution=institution) | Q(org_admin_of__institution=institution) | Q(superuser_of=institution)
                    q_others = (Q(officer_of__institution__isnull=False) & ~Q(officer_of__institution=institution)) | \
                               (Q(org_admin_of__institution__isnull=False) & ~Q(org_admin_of__institution=institution)) | \
                               (Q(superuser_of__isnull=False) & ~Q(superuser_of=institution))
                    
                    kwargs["queryset"] = User.objects.filter(q_ours | ~q_others).distinct()
        return super().formfield_for_manytomany(db_field, request, **kwargs)


@admin.register(Institution)
class InstitutionAdmin(BaseMultiTenantAdmin):
    list_display = ('name', 'superuser')
    search_fields = ('name',)

    def has_add_permission(self, request):
        return is_platform_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        
        # If a superuser is assigned, make sure they have localized permissions
        # but NOT is_staff access (to block admin panel access).
        if obj.superuser:
            user = obj.superuser
            # user.is_staff = False # Ensure this is False if we want total lockout
            user.save()
            
            # Grant all permissions for the 'orgs' app EXCEPT for the Institution model
            app_permissions = Permission.objects.filter(
                content_type__app_label='orgs'
            ).exclude(
                content_type__model='institution'
            )
            user.user_permissions.add(*app_permissions)
            
            # Also grant User management permissions so they can add/remove users
            # (In a real app, you might want more granular control over which users)
            user_permissions = Permission.objects.filter(
                content_type__app_label='auth',
                content_type__model='user'
            )
            user.user_permissions.add(*user_permissions)


@admin.register(Organization)
class OrganizationAdmin(BaseMultiTenantAdmin):
    list_display = ('name', 'institution', 'budget')
    search_fields = ('name',)
    filter_horizontal = ('officers', 'admins')


@admin.register(Project)
class ProjectAdmin(BaseMultiTenantAdmin):
    list_display = ('name', 'organization', 'allocated_budget')
    list_filter = ('organization',)


@admin.register(CapitalRequest)
class CapitalRequestAdmin(BaseMultiTenantAdmin):
    list_display = ('organization', 'project', 'amount', 'status', 'submitted_by', 'created_at')
    list_filter = ('status', 'organization')


@admin.register(AuditLog)
class AuditLogAdmin(BaseMultiTenantAdmin):
    list_display = ('timestamp', 'organization', 'user', 'action')
    list_filter = ('organization', 'timestamp')
    readonly_fields = ('timestamp',)
