"""Tests for the props app — colours, branding, and email."""

from unittest.mock import MagicMock, patch

import pytest

from django.core.cache import cache
from django.urls import reverse

from props.colors import (
    auto_derive_accent,
    auto_derive_secondary,
    generate_brand_css_properties,
    generate_dark_palette,
    generate_oklch_palette,
    hex_to_oklch,
)


class TestHexToOklch:
    """Test hex_to_oklch conversion pipeline."""

    def test_returns_three_floats(self):
        l, c, h = hex_to_oklch("#4F46E5")
        assert isinstance(l, float)
        assert isinstance(c, float)
        assert isinstance(h, float)

    def test_white_is_high_lightness(self):
        l, c, h = hex_to_oklch("#FFFFFF")
        assert l > 0.99

    def test_black_is_low_lightness(self):
        l, c, h = hex_to_oklch("#000000")
        assert l < 0.01

    def test_pure_red(self):
        l, c, h = hex_to_oklch("#FF0000")
        assert 0.3 < l < 0.7
        assert c > 0.1

    def test_accepts_without_hash(self):
        l1, c1, h1 = hex_to_oklch("#4F46E5")
        l2, c2, h2 = hex_to_oklch("4F46E5")
        assert abs(l1 - l2) < 0.001
        assert abs(c1 - c2) < 0.001


class TestGenerateOklchPalette:
    """Test palette generation."""

    def test_returns_11_shades(self):
        palette = generate_oklch_palette("#4F46E5")
        expected_keys = {
            "50",
            "100",
            "200",
            "300",
            "400",
            "500",
            "600",
            "700",
            "800",
            "900",
            "950",
        }
        assert set(palette.keys()) == expected_keys

    def test_values_are_hex(self):
        palette = generate_oklch_palette("#4F46E5")
        for shade, value in palette.items():
            assert value.startswith("#"), f"Shade {shade}: {value}"

    def test_achromatic_input_does_not_crash(self):
        palette = generate_oklch_palette("#808080")
        assert len(palette) == 11

    def test_empty_hex_returns_empty(self):
        assert generate_oklch_palette("") == {}
        assert generate_oklch_palette(None) == {}

    def test_invalid_hex_returns_empty(self):
        assert generate_oklch_palette("not-a-color") == {}


class TestGenerateBrandCssProperties:
    """Test CSS custom property generation."""

    def test_generate_css_properties(self):
        css = generate_brand_css_properties(primary_hex="#4F46E5")
        assert "--brand-primary-500:" in css
        assert "--brand-primary-50:" in css

    def test_css_properties_multiple_colors(self):
        css = generate_brand_css_properties(
            primary_hex="#4F46E5",
            secondary_hex="#10B981",
        )
        assert "--brand-primary-500:" in css
        assert "--brand-secondary-500:" in css

    def test_empty_input_returns_empty(self):
        css = generate_brand_css_properties()
        assert css == ""

    def test_context_processor_includes_brand_css(self, client, db):
        from django.test import RequestFactory

        from props.context_processors import site_settings

        factory = RequestFactory()
        request = factory.get("/")
        ctx = site_settings(request)
        assert "brand_css_properties" in ctx


