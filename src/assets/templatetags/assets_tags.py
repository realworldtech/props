"""Template tags for the assets app."""

from django import template

from assets.models import Category

register = template.Library()


@register.simple_tag
def category_exists(name):
    """Check if a category with the given name exists."""
    return Category.objects.filter(name__iexact=name).exists()
