"""Authentication views for PROPS."""

import logging

from django_ratelimit.decorators import ratelimit

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    PasswordResetForm,
    SetPasswordForm,
)
from django.contrib.auth.tokens import default_token_generator
from django.core import signing
from django.db import models
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import urlsafe_base64_decode

from .email import send_branded_email
from .forms import ProfileEditForm, RegistrationForm
from .models import CustomUser

logger = logging.getLogger(__name__)


@ratelimit(key="ip", rate="5/m", method="POST", block=False)
def login_view(request):
    """Handle user login with account state detection (S2.15.3-06)."""
    if request.user.is_authenticated:
        return redirect("assets:dashboard")

    was_limited = getattr(request, "limited", False)
    if was_limited:
        messages.error(
            request, "Too many login attempts. Please try again shortly."
        )
        return render(
            request, "registration/login.html", {"form": AuthenticationForm()}
        )

    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)
        # Check account states before authentication
        username = request.POST.get("username", "").strip()
        if username:
            try:
                user = CustomUser.objects.get(
                    models.Q(username__iexact=username)
                    | models.Q(email__iexact=username)
                )
                if not user.is_active:
                    if user.rejection_reason:
                        # S2.15.5-03: Rejected
                        return render(
                            request,
                            "registration/account_state.html",
                            {"state": "rejected"},
                        )
                    elif user.email_verified:
                        # S2.15.3-04: Pending approval
                        return render(
                            request,
                            "registration/account_state.html",
                            {"state": "pending_approval"},
                        )
                    else:
                        # S2.15.3-05: Unverified
                        return render(
                            request,
                            "registration/account_state.html",
                            {
                                "state": "unverified",
                                "email": user.email,
                            },
                        )
            except CustomUser.DoesNotExist:
                pass
            except CustomUser.MultipleObjectsReturned:
                pass

        if form.is_valid():
            user = form.get_user()
            # Block borrower-only users from logging in
            if user.has_perm("assets.can_be_borrower") and not user.has_perm(
                "assets.can_checkout_asset"
            ):
                return render(
                    request,
                    "registration/account_state.html",
                    {"state": "borrower_no_access"},
                )
            login(request, user)
            next_url = request.GET.get("next", "assets:dashboard")
            return redirect(next_url)
    else:
        form = AuthenticationForm()

    return render(request, "registration/login.html", {"form": form})


def logout_view(request):
    """Handle user logout."""
    logout(request)
    messages.success(request, "You have been logged out.")
    return redirect("accounts:login")


@login_required
def profile_view(request):
    """Display user profile."""
    user = request.user
    recent_transactions = user.transactions.select_related(
        "asset", "from_location", "to_location"
    )[:10]
    borrowed_assets = user.borrowed_assets.select_related(
        "category", "current_location"
    )
    # V300: Department memberships for dept managers
    from assets.models import Department

    managed_departments = Department.objects.filter(managers=user)

    return render(
        request,
        "accounts/profile.html",
        {
            "profile_user": user,
            "recent_transactions": recent_transactions,
            "borrowed_assets": borrowed_assets,
            "managed_departments": managed_departments,
        },
    )


@ratelimit(key="ip", rate="5/h", method="POST", block=False)
def register_view(request):
    """Handle user registration (S2.15.1)."""
    if request.user.is_authenticated:
        return redirect("assets:dashboard")

    was_limited = getattr(request, "limited", False)
    if was_limited:
        messages.error(
            request, "Too many registration attempts. Please try again later."
        )
        return render(
            request, "registration/register.html", {"form": RegistrationForm()}
        )

    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            # S2.15.1-09: If email exists, show same confirmation
            if getattr(form, "_email_exists", False):
                return render(
                    request,
                    "registration/register_confirm.html",
                    {"email": form.cleaned_data["email"]},
                )
            user = form.save()
            try:
                _send_verification_email(user, request)
            except OSError:
                import logging

                logger = logging.getLogger(__name__)
                logger.error(
                    "Failed to send verification email to %s",
                    user.email,
                )
            return render(
                request,
                "registration/register_confirm.html",
                {"email": user.email},
            )
    else:
        form = RegistrationForm()

    return render(request, "registration/register.html", {"form": form})


def _send_verification_email(user, request):
    """Send email verification link (S2.15.2)."""
    signer = signing.TimestampSigner()
    token = signer.sign(str(user.pk))

    scheme = "https" if request.is_secure() else "http"
    domain = request.get_host()
    verify_url = f"{scheme}://{domain}/accounts/verify-email/{token}/"

    send_branded_email(
        template_name="verification",
        context={
            "display_name": user.get_display_name(),
            "verify_url": verify_url,
        },
        subject=f"{settings.SITE_NAME} - Verify your email address",
        recipient=user.email,
    )


