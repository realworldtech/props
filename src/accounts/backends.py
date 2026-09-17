"""Custom authentication backend for PROPS."""

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

User = get_user_model()


class EmailOrUsernameBackend(ModelBackend):
    """Allow login with either email address or username."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None or password is None:
            return None

        if "@" in username:
            users = User.objects.filter(email__iexact=username)
            if users.count() != 1:
                return None
            user = users.first()
        else:
            user = self._get_by_username(username)
            if user is None:
                return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None

    @staticmethod
    def _get_by_username(username):
        """Exact match first; otherwise a unique case-insensitive match.

        Usernames are generated in lowercase, but mobile keyboards
        capitalise the first letter of a text field.
        """
        user = User.objects.filter(username=username).first()
        if user is not None:
            return user
        users = list(User.objects.filter(username__iexact=username)[:2])
        return users[0] if len(users) == 1 else None
