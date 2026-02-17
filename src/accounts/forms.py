"""Forms for the accounts app."""

import re

from django import forms
from django.contrib.auth.forms import UserChangeForm, UserCreationForm

from assets.models import Department

from .models import CustomUser


class CustomUserCreationForm(UserCreationForm):
    class Meta:
        model = CustomUser
        fields = ("username", "email", "display_name", "phone_number")


class CustomUserChangeForm(UserChangeForm):
    class Meta:
        model = CustomUser
        fields = (
            "username",
            "email",
            "display_name",
            "phone_number",
            "first_name",
            "last_name",
        )


class ProfileEditForm(forms.ModelForm):
    """Form for editing user profile details."""

    class Meta:
        model = CustomUser
        fields = ("display_name", "email", "phone_number", "organisation")

    def clean_phone_number(self):
        phone = self.cleaned_data.get("phone_number", "").strip()
        if phone and not re.match(r"^[0-9\s\-\(\)\+]+$", phone):
            raise forms.ValidationError(
                "Phone number may only contain digits, spaces, hyphens, "
                "parentheses, and +."
            )
        return phone

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip().lower()
        if (
            CustomUser.objects.filter(email__iexact=email)
            .exclude(pk=self.instance.pk)
            .exists()
        ):
            raise forms.ValidationError(
                "This email address is already in use."
            )
        return email


class RegistrationForm(UserCreationForm):
    """Public registration form per S2.15.1."""

    email = forms.EmailField(required=True)
    display_name = forms.CharField(max_length=255, required=True)
    phone_number = forms.CharField(max_length=20, required=False)
    requested_department = forms.ModelChoiceField(
        queryset=Department.objects.filter(is_active=True),
        required=False,
        empty_label="Select a department...",
    )

    class Meta:
        model = CustomUser
        fields = (
            "email",
            "display_name",
            "phone_number",
            "requested_department",
        )

    def clean_phone_number(self):
        phone = self.cleaned_data.get("phone_number", "").strip()
        if phone and not re.match(r"^[0-9\s\-\(\)\+]+$", phone):
            raise forms.ValidationError(
                "Phone number may only contain digits, spaces, hyphens, "
                "parentheses, and +."
            )
        return phone

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip().lower()
        if CustomUser.objects.filter(email__iexact=email).exists():
            # S2.15.1-09: Don't reveal email exists, silently flag for view
            self._email_exists = True
        else:
            self._email_exists = False
        return email

    def _generate_username(self, email: str) -> str:
        """Generate username from email prefix with suffix."""
        base = email.split("@")[0].lower()
        # Clean to valid chars
        base = re.sub(r"[^a-z0-9_]", "", base) or "user"
        username = base
        counter = 2
        while CustomUser.objects.filter(username=username).exists():
            username = f"{base}{counter}"
            counter += 1
        return username

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self._generate_username(user.email)
        user.is_active = False
        user.email_verified = False
        if commit:
            user.save()
        return user
