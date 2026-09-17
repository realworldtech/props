"""Tests for props infrastructure — settings, Docker, deployment."""

from unittest.mock import MagicMock, patch

import pytest

from django.conf import settings
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import reverse

from props.colors import (
    auto_derive_accent,
    auto_derive_secondary,
    generate_brand_css_properties,
    generate_dark_palette,
    generate_oklch_palette,
    hex_to_oklch,
)


class TestInfrastructureSettings:
    """V572-V578: Infrastructure settings verification."""

    def test_celery_broker_configured(self):
        """Celery broker URL is configured."""
        from django.conf import settings

        assert hasattr(settings, "CELERY_BROKER_URL")
        assert settings.CELERY_BROKER_URL

    def test_cache_backend_configured(self):
        """Cache backend is configured."""
        from django.conf import settings

        assert "default" in settings.CACHES

    def test_auth_user_model_is_custom(self):
        """AUTH_USER_MODEL points to accounts.CustomUser."""
        from django.conf import settings

        assert settings.AUTH_USER_MODEL == "accounts.CustomUser"

    def test_authentication_backend_includes_custom(self):
        """Custom email/username backend is in AUTHENTICATION_BACKENDS."""
        from django.conf import settings

        assert any(
            "EmailOrUsername" in b for b in settings.AUTHENTICATION_BACKENDS
        )

    def test_gravatar_settings(self):
        """Gravatar settings are configured for avatar display."""
        from django.conf import settings

        assert hasattr(settings, "GRAVATAR_DEFAULT_IMAGE")
        assert settings.GRAVATAR_DEFAULT_IMAGE == "mp"
        assert settings.GRAVATAR_DEFAULT_SECURE is True

    def test_htmx_middleware_present(self):
        """django-htmx middleware is configured."""
        from django.conf import settings

        assert any("htmx" in m.lower() for m in settings.MIDDLEWARE)

    def test_context_processors_configured(self):
        """Custom context processors are in template settings."""
        from django.conf import settings

        processors = settings.TEMPLATES[0]["OPTIONS"]["context_processors"]
        assert "props.context_processors.site_settings" in processors
        assert "props.context_processors.user_role" in processors


# ============================================================
# BATCH 6: ZERO-COVERAGE INFRASTRUCTURE TESTS
# ============================================================


def _compose_file():
    """Resolve docker-compose.yml path from repo root.

    Returns the Path, or None if the file is not available
    (e.g. inside Docker where .dockerignore excludes it).
    """
    from pathlib import Path

    path = Path(__file__).parent.parent.parent.parent / "docker-compose.yml"
    return path if path.exists() else None


_skip_no_compose = pytest.mark.skipif(
    _compose_file() is None,
    reason="docker-compose.yml not available (excluded by .dockerignore)",
)


def _repo_file(name):
    """Return a repo-root file's Path, or None inside the Docker image."""
    from pathlib import Path

    path = Path(__file__).parent.parent.parent.parent / name
    return path if path.exists() else None


def _skip_without(name):
    return pytest.mark.skipif(
        _repo_file(name) is None,
        reason=f"{name} not available (excluded by .dockerignore)",
    )


@_skip_no_compose
@pytest.mark.django_db
class TestDockerComposeServices:
    """V578, V609, V610, V612, V613, V615, V620, V901: Docker services."""

    def test_garage_service_exists(self):
        """V578: Garage container in Docker Compose."""
        content = _compose_file().read_text()
        assert "garage:" in content
        assert "image: dxflrs/garage" in content

    def test_web_service_exists(self):
        """V609/V59: Web service Docker config (Daphne ASGI)."""
        content = _compose_file().read_text()
        assert "web:" in content
        assert "daphne" in content

    def test_postgres_service_exists(self):
        """V610: PostgreSQL service."""
        content = _compose_file().read_text()
        assert "db:" in content
        assert "image: postgres:17" in content

    def test_traefik_service_exists(self):
        """V612: Traefik reverse proxy."""
        content = _compose_file().read_text()
        assert "traefik:" in content
        assert "image: traefik:v3." in content

    def test_deployment_profiles_exist(self):
        """V613: Dev and prod deployment profiles."""
        content = _compose_file().read_text()
        assert 'profiles: ["dev"]' in content
        assert 'profiles: ["prod"]' in content

    def test_migrations_on_startup(self):
        """V615: Database migrations on startup."""
        content = _compose_file().read_text()
        assert "python manage.py migrate" in content

    def test_celery_services_exist(self):
        """V620: Celery worker and beat services."""
        content = _compose_file().read_text()
        assert "celery-worker:" in content
        assert "celery-beat:" in content

    def test_restart_policies_configured(self):
        """V901: Docker restart policies."""
        content = _compose_file().read_text()
        assert "restart: unless-stopped" in content