def verify_email_view(request, token):
    """Handle email verification link (S2.15.2-05)."""
    signer = signing.TimestampSigner()
    try:
        # Max age 48 hours
        user_pk = signer.unsign(token, max_age=48 * 60 * 60)
    except signing.SignatureExpired:
        return render(
            request,
            "registration/verify_email.html",
            {"status": "expired"},
        )
    except signing.BadSignature:
        return render(
            request,
            "registration/verify_email.html",
            {"status": "invalid"},
        )

    try:
        user = CustomUser.objects.get(pk=user_pk)
    except CustomUser.DoesNotExist:
        return render(
            request,
            "registration/verify_email.html",
            {"status": "invalid"},
        )

    if user.email_verified:
        return render(
            request,
            "registration/verify_email.html",
            {"status": "already_verified"},
        )

    user.email_verified = True
    user.save(update_fields=["email_verified"])

    # Notify system admins (S2.15.2-11)
    _notify_admins_new_pending_user(user, request)

    return render(
        request,
        "registration/verify_email.html",
        {"status": "verified"},
    )


def _notify_admins_new_pending_user(user, request):
    """Notify system admins about a new user pending approval (S2.15.2-11)."""
    # Find users with can_approve_users permission (covers renamed groups)
    admin_emails = list(
        CustomUser.objects.filter(
            is_active=True,
            email__isnull=False,
        )
        .filter(
            models.Q(is_superuser=True)
            | models.Q(groups__permissions__codename="can_approve_users")
        )
        .exclude(email="")
        .distinct()
        .values_list("email", flat=True)
    )

    if not admin_emails:
        return

    dept_name = (
        user.requested_department.name if user.requested_department else "None"
    )
    scheme = "https" if request.is_secure() else "http"
    domain = request.get_host()
    approval_url = f"{scheme}://{domain}/accounts/approval-queue/"

    send_branded_email(
        template_name="admin_new_pending",
        context={
            "display_name": user.get_display_name(),
            "user_email": user.email,
            "department_name": dept_name,
            "approval_url": approval_url,
        },
        subject=f"{settings.SITE_NAME} - New user pending approval",
        recipient=admin_emails,
    )


@ratelimit(key="post:email", rate="3/h", method="POST", block=False)
def resend_verification_view(request):
    """Resend verification email (S2.15.2-07)."""
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        was_limited = getattr(request, "limited", False)
        if not was_limited and email:
            try:
                user = CustomUser.objects.get(
                    email__iexact=email, email_verified=False
                )
                _send_verification_email(user, request)
            except CustomUser.DoesNotExist:
                pass  # S2.15.2-07: Don't reveal if email exists
        # Always show same message
        return render(
            request,
            "registration/resend_verification.html",
            {"sent": True},
        )
    return render(
        request,
        "registration/resend_verification.html",
        {"sent": False},
    )


def _can_approve_users(user):
    """Check if a user can approve/reject registrations.

    Returns True for system admins or users with the explicit
    ``can_approve_users`` permission (V422 / S2.15.6-05).
    """
    from assets.services.permissions import get_user_role

    if get_user_role(user) == "system_admin":
        return True
    return user.has_perm("accounts.can_approve_users")


@login_required
def approval_queue_view(request):
    """Display pending user approvals (S2.15.4-01). System Admins only."""
    if not _can_approve_users(request.user):
        return HttpResponseForbidden("Permission denied")

    tab = request.GET.get("tab", "pending")

    if tab == "history":
        users = (
            CustomUser.objects.filter(
                models.Q(is_active=True, approved_by__isnull=False)
                | models.Q(rejection_reason__gt="")
            )
            .select_related("requested_department", "approved_by")
            .order_by("-approved_at")
        )
    else:
        users = (
            CustomUser.objects.filter(
                is_active=False,
                email_verified=True,
                rejection_reason="",
            )
            .select_related("requested_department")
            .order_by("date_joined")
        )

    from django.contrib.auth.models import Group
    from django.core.paginator import Paginator

    groups = Group.objects.exclude(
        permissions__codename="can_approve_users"
    ).order_by("name")
    from assets.models import Department

    departments = Department.objects.filter(is_active=True).order_by("name")

    paginator = Paginator(users, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "accounts/approval_queue.html",
        {
            "users": page_obj,
            "page_obj": page_obj,
            "paginator": paginator,
            "is_paginated": page_obj.has_other_pages(),
            "tab": tab,
            "groups": groups,
            "departments": departments,
        },
    )


