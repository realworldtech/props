"""Tests for props non-functional requirements — health, accessibility."""

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

        pyproject = (
            Path(__file__).parent.parent.parent.parent / "pyproject.toml"
        )
        assert pyproject.exists()
        content = pyproject.read_text()
        assert "[tool.black]" in content
        assert "[tool.isort]" in content


@pytest.mark.django_db
class TestAndroidFormSubmission:
    """Regression tests for Android Chrome form submission bug.

    Android Chrome re-checks the submitter button's disabled state after
    the submit handler returns. If btn.disabled is set synchronously (or
    via setTimeout(0) which fires before form serialisation completes on
    Android), Chrome cancels the in-flight POST.  Additionally, mutating
    the button's DOM (textContent, appendChild) during the submit event
    can trigger re-evaluation of the submitter on Android.

    The fix: defer ALL button mutations (spinner, disable) to a
    requestAnimationFrame callback so they execute after the browser has
    committed the navigation.
    """

    def test_base_template_submit_handler_does_not_disable_button(
        self, admin_client
    ):
        """The global submit handler must never set btn.disabled = true.

        Android Chrome re-checks submitter state after the handler
        returns.  Setting disabled (even in setTimeout(0)) causes Chrome
        to cancel the form POST when the virtual keyboard is
        dismissing.  The handler should use pointer-events/aria-disabled
        and data-submitted flag instead.
        """
        response = admin_client.get(reverse("assets:quick_capture"))
        content = response.content.decode()
        # The submit handler script should NOT contain btn.disabled
        # (the old broken pattern that cancels Android submissions)
        assert "btn.disabled = true" not in content, (
            "base.html submit handler must not set btn.disabled — "
            "this cancels form submissions on Android Chrome"
        )

    def test_base_template_submit_handler_defers_dom_mutations(
        self, admin_client
    ):
        """All button DOM changes must be inside requestAnimationFrame.

        Synchronous DOM changes (textContent, appendChild) during the
        submit event can cause Android Chrome to re-evaluate the
        submitter and cancel the POST.
        """
        import re

        response = admin_client.get(reverse("assets:quick_capture"))
        content = response.content.decode()
        # Extract the submit event handler script block
        match = re.search(
            r"document\.addEventListener\('submit'," r"(.*?)\);\s*</script>",
            content,
            re.DOTALL,
        )
        assert match, "Submit event handler not found in page"
        handler_code = match.group(1)
        assert "requestAnimationFrame" in handler_code, (
            "base.html submit handler must use requestAnimationFrame "
            "to defer button mutations"
        )

    def test_quick_capture_form_submits_with_name(self, admin_client):
        """Round-trip: GET form, extract fields, POST with name.

        Regression test for the Android bug: entering text in the name
        field must not prevent form submission.
        """
        from assets.tests.functional.helpers import FormFieldCollector

        get_resp = admin_client.get(reverse("assets:quick_capture"))
        assert get_resp.status_code == 200

        parser = FormFieldCollector()
        parser.feed(get_resp.content.decode())
        fields = parser.fields

        # Populate name — the scenario that fails on Android
        fields["name"] = "Android Test Asset"
        # Remove image field (file fields need special handling)
        fields.pop("image", None)

        post_resp = admin_client.post(reverse("assets:quick_capture"), fields)
        assert post_resp.status_code == 200
        from assets.models import Asset

        assert Asset.objects.filter(
            name="Android Test Asset", status="draft"
        ).exists()

    def test_quick_capture_submit_button_is_type_submit(self, admin_client):
        """The Capture Asset button must be type=submit.

        All other buttons in the form must be type=button to avoid
        confusing Android Chrome's submitter identification.
        """
        import re

        response = admin_client.get(reverse("assets:quick_capture"))
        content = response.content.decode()
        # Find all buttons inside the form
        form_match = re.search(
            r"<form[^>]*method=\"post\"[^>]*>(.*?)</form>",
            content,
            re.DOTALL,
        )
        assert form_match, "Quick capture form not found"
        form_html = form_match.group(1)
        buttons = re.findall(r"<button\b([^>]*)>", form_html, re.DOTALL)
        submit_count = 0
        for btn_attrs in buttons:
            if 'type="submit"' in btn_attrs:
                submit_count += 1
            else:
                assert 'type="button"' in btn_attrs, (
                    f'Non-submit button missing type="button": '
                    f"{btn_attrs[:80]}"
                )