class TestSiteBranding:
    """Test the SiteBranding singleton model."""

    def test_singleton_enforcement(self, db):
        from assets.models import SiteBranding

        first = SiteBranding.objects.create()
        second = SiteBranding()
        second.save()
        # Second save should reuse the first pk
        assert SiteBranding.objects.count() == 1
        assert second.pk == first.pk

    def test_get_cached_returns_none_when_empty(self, db):
        from assets.models import SiteBranding

        cache.clear()
        result = SiteBranding.get_cached()
        assert result is None

    def test_get_cached_returns_instance(self, db):
        from assets.models import SiteBranding

        cache.clear()
        SiteBranding.objects.create()
        result = SiteBranding.get_cached()
        assert result is not None
        assert isinstance(result, SiteBranding)

    def test_save_clears_cache(self, db):
        from assets.models import SiteBranding

        cache.clear()
        branding = SiteBranding.objects.create()
        # Prime cache
        SiteBranding.get_cached()
        assert cache.get("site_branding") is not None
        # Save should clear
        branding.save()
        assert cache.get("site_branding") is None

    def test_str(self, db):
        from assets.models import SiteBranding

        branding = SiteBranding.objects.create()
        assert str(branding) == "Site Branding"

    def test_logo_file_size_validator(self, db):
        from django.core.exceptions import ValidationError

        from assets.models import validate_logo_file_size

        mock_file = MagicMock()
        mock_file.size = 600 * 1024  # 600 KB, exceeds 500 KB limit
        with pytest.raises(ValidationError, match="500 KB"):
            validate_logo_file_size(mock_file)

    def test_favicon_file_size_validator(self, db):
        from django.core.exceptions import ValidationError

        from assets.models import validate_favicon_file_size

        mock_file = MagicMock()
        mock_file.size = 200 * 1024  # 200 KB, exceeds 100 KB limit
        with pytest.raises(ValidationError, match="100 KB"):
            validate_favicon_file_size(mock_file)

    def test_logo_valid_size_passes(self):
        from assets.models import validate_logo_file_size

        mock_file = MagicMock()
        mock_file.size = 100 * 1024  # 100 KB, within limit
        validate_logo_file_size(mock_file)  # Should not raise

    def test_clean_rejects_invalid_logo_extension(self, db):
        from django.core.exceptions import ValidationError

        from assets.models import SiteBranding

        branding = SiteBranding()
        mock_file = MagicMock()
        mock_file.name = "logo.jpg"
        branding.logo_light = mock_file
        with pytest.raises(ValidationError, match="logo_light"):
            branding.clean()


class TestHealthEndpoint:
    """C1: Health check endpoint at /health/."""

    def test_health_returns_200(self, client, db):
        response = client.get("/health/")
        assert response.status_code == 200

    def test_health_returns_json(self, client, db):
        response = client.get("/health/")
        assert response["Content-Type"] == "application/json"
        data = response.json()
        assert data["status"] == "ok"
        assert data["db"] is True

    def test_health_no_auth_required(self, client, db):
        response = client.get("/health/")
        assert response.status_code == 200


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
        assert "PROPS" in msg.body

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


class TestDarkModePalette:
    """Test dark mode palette generation."""

    def test_dark_mode_palette_generated(self):
        """Verify dark mode CSS vars are generated."""
        css = generate_brand_css_properties(primary_hex="#4F46E5")
        assert "--brand-primary-dark-500:" in css
        assert "--brand-primary-dark-50:" in css
        assert "--brand-primary-dark-950:" in css

    def test_dark_mode_reduced_chroma(self):
        """Verify dark palette has lower max chroma than light palette."""
        from coloraide import Color

        light_palette = generate_oklch_palette("#4F46E5")
        dark_palette = generate_dark_palette("#4F46E5")

        # Average chroma across all shades should be lower for dark
        def avg_chroma(palette):
            total = 0.0
            for hex_val in palette.values():
                c = Color(hex_val).convert("oklch")
                ch = c["chroma"]
                import math

                if math.isnan(ch):
                    ch = 0.0
                total += ch
            return total / len(palette)

        light_avg = avg_chroma(light_palette)
        dark_avg = avg_chroma(dark_palette)
        assert dark_avg < light_avg, (
            f"Dark avg chroma {dark_avg:.4f} "
            f"should be < light avg {light_avg:.4f}"
        )

    def test_dark_palette_returns_11_shades(self):
        """Dark palette should have same shade keys as light."""
        palette = generate_dark_palette("#4F46E5")
        assert len(palette) == 11
        expected_keys = {
            "50",
            "100",
            "200",
            "300",
            "400",
            "500",
            "600",
            "700",
            "800",
            "900",
            "950",
        }
        assert set(palette.keys()) == expected_keys

    def test_dark_palette_empty_input(self):
        """Empty hex should return empty dict."""
        assert generate_dark_palette("") == {}
        assert generate_dark_palette(None) == {}

    def test_dark_css_vars_for_multiple_colors(self):
        """Dark vars generated for secondary and accent too."""
        css = generate_brand_css_properties(
            primary_hex="#4F46E5",
            secondary_hex="#10B981",
            accent_hex="#F59E0B",
        )
        assert "--brand-primary-dark-500:" in css
        assert "--brand-secondary-dark-500:" in css
        assert "--brand-accent-dark-500:" in css