@login_required
def approve_user_view(request, user_pk):
    """Approve a pending user (S2.15.4-05). System Admins only."""
    if not _can_approve_users(request.user):
        return HttpResponseForbidden("Permission denied")

    if request.method != "POST":
        return redirect("accounts:approval_queue")

    pending_user = CustomUser.objects.get(pk=user_pk)

    from django.contrib.auth.models import Group

    from assets.models import Department

    # S7.13-07: Guard against concurrent approval
    if pending_user.is_active:
        messages.info(
            request,
            f"{pending_user.get_display_name()} is already approved.",
        )
        return redirect("accounts:approval_queue")

    # Get form data
    group_name = request.POST.get("role", "Member")
    dept_ids = request.POST.getlist("departments")

    # S7.13-06: Re-validate department is_active server-side
    # Check if the selected group is a dept manager role
    # (has can_merge_assets but NOT can_approve_users)
    _selected_group = Group.objects.filter(name=group_name).first()
    _is_dept_manager_role = bool(
        _selected_group
        and _selected_group.permissions.filter(
            codename="can_merge_assets"
        ).exists()
        and not _selected_group.permissions.filter(
            codename="can_approve_users"
        ).exists()
    )
    if _is_dept_manager_role and dept_ids:
        inactive_depts = Department.objects.filter(
            pk__in=dept_ids, is_active=False
        )
        if inactive_depts.exists():
            inactive_names = ", ".join(
                inactive_depts.values_list("name", flat=True)
            )
            messages.error(
                request,
                f"Cannot assign inactive department(s): " f"{inactive_names}.",
            )
            dept_ids = list(
                Department.objects.filter(
                    pk__in=dept_ids, is_active=True
                ).values_list("pk", flat=True)
            )

    # 1. Activate user
    pending_user.is_active = True
    pending_user.approved_by = request.user
    pending_user.approved_at = timezone.now()
    pending_user.rejection_reason = ""  # Clear if reversing a rejection
    pending_user.save(
        update_fields=[
            "is_active",
            "approved_by",
            "approved_at",
            "rejection_reason",
        ]
    )

    # 2. Add to group
    group = None
    try:
        group = Group.objects.get(name=group_name)
        pending_user.groups.clear()
        pending_user.groups.add(group)
    except Group.DoesNotExist:
        logger.error(
            "Approval role '%s' not found for user %s",
            group_name,
            pending_user.pk,
        )
        messages.error(
            request,
            f"Role '{group_name}' does not exist. "
            f"User was activated but no role was assigned.",
        )

    # 3. If the assigned group is dept manager role, add to M2M
    if (
        group
        and group.permissions.filter(codename="can_merge_assets").exists()
        and not group.permissions.filter(codename="can_approve_users").exists()
        and dept_ids
    ):
        for dept_id in dept_ids:
            try:
                dept = Department.objects.get(pk=dept_id, is_active=True)
                dept.managers.add(pending_user)
            except Department.DoesNotExist:
                pass

    # 4. Send approval email (S2.15.4-08)
    dept_names = (
        list(
            Department.objects.filter(pk__in=dept_ids).values_list(
                "name", flat=True
            )
        )
        if dept_ids
        else []
    )
    try:
        _send_approval_email(pending_user, group_name, dept_names)
    except Exception:
        logger.exception(
            "Failed to send approval email to %s",
            pending_user.email,
        )
        messages.warning(
            request,
            f"{pending_user.get_display_name()} has been approved"
            f" as {group_name}, but the notification email"
            f" could not be sent.",
        )
        return redirect("accounts:approval_queue")

    messages.success(
        request,
        f"{pending_user.get_display_name()} has been approved"
        f" as {group_name}.",
    )
    return redirect("accounts:approval_queue")


