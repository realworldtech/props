"""PDF generation for PROPS."""

from weasyprint import HTML

from django.template.loader import render_to_string


def generate_pick_sheet_pdf(hold_list, items):
    """Generate a pick sheet PDF for a hold list.

    Args:
        hold_list: HoldList instance
        items: QuerySet of HoldListItem instances

    Returns:
        bytes: PDF file content
    """
    html_string = render_to_string(
        "assets/pick_sheet.html",
        {
            "hold_list": hold_list,
            "items": items,
        },
    )
    return HTML(string=html_string).write_pdf()