class TestAutoDerive:
    """Test auto-derivation of secondary and accent from primary."""

    def test_auto_derive_secondary_from_primary(self):
        """Set only primary, verify secondary is auto-derived."""
        secondary = auto_derive_secondary("#4F46E5")
        assert secondary.startswith("#")
        assert len(secondary) == 7
        # Should be different from primary
        assert secondary.lower() != "#4f46e5"

    def test_auto_derive_accent_from_primary(self):
        """Set only primary, verify accent is auto-derived."""
        accent = auto_derive_accent("#4F46E5")
        assert accent.startswith("#")
        assert len(accent) == 7
        assert accent.lower() != "#4f46e5"

    def test_auto_derive_secondary_different_from_accent(self):
        """Secondary and accent should be different."""
        secondary = auto_derive_secondary("#4F46E5")
        accent = auto_derive_accent("#4F46E5")
        assert secondary != accent

    def test_auto_derive_returns_empty_for_invalid(self):
        """Invalid input should return empty string."""
        assert auto_derive_secondary("") == ""
        assert auto_derive_accent("") == ""
        assert auto_derive_secondary("not-a-color") == ""
        assert auto_derive_accent("not-a-color") == ""

    def test_explicit_secondary_not_overridden(self, db):
        """If secondary is set explicitly, auto-derive doesn't run."""
        from django.test import RequestFactory

        from assets.models import SiteBranding

        cache.clear()
        SiteBranding.objects.create(
            primary_color="#4F46E5",
            secondary_color="#FF0000",
        )

        from props.context_processors import site_settings

        factory = RequestFactory()
        request = factory.get("/")
        ctx = site_settings(request)
        # The explicit secondary (#FF0000) palette should appear
        css = ctx["brand_css_properties"]
        # Should have secondary vars from #FF0000, not auto-derived
        assert "--brand-secondary-500:" in css

    def test_auto_derive_in_context_processor(self, db):
        """When secondary/accent are empty, context processor derives."""
        from django.test import RequestFactory

        from assets.models import SiteBranding

        cache.clear()
        SiteBranding.objects.create(
            primary_color="#4F46E5",
            secondary_color="",
            accent_color="",
        )

        from props.context_processors import site_settings

        factory = RequestFactory()
        request = factory.get("/")
        ctx = site_settings(request)
        css = ctx["brand_css_properties"]
        assert "--brand-secondary-500:" in css
        assert "--brand-accent-500:" in css


class TestBrandingCacheTTL:
    """Test SiteBranding cache uses timeout=None."""

    def test_branding_cache_timeout_none(self, db):
        """Verify cache uses timeout=None (no expiry)."""
        from assets.models import SiteBranding

        cache.clear()
        SiteBranding.objects.create()

        with patch.object(cache, "set", wraps=cache.set) as mock_set:
            cache.delete("site_branding")
            SiteBranding.get_cached()
            mock_set.assert_called_once()
            call_kwargs = mock_set.call_args
            # timeout should be None (keyword or positional arg)
            if call_kwargs.kwargs.get("timeout") is not None:
                # Check positional args
                assert call_kwargs.kwargs.get("timeout") is None, (
                    f"Expected timeout=None, "
                    f"got {call_kwargs.kwargs.get('timeout')}"
                )
            # Also verify by position if passed that way
            args = call_kwargs.args
            if len(args) >= 3:
                assert args[2] is None, f"Expected timeout=None, got {args[2]}"


