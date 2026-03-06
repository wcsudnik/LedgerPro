from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver


class Institution(models.Model):
    """Represents a university or school - there is exactly one superuser per Institution."""
    name = models.CharField(max_length=200)
    superuser = models.OneToOneField(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='superuser_of', help_text="The single superuser account for this institution"
    )

    def __str__(self):
        return self.name

    def transfer_superuser(self, new_user):
        """Transfer the superuser role to another user account."""
        old_superuser = self.superuser
        # Revoke old superuser privileges
        if old_superuser:
            old_superuser.is_superuser = False
            old_superuser.is_staff = False
            old_superuser.save()
        # Grant new superuser privileges
        new_user.is_superuser = True
        new_user.is_staff = True
        new_user.save()
        self.superuser = new_user
        self.save()


class Organization(models.Model):
    institution = models.ForeignKey(
        Institution, on_delete=models.CASCADE, related_name='organizations',
        null=True, blank=True
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    officers = models.ManyToManyField(User, related_name='officer_of', blank=True)
    budget = models.DecimalField(
        max_digits=12, decimal_places=2, default=0.00,
        help_text="Starting budget manually inputted by Admins"
    )
    admins = models.ManyToManyField(
        User, related_name='org_admin_of', blank=True,
        help_text="Users with admin-level access to this org (set by the Institution Superuser)"
    )

    def __str__(self):
        return self.name


class Project(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='projects')
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    bulletin = models.TextField(blank=True, help_text="Project bulletin board")
    allocated_budget = models.DecimalField(
        max_digits=12, decimal_places=2, default=0.00,
        help_text="Capital allocated from the main Org budget to this project"
    )

    def __str__(self):
        return f"{self.name} ({self.organization.name})"


class CapitalRequest(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending Review'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    ]
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='requests')
    # Project is now REQUIRED - every capital request must belong to a project
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='requests')
    submitted_by = models.ForeignKey(User, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    purpose = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    admin_note = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Request {self.id} – {self.project.name} – ${self.amount}"


class AuditLog(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='audit_logs')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    action = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"[{self.timestamp}] {self.user} – {self.action}"
