from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.http import JsonResponse
from django.urls import include, path
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework.routers import DefaultRouter

from tracker import views

router = DefaultRouter()
router.register("applications", views.ApplicationViewSet, basename="application")
router.register("companies", views.CompanyViewSet, basename="company")
router.register("interviews", views.InterviewViewSet, basename="interview")

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("closed/", views.closed, name="closed"),
    path("applications/new/", views.add_application, name="add"),
    path("applications/<int:pk>/", views.detail, name="detail"),
    path("applications/<int:pk>/move/", views.move, name="move"),
    path("applications/<int:pk>/interviews/", views.add_interview, name="add-interview"),
    path("import/", views.import_view, name="import"),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
    path("api/stats/", views.StatsView.as_view(), name="api-stats"),
    path("api/", include(router.urls)),
    path("api/token/", obtain_auth_token, name="api-token"),
    path("healthz", lambda request: JsonResponse({"status": "ok"}), name="healthz"),
]
