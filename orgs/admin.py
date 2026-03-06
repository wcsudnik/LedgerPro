from django.contrib import admin
from django.contrib.auth.models import User
from .models import Institution, Organization, Project, CapitalRequest, AuditLog


@admin.register(Institution)
class InstitutionAdmin(admin.ModelAdmin):
    list_display = ('name', 'superuser')
    search_fields = ('name',)


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ('name', 'institution', 'budget')
    search_fields = ('name',)
    filter_horizontal = ('officers', 'admins')


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'organization')
    list_filter = ('organization',)


@admin.register(CapitalRequest)
class CapitalRequestAdmin(admin.ModelAdmin):
    list_display = ('organization', 'project', 'amount', 'status', 'submitted_by', 'created_at')
    list_filter = ('status', 'organization')


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'organization', 'user', 'action')
    list_filter = ('organization', 'timestamp')
    readonly_fields = ('timestamp',)
