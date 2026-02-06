"""Tests for the props app — colours, branding, and email."""

from unittest.mock import MagicMock, patch

import pytest

from django.core.cache import cache
from django.test import override_settings

from props.colors import generate_oklch_palette, hex_to_oklch


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

    def test_values_are_oklch_strings(self):
        palette = generate_oklch_palette("#4F46E5")
        for shade, value in palette.items():
            assert value.startswith("oklch("), f"{shade}: {value}"
            assert value.endswith(")"), f"{shade}: {value}"

    def test_lightness_descends(self):
        palette = generate_oklch_palette("#4F46E5")
        # Extract lightness from oklch strings
        shades_ordered = [
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
        ]
        lightnesses = []
        for shade in shades_ordered:
            val = palette[shade]
            # "oklch(97.7% .014 308.3)" -> extract 97.7
            pct = float(val.split("(")[1].split("%")[0])
            lightnesses.append(pct)
        # Each shade should be darker than the previous
        for i in range(1, len(lightnesses)):
            assert lightnesses[i] < lightnesses[i - 1]

    def test_different_colors_have_different_hue(self):
        palette_blue = generate_oklch_palette("#0000FF")
        palette_green = generate_oklch_palette("#00FF00")
        # The hue should be different between green and blue
        hue_blue = float(palette_blue["600"].rstrip(")").split()[-1])
        hue_green = float(palette_green["600"].rstrip(")").split()[-1])
        assert abs(hue_blue - hue_green) > 30

    def test_achromatic_input_does_not_crash(self):
        palette = generate_oklch_palette("#808080")
        assert len(palette) == 11


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


class TestSendBrandedEmail:
    """Test the send_branded_email utility."""

    @patch("accounts.tasks.send_email_task")
    def test_dispatches_to_celery(self, mock_task, db):
        from accounts.email import send_branded_email

        mock_task.delay = MagicMock()

        send_branded_email(
            template_name="verification",
            context={
                "display_name": "Test User",
                "verify_url": "https://example.com/verify/abc/",
            },
            subject="Test Subject",
            recipient="test@example.com",
        )

        mock_task.delay.assert_called_once()
        call_kwargs = mock_task.delay.call_args[1]
        assert call_kwargs["subject"] == "Test Subject"
        assert call_kwargs["recipient_list"] == ["test@example.com"]
        assert "example.com/verify/abc/" in call_kwargs["html_body"]
        assert "example.com/verify/abc/" in call_kwargs["text_body"]

    @patch("accounts.tasks.send_email_task")
    def test_injects_branding_context(self, mock_task, db):
        from accounts.email import send_branded_email

        mock_task.delay = MagicMock()

        send_branded_email(
            template_name="account_rejected",
            context={"display_name": "Rejected User"},
            subject="Rejected",
            recipient="reject@example.com",
        )

        call_kwargs = mock_task.delay.call_args[1]
        # Should contain the site name from settings
        assert "PROPS" in call_kwargs["html_body"]
        assert "PROPS" in call_kwargs["text_body"]

    @patch("accounts.tasks.send_email_task")
    def test_handles_list_recipient(self, mock_task, db):
        from accounts.email import send_branded_email

        mock_task.delay = MagicMock()

        send_branded_email(
            template_name="admin_new_pending",
            context={
                "display_name": "New User",
                "user_email": "new@example.com",
                "department_name": "Props",
                "approval_url": "https://example.com/approve/",
            },
            subject="New Pending",
            recipient=["admin1@example.com", "admin2@example.com"],
        )

        call_kwargs = mock_task.delay.call_args[1]
        assert call_kwargs["recipient_list"] == [
            "admin1@example.com",
            "admin2@example.com",
        ]