@login_required
def reject_user_view(request, user_pk):
    """Reject a pending user (S2.15.5-01). System Admins only."""
    if not _can_approve_users(request.user):
        return HttpResponseForbidden("Permission denied")

    if request.method != "POST":
        return redirect("accounts:approval_queue")

    pending_user = CustomUser.objects.get(pk=user_pk)
    reason = request.POST.get("rejection_reason", "").strip()
    if not reason:
        messages.error(request, "A rejection reason is required.")
        return redirect("accounts:approval_queue")

    pending_user.rejection_reason = reason
    pending_user.approved_by = request.user
    pending_user.approved_at = timezone.now()
    pending_user.is_active = False
    pending_user.save(
        update_fields=[
            "rejection_reason",
            "approved_by",
            "approved_at",
            "is_active",
        ]
    )

    # Send rejection email (S2.15.5-02)
    try:
        _send_rejection_email(pending_user)
    except Exception:
        logger.exception(
            "Failed to send rejection email to %s",
            pending_user.email,
        )
        messages.warning(
            request,
            f"{pending_user.get_display_name()}'s registration"
            f" has been rejected, but the notification email"
            f" could not be sent.",
        )
        return redirect("accounts:approval_queue")

    messages.success(
        request,
        f"{pending_user.get_display_name()}'s registration"
        f" has been rejected.",
    )
    return redirect("accounts:approval_queue")


def _send_approval_email(user, role_name, dept_names):
    """Send approval notification to user (S2.15.4-08)."""
    send_branded_email(
        template_name="account_approved",
        context={
            "display_name": user.get_display_name(),
            "role_name": role_name,
            "dept_names": ", ".join(dept_names) if dept_names else "",
        },
        subject=f"{settings.SITE_NAME} - Your account has been approved",
        recipient=user.email,
    )


def _send_rejection_email(user):
    """Send rejection notification to user (S2.15.5-02)."""
    send_branded_email(
        template_name="account_rejected",
        context={
            "display_name": user.get_display_name(),
        },
        subject=f"{settings.SITE_NAME} - Your account registration",
        recipient=user.email,
    )


@login_required
def profile_edit_view(request):
    """Edit user profile details."""
    user = request.user
    original_email = user.email

    if request.method == "POST":
        form = ProfileEditForm(request.POST, instance=user)
        if form.is_valid():
            new_email = form.cleaned_data["email"]
            email_changed = new_email.lower() != original_email.lower()

            form.save()

            if email_changed:
                user.email_verified = False
                user.is_active = False
                user.save(update_fields=["email_verified", "is_active"])
                _send_verification_email(user, request)
                logout(request)
                messages.info(
                    request,
                    "Your email has been changed. Please verify your new "
                    "email address before logging in again.",
                )
                return redirect("accounts:login")

            messages.success(request, "Profile updated successfully.")
            return redirect("accounts:profile")
    else:
        form = ProfileEditForm(instance=user)

    return render(request, "accounts/profile_edit.html", {"form": form})


@login_required
def password_change_view(request):
    """Change current user's password."""
    if request.method == "POST":
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            messages.success(request, "Your password has been changed.")
            return redirect("accounts:profile")
    else:
        form = PasswordChangeForm(request.user)

    return render(request, "accounts/password_change.html", {"form": form})


@ratelimit(key="post:email", rate="3/h", method="POST", block=False)
def password_reset_view(request):
    """Request a password reset email."""
    was_limited = getattr(request, "limited", False)

    if request.method == "POST":
        form = PasswordResetForm(request.POST)
        if form.is_valid():
            if not was_limited:
                form.save(
                    request=request,
                    use_https=request.is_secure(),
                    token_generator=default_token_generator,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    email_template_name=("accounts/password_reset_email.txt"),
                    html_email_template_name=(
                        "accounts/password_reset_email.html"
                    ),
                )
            # Always redirect so user can't tell if rate-limited
            return redirect("accounts:password_reset_done")
    else:
        form = PasswordResetForm()

    return render(request, "accounts/password_reset.html", {"form": form})


def password_reset_done_view(request):
    """Shown after password reset email has been sent."""
    return render(request, "accounts/password_reset_done.html")


def password_reset_confirm_view(request, uidb64, token):
    """Set new password after clicking reset link."""
    try:
        uid = urlsafe_base64_decode(uidb64).decode()
        user = CustomUser.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, CustomUser.DoesNotExist):
        user = None

    if user is not None and default_token_generator.check_token(user, token):
        if request.method == "POST":
            form = SetPasswordForm(user, request.POST)
            if form.is_valid():
                form.save()
                messages.success(
                    request,
                    "Your password has been reset. You can now log in.",
                )
                return redirect("accounts:password_reset_complete")
        else:
            form = SetPasswordForm(user)
        return render(
            request,
            "accounts/password_reset_confirm.html",
            {"form": form, "validlink": True},
        )
    else:
        return render(
            request,
            "accounts/password_reset_confirm.html",
            {"validlink": False},
        )


def password_reset_complete_view(request):
    """Shown after password has been successfully reset."""
    return render(request, "accounts/password_reset_complete.html")
