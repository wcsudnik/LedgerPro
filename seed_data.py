from django.contrib.auth.models import User
from orgs.models import Organization, Project

# Create Admin User
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@example.com', 'admin')
    print("Created superuser: admin / admin")

# Create Officer
if not User.objects.filter(username='officer1').exists():
    officer = User.objects.create_user('officer1', 'officer@example.com', 'password123')
    print("Created officer user: officer1 / password123")
else:
    officer = User.objects.get(username='officer1')

# Create Org
org, created = Organization.objects.get_or_create(
    name="Debate Club",
    defaults={
        "description": "The official university debate team. We travel nationally.",
        "budget": 5000.00
    }
)
if created:
    org.officers.add(officer)
    print("Created 'Debate Club' organization and assigned 'officer1'.")

# Create Project
project, created = Project.objects.get_or_create(
    organization=org,
    name="National Tournament Trip",
    defaults={
        "description": "Funding for hotel and flight for the Chicago debate tournament.",
        "bulletin": "The hotel block has been booked. We just need to pay the remaining balance."
    }
)
if created:
    print("Created 'National Tournament Trip' project.")