@pytest.mark.django_db
class TestInfrastructureConfiguration:
    """V575, V592, V594, V606, V614, V619: Infrastructure settings."""

    def test_whitenoise_in_storages(self):
        """V575: Static files served via WhiteNoise.

        Note: conftest.py removes WhiteNoise from runtime MIDDLEWARE to
        avoid race conditions with parallel test workers, so we check
        the settings module source directly.
        """
        import props.settings as ps

        assert any("whitenoise" in m.lower() for m in ps.MIDDLEWARE)

    def test_tailwind_css_configured(self, client_logged_in):
        """V592: Tailwind CSS 4.x."""
        response = client_logged_in.get("/")
        content = response.content.decode()
        assert "tailwind.css" in content or "tailwindcss" in content

    def test_dark_mode_support(self):
        """V594: Dark mode configuration."""
        from django.test import RequestFactory

        from props.context_processors import site_settings

        factory = RequestFactory()
        request = factory.get("/")
        ctx = site_settings(request)
        assert "color_mode" in ctx

    def test_session_configuration(self):
        """V606: Session management configuration."""
        from django.conf import settings

        assert hasattr(settings, "SESSION_COOKIE_SECURE")
        assert hasattr(settings, "SESSION_COOKIE_HTTPONLY")

    def test_environment_variables_used(self):
        """V614: Environment variables and startup validation."""
        from django.conf import settings

        assert hasattr(settings, "SECRET_KEY")
        assert hasattr(settings, "DATABASE_URL")
        assert hasattr(settings, "DEBUG")

    def test_celery_broker_url_configured(self):
        """V619: Celery + Redis stack."""
        from django.conf import settings

        assert hasattr(settings, "CELERY_BROKER_URL")
        assert settings.CELERY_BROKER_URL
        assert "redis" in settings.CELERY_BROKER_URL.lower()


@pytest.mark.django_db
class TestGravatarConfiguration:
    """V632, V633, V634: Gravatar integration."""

    def test_gravatar_in_installed_apps(self):
        """V632: django-gravatar2 in INSTALLED_APPS."""
        from django.conf import settings

        assert "django_gravatar" in settings.INSTALLED_APPS

    def test_gravatar_settings_configured(self):
        """V633: Gravatar config settings."""
        from django.conf import settings

        assert hasattr(settings, "GRAVATAR_DEFAULT_IMAGE")
        assert hasattr(settings, "GRAVATAR_DEFAULT_SECURE")

    def test_gravatar_used_in_templates(self, client_logged_in):
        """V634: Gravatar used in templates."""
        response = client_logged_in.get("/")
        content = response.content.decode()
        assert "gravatar.com" in content or "avatar.html" in content