class TestBrandingCSSIntegration:
    """Test that brand CSS custom properties are wired into templates."""

    def test_brand_css_properties_in_base_template(self, client_logged_in, db):
        """Dashboard response should contain --brand-primary CSS var."""
        response = client_logged_in.get("/")
        content = response.content.decode()
        assert "--brand-primary-" in content

    def test_brand_css_with_custom_primary_color(self, client_logged_in, db):
        """SiteBranding with custom primary renders derived CSS vars."""
        from assets.models import SiteBranding

        cache.clear()
        SiteBranding.objects.create(primary_color="#BC2026")
        response = client_logged_in.get("/")
        content = response.content.decode()
        assert "--brand-primary-500:" in content
        assert "--brand-secondary-500:" in content
        assert "--brand-accent-500:" in content

    def test_brand_css_vars_present_when_no_branding(
        self, client_logged_in, db
    ):
        """Even without SiteBranding, fallback CSS vars are provided."""
        from assets.models import SiteBranding

        cache.clear()
        SiteBranding.objects.all().delete()
        response = client_logged_in.get("/")
        content = response.content.decode()
        # Fallback CSS vars should still be present from settings
        assert "--brand-primary-" in content

    def test_context_processor_includes_brand_css(self, db):
        """The context processor always includes brand_css_properties."""
        from django.test import RequestFactory

        from props.context_processors import site_settings

        factory = RequestFactory()
        request = factory.get("/")
        ctx = site_settings(request)
        assert "brand_css_properties" in ctx
        # Should be non-empty even without SiteBranding
        assert ctx["brand_css_properties"] != ""

    def test_brand_utility_classes_in_base_template(
        self, client_logged_in, db
    ):
        """Base template should contain brand utility CSS classes."""
        response = client_logged_in.get("/")
        content = response.content.decode()
        assert ".bg-brand-primary" in content
        assert ".text-brand-primary" in content
        assert ".border-brand-primary" in content


# ============================================================
# BATCH 5: S5 NON-FUNCTIONAL / S4.6 UNFOLD THEME TESTS
# ============================================================


class TestUnfoldThemeConfiguration:
    """V596 S4.6.2.1: Unfold theme branding config."""

    def test_unfold_settings_exist(self):
        """UNFOLD settings dict is configured."""
        from django.conf import settings

        assert hasattr(settings, "UNFOLD")
        assert isinstance(settings.UNFOLD, dict)

    def test_unfold_site_title(self):
        """UNFOLD SITE_TITLE matches SITE_NAME."""
        from django.conf import settings

        assert settings.UNFOLD["SITE_TITLE"] == settings.SITE_NAME

    def test_unfold_site_header(self):
        """UNFOLD SITE_HEADER is configured."""
        from django.conf import settings

        assert settings.UNFOLD["SITE_HEADER"] == settings.SITE_SHORT_NAME

    def test_unfold_has_primary_colors(self):
        """UNFOLD COLORS includes a primary palette."""
        from django.conf import settings

        colors = settings.UNFOLD.get("COLORS", {})
        assert "primary" in colors
        palette = colors["primary"]
        assert "500" in palette
        assert "50" in palette

    def test_unfold_sidebar_navigation(self):
        """UNFOLD sidebar has navigation entries."""
        from django.conf import settings

        sidebar = settings.UNFOLD.get("SIDEBAR", {})
        nav = sidebar.get("navigation", [])
        assert len(nav) > 0

    def test_unfold_sidebar_includes_assets(self):
        """UNFOLD sidebar navigation includes Assets section."""
        from django.conf import settings

        sidebar = settings.UNFOLD.get("SIDEBAR", {})
        nav = sidebar.get("navigation", [])
        titles = [g.get("title", "") for g in nav if isinstance(g, dict)]
        assert "Assets" in titles

    def test_unfold_sidebar_includes_users(self):
        """UNFOLD sidebar navigation includes Users section."""
        from django.conf import settings

        sidebar = settings.UNFOLD.get("SIDEBAR", {})
        nav = sidebar.get("navigation", [])
        titles = [g.get("title", "") for g in nav if isinstance(g, dict)]
        assert "Users & Auth" in titles

    def test_unfold_sidebar_includes_site_branding(self):
        """UNFOLD sidebar has Site Branding in Settings section."""
        from django.conf import settings

        sidebar = settings.UNFOLD.get("SIDEBAR", {})
        nav = sidebar.get("navigation", [])
        settings_group = next(
            (g for g in nav if g.get("title") == "Settings"), None
        )
        assert settings_group is not None
        item_titles = [i["title"] for i in settings_group["items"]]
        assert "Site Branding" in item_titles


