from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),

    # Capital requests
    path('requests/', views.all_requests, name='all_requests'),
    path('requests/<int:req_id>/review/', views.review_request, name='review_request'),

    # Org pages
    path('new-org/', views.create_org, name='create_org'),
    path('org/<int:org_id>/', views.org_detail, name='org_detail'),
    path('org/<int:org_id>/edit/', views.edit_org, name='edit_org'),
    path('org/<int:org_id>/new-project/', views.create_project, name='create_project'),

    # Project pages
    path('org/<int:org_id>/project/<int:project_id>/', views.project_detail, name='project_detail'),
    path('org/<int:org_id>/project/<int:project_id>/request/', views.submit_request, name='submit_request_project'),

    # Superuser management
    path('superuser/', views.superuser_dashboard, name='superuser_dashboard'),
    path('superuser/make-admin/<int:user_id>/', views.make_admin, name='make_admin'),
    path('superuser/revoke-admin/<int:user_id>/', views.revoke_admin, name='revoke_admin'),
    path('superuser/transfer/', views.transfer_superuser, name='transfer_superuser'),
]
