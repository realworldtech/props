"""URL configuration for accounts app."""

from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("profile/", views.profile_view, name="profile"),
    path("profile/edit/", views.profile_edit_view, name="profile_edit"),
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
    # Password management
    path(
        "password/change/",
        views.password_change_view,
        name="password_change",
    ),
    path(
        "password/reset/",
        views.password_reset_view,
        name="password_reset",
    ),
    path(
        "password/reset/done/",
        views.password_reset_done_view,
        name="password_reset_done",
    ),
    path(
        "password/reset/<uidb64>/<token>/",
        views.password_reset_confirm_view,
        name="password_reset_confirm",
    ),
    path(
        "password/reset/complete/",
        views.password_reset_complete_view,
        name="password_reset_complete",
    ),
]
