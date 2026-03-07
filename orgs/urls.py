from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),

    # Capital requests
    path('requests/', views.all_requests, name='all_requests'),
    path('requests/<int:req_id>/review/', views.review_request, name='review_request'),

    # Org pages
    path('org/<int:org_id>/', views.org_detail, name='org_detail'),
    path('org/<int:org_id>/new-project/', views.create_project, name='create_project'),

    # Project pages
    path('org/<int:org_id>/project/<int:project_id>/', views.project_detail, name='project_detail'),
    path('org/<int:org_id>/project/<int:project_id>/request/', views.submit_request, name='submit_request_project'),
    path('org/<int:org_id>/project/<int:project_id>/allocate/', views.allocate_funds, name='allocate_funds'),
    path('org/<int:org_id>/project/<int:project_id>/record-credit/', views.record_credit, name='record_credit'),

    # Superuser management
    path('superuser/', views.superuser_dashboard, name='superuser_dashboard'),
    path('superuser/create-org/', views.create_org_frontend, name='create_org_frontend'),
    path('superuser/create-user/', views.create_user_frontend, name='create_user_frontend'),
    path('superuser/make-admin/<int:user_id>/', views.make_admin, name='make_admin'),
    path('superuser/revoke-admin/<int:user_id>/', views.revoke_admin, name='revoke_admin'),
    path('superuser/transfer/', views.transfer_superuser, name='transfer_superuser'),
    
    # Analytics
    path('org/<int:org_id>/analytics/', views.org_analytics, name='org_analytics'),
    path('institution/analytics/', views.institution_analytics, name='institution_analytics'),
    path('analytics/export-pdf/', views.export_analytics_pdf, name='export_analytics_pdf'),
]