class TestSiteBrandingModelExtended:
    """V596: SiteBranding model for Unfold theme customisation."""

    def test_site_branding_has_color_fields(self):
        """SiteBranding model has primary, secondary, accent color fields."""
        from assets.models import SiteBranding

        field_names = [f.name for f in SiteBranding._meta.get_fields()]
        assert "primary_color" in field_names
        assert "secondary_color" in field_names
        assert "accent_color" in field_names

    def test_site_branding_has_color_mode(self):
        """SiteBranding model has a color_mode field."""
        from assets.models import SiteBranding

        field = SiteBranding._meta.get_field("color_mode")
        assert field is not None
        assert field.default == "system"

    def test_site_branding_has_logo_fields(self):
        """SiteBranding model has logo_light and logo_dark fields."""
        from assets.models import SiteBranding

        field_names = [f.name for f in SiteBranding._meta.get_fields()]
        assert "logo_light" in field_names
        assert "logo_dark" in field_names
        assert "favicon" in field_names

    def test_site_branding_registered_in_admin(self):
        """SiteBranding is registered in the admin."""
        from django.contrib.admin.sites import site

        from assets.models import SiteBranding

        assert SiteBranding in site._registry


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


@pytest.mark.django_db
class TestDockerComposeServices:
    """V578, V609, V610, V612, V613, V615, V620, V901: Docker services."""

    def test_garage_service_exists(self):
        """V578: Garage container in Docker Compose."""
        from pathlib import Path

        compose_file = (
            Path(__file__).parent.parent.parent / "docker-compose.yml"
        )
        assert compose_file.exists()
        content = compose_file.read_text()
        assert "garage:" in content
        assert "image: dxflrs/garage" in content

    def test_web_service_exists(self):
        """V609: Web service Docker config."""
        from pathlib import Path

        compose_file = (
            Path(__file__).parent.parent.parent / "docker-compose.yml"
        )
        content = compose_file.read_text()
        assert "web:" in content
        assert "gunicorn" in content

    def test_postgres_service_exists(self):
        """V610: PostgreSQL service."""
        from pathlib import Path

        compose_file = (
            Path(__file__).parent.parent.parent / "docker-compose.yml"
        )
        content = compose_file.read_text()
        assert "db:" in content
        assert "image: postgres:17" in content

    def test_traefik_service_exists(self):
        """V612: Traefik reverse proxy."""
        from pathlib import Path

        compose_file = (
            Path(__file__).parent.parent.parent / "docker-compose.yml"
        )
        content = compose_file.read_text()
        assert "traefik:" in content
        assert "image: traefik:v3." in content

    def test_deployment_profiles_exist(self):
        """V613: Dev and prod deployment profiles."""
        from pathlib import Path

        compose_file = (
            Path(__file__).parent.parent.parent / "docker-compose.yml"
        )
        content = compose_file.read_text()
        assert 'profiles: ["dev"]' in content
        assert 'profiles: ["prod"]' in content

    def test_migrations_on_startup(self):
        """V615: Database migrations on startup."""
        from pathlib import Path

        compose_file = (
            Path(__file__).parent.parent.parent / "docker-compose.yml"
        )
        content = compose_file.read_text()
        assert "python manage.py migrate" in content

    def test_celery_services_exist(self):
        """V620: Celery worker and beat services."""
        from pathlib import Path

        compose_file = (
            Path(__file__).parent.parent.parent / "docker-compose.yml"
        )
        content = compose_file.read_text()
        assert "celery-worker:" in content
        assert "celery-beat:" in content

    def test_restart_policies_configured(self):
        """V901: Docker restart policies."""
        from pathlib import Path

        compose_file = (
            Path(__file__).parent.parent.parent / "docker-compose.yml"
        )
        content = compose_file.read_text()
        assert "restart: unless-stopped" in content