@pytest.mark.django_db
class TestDeploymentConstraints:
    """V686-V692, V694, V696: Deployment constraints."""

    def test_htmx_in_base_template(self, client_logged_in):
        """V686: Frontend uses HTMX + Tailwind."""
        response = client_logged_in.get("/")
        content = response.content.decode()
        assert "htmx" in content.lower()

    def test_unfold_in_installed_apps(self):
        """V687: Admin uses django-unfold."""
        from django.conf import settings

        assert "unfold" in settings.INSTALLED_APPS

    @_skip_no_compose
    def test_docker_compose_exists(self):
        """V688, V691: Docker Compose + S3."""
        assert _compose_file() is not None

    def test_uv_dependency_management_configured(self):
        """V690, V628: uv dependency management (S4.13)."""
        from pathlib import Path

        root = Path(__file__).parent.parent.parent.parent
        pyproject = (root / "pyproject.toml").read_text()
        assert "[project]" in pyproject
        assert "Django" in pyproject
        assert (root / "uv.lock").exists()
        assert not (root / "requirements.txt").exists()

    @_skip_without("Dockerfile")
    def test_docker_build_installs_from_lockfile(self):
        """S4.13.2-04: the image installs from the frozen lockfile."""
        dockerfile = _repo_file("Dockerfile").read_text()
        assert "uv sync --frozen" in dockerfile
        assert "pip install" not in dockerfile

    @_skip_no_compose
    def test_single_server_deployment(self):
        """V692: Single server deployment."""
        content = _compose_file().read_text()
        assert "db:" in content
        assert "web:" in content or "web-prod:" in content

    def test_agpl_license(self):
        """V694: AGPL-3.0 license."""
        from pathlib import Path

        license_file = Path(__file__).parent.parent.parent.parent / "LICENSE"
        assert license_file.exists()
        content = license_file.read_text()
        assert "GNU AFFERO GENERAL PUBLIC LICENSE" in content

    def test_email_settings_configured(self):
        """V696: Self-contained with SMTP."""
        from django.conf import settings

        assert hasattr(settings, "EMAIL_HOST")
        assert hasattr(settings, "DEFAULT_FROM_EMAIL")


@pytest.mark.django_db
class TestMigrations:
    """Ensure all model changes have corresponding migrations."""

    def test_no_missing_migrations(self):
        """makemigrations --check must report no pending changes.

        If this fails, a model was changed without generating a
        migration. Run: python manage.py makemigrations
        """
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        try:
            call_command(
                "makemigrations",
                "--check",
                "--dry-run",
                stdout=out,
            )
        except SystemExit:
            pytest.fail(
                f"Missing migrations detected: {out.getvalue()}"
                f"\nRun: python manage.py makemigrations"
            )


# ============================================================
# APP_VERSION SETTING AND CONTEXT PROCESSOR TESTS
# ============================================================


class VersionSettingsTest(TestCase):
    def test_app_version_setting_exists(self):
        """APP_VERSION should exist and be a non-empty string."""
        self.assertTrue(hasattr(settings, "APP_VERSION"))
        self.assertIsInstance(settings.APP_VERSION, str)
        self.assertGreater(len(settings.APP_VERSION), 0)


class VersionContextProcessorTest(TestCase):
    def test_app_version_in_site_settings_context(self):
        """site_settings context processor should include app_version."""
        from props.context_processors import site_settings

        factory = RequestFactory()
        request = factory.get("/")
        context = site_settings(request)
        self.assertIn("app_version", context)
        self.assertIsInstance(context["app_version"], str)


class VersionFooterTest(TestCase):
    def test_version_shown_in_footer_for_authenticated_user(self):
        """Footer should display the app version for logged-in users."""
        from django.contrib.auth import get_user_model

        User = get_user_model()
        User.objects.create_user(
            username="footertest",
            email="footer@test.com",
            password="testpass123",
        )
        self.client.login(username="footertest", password="testpass123")
        response = self.client.get("/")
        # Follow redirects to reach a page with the footer
        if response.status_code == 302:
            response = self.client.get(response.url)
        content = response.content.decode()
        self.assertIn(settings.APP_VERSION, content)


class TestSendBrandedEmail:
    """Test the send_branded_email utility."""

    @patch("django.core.mail.EmailMultiAlternatives.send")
    def test_sends_synchronously(self, mock_send, db):
        from accounts.email import send_branded_email

        send_branded_email(
            template_name="verification",
            context={
                "display_name": "Test User",
                "verify_url": "https://example.com/verify/abc/",
            },
            subject="Test Subject",
            recipient="test@example.com",
        )

        mock_send.assert_called_once()

    @patch("django.core.mail.EmailMultiAlternatives.send")
    def test_injects_branding_context(self, mock_send, db):
        from django.core.mail import EmailMultiAlternatives

        from accounts.email import send_branded_email

        sent_messages = []
        original_init = EmailMultiAlternatives.__init__

        def capture_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            sent_messages.append(self)

        with patch.object(EmailMultiAlternatives, "__init__", capture_init):
            send_branded_email(
                template_name="account_rejected",
                context={"display_name": "Rejected User"},
                subject="Rejected",
                recipient="reject@example.com",
            )

        assert len(sent_messages) == 1
        msg = sent_messages[0]
        assert settings.SITE_NAME in msg.body

    @patch("django.core.mail.EmailMultiAlternatives.send")
    def test_handles_list_recipient(self, mock_send, db):
        from django.core.mail import EmailMultiAlternatives

        from accounts.email import send_branded_email

        sent_messages = []
        original_init = EmailMultiAlternatives.__init__

        def capture_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            sent_messages.append(self)

        with patch.object(EmailMultiAlternatives, "__init__", capture_init):
            send_branded_email(
                template_name="admin_new_pending",
                context={
                    "display_name": "New User",
                    "user_email": "new@example.com",
                    "department_name": "Props",
                    "approval_url": "https://example.com/approve/",
                },
                subject="New Pending",
                recipient=[
                    "admin1@example.com",
                    "admin2@example.com",
                ],
            )

        assert len(sent_messages) == 1
        assert sent_messages[0].to == [
            "admin1@example.com",
            "admin2@example.com",
        ]


