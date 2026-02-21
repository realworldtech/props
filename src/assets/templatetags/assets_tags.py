"""Template tags for the assets app."""

import re

from django import template

from assets.models import Category, Department

register = template.Library()

# Matches "Quick Capture <date/time>" auto-generated names
_QUICK_CAPTURE_RE = re.compile(r"^Quick Capture\b", re.IGNORECASE)


@register.simple_tag
def category_exists(name):
    """Check if a category with the given name exists."""
    return Category.objects.filter(name__iexact=name).exists()


@register.simple_tag
def department_exists(name):
    """Check if a department with the given name exists."""
    return Department.objects.filter(name__iexact=name).exists()


@register.simple_tag
def is_placeholder_name(name):
    """Check if an asset name is an auto-generated placeholder."""
    return bool(_QUICK_CAPTURE_RE.match(name))