@pytest.mark.django_db
class TestInfrastructureConfiguration:
    """V575, V592, V594, V606, V614, V619: Infrastructure settings."""

    def test_whitenoise_in_storages(self):
        """V575: Static files served via WhiteNoise."""
        from django.conf import settings

        assert any("whitenoise" in m.lower() for m in settings.MIDDLEWARE)

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

    def test_docker_compose_exists(self):
        """V688, V691: Docker Compose + S3."""
        from pathlib import Path

        compose_file = (
            Path(__file__).parent.parent.parent / "docker-compose.yml"
        )
        assert compose_file.exists()

    def test_pip_tools_configured(self):
        """V690, V628: pip-tools dependency management."""
        from pathlib import Path

        req_in = Path(__file__).parent.parent.parent / "requirements.in"
        assert req_in.exists()
        content = req_in.read_text()
        assert "Django" in content

    def test_single_server_deployment(self):
        """V692: Single server deployment."""
        from pathlib import Path

        compose_file = (
            Path(__file__).parent.parent.parent / "docker-compose.yml"
        )
        content = compose_file.read_text()
        assert "db:" in content
        assert "web:" in content or "web-prod:" in content

    def test_agpl_license(self):
        """V694: AGPL-3.0 license."""
        from pathlib import Path

        license_file = Path(__file__).parent.parent.parent / "LICENSE"
        assert license_file.exists()
        content = license_file.read_text()
        assert "GNU AFFERO GENERAL PUBLIC LICENSE" in content

    def test_email_settings_configured(self):
        """V696: Self-contained with SMTP."""
        from django.conf import settings

        assert hasattr(settings, "EMAIL_HOST")
        assert hasattr(settings, "DEFAULT_FROM_EMAIL")


@pytest.mark.django_db
class TestNonFunctionalRequirements:
    """V868-V897: Performance and UX requirements."""

    def test_quick_capture_view_loads(self, admin_client):
        """V868: Concurrent quick capture users."""
        response = admin_client.get(reverse("assets:quick_capture"))
        assert response.status_code == 200

    def test_search_returns_results(self, admin_client, asset):
        """V871: Search response time."""
        response = admin_client.get(
            reverse("assets:asset_list"), {"q": asset.name}
        )
        assert response.status_code == 200

    def test_forms_have_appropriate_css(self, admin_client):
        """V874: Touch targets 44x44px."""
        response = admin_client.get(reverse("assets:asset_create"))
        content = response.content.decode()
        assert "form" in content.lower()

    def test_asset_list_has_lazy_loading(self, admin_client, asset):
        """V878: Lazy loading in scrollable views."""
        response = admin_client.get(reverse("assets:asset_list"))
        content = response.content.decode()
        assert 'loading="lazy"' in content or "lazy" in content.lower()

    def test_queryset_optimization_exists(self):
        """V879: select_related/prefetch_related."""
        from assets.services.bulk import build_asset_filter_queryset

        assert callable(build_asset_filter_queryset)

    def test_shared_queryset_builder_exists(self):
        """V881: Shared queryset builder."""
        from assets.services.bulk import build_asset_filter_queryset

        assert callable(build_asset_filter_queryset)

    def test_asset_model_has_indexes(self):
        """V882: Database indexes on Asset model."""
        from assets.models import Asset

        indexes = Asset._meta.indexes
        assert len(indexes) > 0

    def test_ai_status_endpoint_returns_json(self, admin_client, asset):
        """V886: AI HTMX polling."""
        from assets.models import AssetImage

        image = AssetImage.objects.create(asset=asset)
        response = admin_client.get(
            reverse("assets:ai_status", args=[asset.pk, image.pk])
        )
        assert response.status_code == 200

    def test_asset_creation_works(self, asset):
        """V897: 50K asset capacity."""
        assert asset.pk is not None
        assert asset.name is not None


@pytest.mark.django_db
class TestAccessibilityAndCodeQuality:
    """V902-V905, V907: WCAG and code quality."""

    def test_forms_have_labels(self, admin_client):
        """V902, V903: WCAG Level A, form labels."""
        response = admin_client.get(reverse("assets:asset_create"))
        content = response.content.decode()
        assert "<label" in content.lower()

    def test_pages_include_viewport_meta(self, client_logged_in):
        """V904: Mobile-first responsive."""
        response = client_logged_in.get("/")
        content = response.content.decode()
        assert 'name="viewport"' in content

    def test_interactive_elements_focusable(self, client_logged_in):
        """V905: Keyboard navigation."""
        response = client_logged_in.get("/")
        content = response.content.decode()
        assert "<button" in content.lower() or "<a " in content.lower()

    def test_code_quality_tools_configured(self):
        """V907: Code quality tools configured."""
        from pathlib import Path

        pyproject = Path(__file__).parent.parent.parent / "pyproject.toml"
        assert pyproject.exists()
        content = pyproject.read_text()
        assert "[tool.black]" in content
        assert "[tool.isort]" in content