class TestCacheConfiguration:
    """CACHE_URL is optional; the cache follows the Celery Redis host."""

    def test_cache_url_defaults_to_broker_host_db_one(self):
        from props.settings import cache_url_from_broker

        assert (
            cache_url_from_broker("redis://redis:6379/0")
            == "redis://redis:6379/1"
        )

    def test_cache_url_default_keeps_credentials_and_host(self):
        from props.settings import cache_url_from_broker

        assert (
            cache_url_from_broker("redis://:secret@cache.internal:6380/3")
            == "redis://:secret@cache.internal:6380/1"
        )

    @_skip_without(".env.example")
    def test_example_env_documents_cache_url(self):
        assert "CACHE_URL=" in _repo_file(".env.example").read_text()


class TestMediaIsolation:
    """Tests must not write media into the source tree.

    Generated files under src/media are walked by pytest collection and,
    through a Docker bind mount, make the suite appear to hang.
    """

    def test_media_root_is_outside_source_tree(self, settings):
        from pathlib import Path

        media_root = Path(settings.MEDIA_ROOT).resolve()
        source_media = (Path(settings.BASE_DIR) / "media").resolve()
        assert media_root != source_media
        assert source_media not in media_root.parents

    def test_pytest_does_not_recurse_into_generated_dirs(self):
        from pathlib import Path

        root = Path(__file__).parent.parent.parent.parent
        pyproject = (root / "pyproject.toml").read_text()
        assert "norecursedirs" in pyproject
        for name in ("media", "staticfiles"):
            assert f'"{name}"' in pyproject


class TestImageRegistryOverride:
    """Prod services pull from a registry chosen per deployment."""

    @_skip_no_compose
    def test_prod_images_use_props_image_variable(self):
        content = _compose_file().read_text()
        assert "ghcr.io/realworldtech/props:" not in content
        image = "${PROPS_IMAGE:-ghcr.io/realworldtech/props}"
        assert f"{image}:${{PROPS_VERSION:-latest}}" in content

    @_skip_without(".env.example")
    def test_example_env_documents_props_image(self):
        assert "PROPS_IMAGE=" in _repo_file(".env.example").read_text()


class TestBuildOnceImages:
    """One image per commit; the version reaches the app at runtime."""

    @_skip_without("Dockerfile")
    def test_dockerfile_has_test_stage_and_runtime_default(self):
        dockerfile = _repo_file("Dockerfile").read_text()
        assert "FROM base AS test" in dockerfile
        assert (
            dockerfile.rstrip()
            .splitlines()[-1]
            .startswith("FROM base AS runtime")
        )

    @_skip_without("Dockerfile")
    def test_dockerfile_does_not_bake_app_version(self):
        dockerfile = _repo_file("Dockerfile").read_text()
        assert "ARG APP_VERSION" not in dockerfile
        assert "ENV GIT_COMMIT=" in dockerfile

    @_skip_no_compose
    def test_prod_services_pass_version_from_env(self):
        content = _compose_file().read_text()
        images = content.count("${PROPS_IMAGE:-ghcr.io/realworldtech/props}")
        assert images >= 5
        assert content.count("APP_VERSION: ${PROPS_VERSION:-latest}") == images
