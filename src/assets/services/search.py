"""Asset text search helpers.

Provides PostgreSQL full-text search (FTS) for Asset querysets, with
icontains fallback for identifier fields (barcode, NFC tag IDs) that
don't benefit from stemming/tokenisation.

Falls back to icontains-only search on non-PostgreSQL backends (e.g.
SQLite in tests).
"""

from django.db import connection
from django.db.models import Case, Q, Value, When
from django.db.models.fields import FloatField

MAX_SEARCH_WORDS = 20


def _is_postgres():
    return connection.vendor == "postgresql"


def _build_fts_search(queryset, words, search_text, icontains_q):
    """FTS search path for PostgreSQL.

    Uses the pre-computed search_vector field (updated by a DB trigger)
    with a GIN index for fast full-text search.
    """
    from django.contrib.postgres.search import SearchQuery, SearchRank

    search_query = SearchQuery(search_text, search_type="websearch")

    # Filter on the stored search_vector field (GIN-indexed)
    fts_filter = Q(search_vector=search_query)

    # Tags via icontains (short strings, stemming adds little value;
    # M2M joins in SearchVector cause row duplication issues)
    tag_q = Q()
    for word in words:
        tag_q &= Q(tags__name__icontains=word)

    combined_filter = fts_filter | tag_q | icontains_q

    barcode_exact = Case(
        When(barcode__iexact=search_text, then=Value(10.0)),
        default=Value(0.0),
        output_field=FloatField(),
    )

    return (
        queryset.annotate(
            fts_rank=SearchRank("search_vector", search_query),
            barcode_boost=barcode_exact,
        )
        .filter(combined_filter)
        .distinct()
        .order_by("-barcode_boost", "-fts_rank")
    )


def _build_icontains_search(queryset, words, search_text, icontains_q):
    """icontains fallback for non-PostgreSQL backends."""
    # Build word-AND query across text fields
    text_q = Q()
    for word in words:
        text_q &= (
            Q(name__icontains=word)
            | Q(description__icontains=word)
            | Q(tags__name__icontains=word)
        )

    combined_filter = text_q | icontains_q

    barcode_exact = Case(
        When(barcode__iexact=search_text, then=Value(10.0)),
        default=Value(0.0),
        output_field=FloatField(),
    )

    return (
        queryset.annotate(barcode_boost=barcode_exact)
        .filter(combined_filter)
        .distinct()
        .order_by("-barcode_boost")
    )


def build_asset_search(
    queryset,
    q,
    include_nfc=True,
    include_category=False,
):
    """Apply search to an Asset queryset.

    Uses PostgreSQL FTS when available, falls back to icontains on other
    backends.

    - FTS/icontains on: name, description, tag names
    - icontains on: barcode (identifier, not prose)
    - icontains on: NFC tag IDs (if include_nfc=True)
    - icontains on: category name (if include_category=True)

    Args:
        queryset: Base Asset queryset to filter.
        q: Search string (space-separated words, ANDed).
        include_nfc: Include NFC tag ID substring matching.
        include_category: Include category name substring matching.

    Returns:
        Filtered, distinct queryset ordered by relevance.
    """
    words = q.split()[:MAX_SEARCH_WORDS]
    if not words:
        return queryset.none()

    search_text = " ".join(words)

    # Identifier fields: always icontains (not prose, no stemming benefit)
    icontains_q = Q()
    for word in words:
        word_q = Q(barcode__icontains=word)
        if include_nfc:
            word_q |= Q(
                nfc_tags__tag_id__icontains=word,
                nfc_tags__removed_at__isnull=True,
            )
        if include_category:
            word_q |= Q(category__name__icontains=word)
        icontains_q &= word_q

    if _is_postgres():
        return _build_fts_search(queryset, words, search_text, icontains_q)
    else:
        return _build_icontains_search(
            queryset, words, search_text, icontains_q
        )
