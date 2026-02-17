"""PDF generation for PROPS."""

from weasyprint import HTML

from django.template.loader import render_to_string


def generate_pick_sheet_pdf(hold_list, items, generated_by=None):
    """Generate a pick sheet PDF for a hold list.

    Args:
        hold_list: HoldList instance
        items: QuerySet of HoldListItem instances
        generated_by: User who generated the sheet (optional)

    Returns:
        bytes: PDF file content
    """
    html_string = render_to_string(
        "assets/pick_sheet.html",
        {
            "hold_list": hold_list,
            "items": items,
            "total_count": items.count(),
            "generated_by": generated_by,
        },
    )
    return HTML(string=html_string).write_pdf()
