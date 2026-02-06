"""URL configuration for accounts app."""

from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("profile/", views.profile_view, name="profile"),
    path("register/", views.register_view, name="register"),
    path(
        "verify-email/<str:token>/",
        views.verify_email_view,
        name="verify_email",
    ),
    path(
        "resend-verification/",
        views.resend_verification_view,
        name="resend_verification",
    ),
    path(
        "approval-queue/",
        views.approval_queue_view,
        name="approval_queue",
    ),
    path(
        "approve/<int:user_pk>/",
        views.approve_user_view,
        name="approve_user",
    ),
    path(
        "reject/<int:user_pk>/",
        views.reject_user_view,
        name="reject_user",
    ),
]
