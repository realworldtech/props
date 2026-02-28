"""Asset text search helpers."""

from django.db.models import Q

MAX_SEARCH_WORDS = 20


def build_asset_text_query(q):
    """Build Q object matching all words in q across asset text fields.

    Each word must appear in at least one of: name, description, barcode,
    or tag name.  Words are ANDed together so "blue bonnet" requires both
    "blue" and "bonnet" to appear (possibly in different fields).

    At most ``MAX_SEARCH_WORDS`` words are considered; additional words
    are silently ignored to bound query complexity.
    """
    words = q.split()[:MAX_SEARCH_WORDS]
    if not words:
        return Q(pk__in=[])
    combined = Q()
    for word in words:
        word_q = (
            Q(name__icontains=word)
            | Q(description__icontains=word)
            | Q(barcode__icontains=word)
            | Q(tags__name__icontains=word)
        )
        combined &= word_q
    return combined
