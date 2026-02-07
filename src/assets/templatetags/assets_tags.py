"""Template tags for the assets app."""

from django import template

from assets.models import Category, Department

register = template.Library()


@register.simple_tag
def category_exists(name):
    """Check if a category with the given name exists."""
    return Category.objects.filter(name__iexact=name).exists()


@register.simple_tag
def department_exists(name):
    """Check if a department with the given name exists."""
    return Department.objects.filter(name__iexact=name).exists()
