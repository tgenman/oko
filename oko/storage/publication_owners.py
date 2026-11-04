"""SQL for selecting primary publication owners, including pending cleanup."""


def owned_ids(feed_condition: str) -> str:
    """Select owner IDs; feed_condition is an internal SQL comparison suffix.

    Each branch uses the caller's feed expression independently, so placeholders
    in it must be supplied twice. A surviving primary always wins over cleanup.
    """
    return f"""SELECT p.id FROM publications p WHERE p.feed_id {feed_condition}
        UNION ALL
        SELECT p.id FROM jobs j JOIN publications p ON p.id=j.item AND p.viewed=1
        WHERE j.kind='delete' AND (j.payload::jsonb->>'cleanup_feed') {feed_condition}
          AND p.feed_id IS NULL"""


def owner_expr(publication_alias: str) -> str:
    """SQL expression for one publication's primary or cleanup feed.

    The alias is supplied by the caller's own SQL, never by external input.
    """
    return f"""COALESCE({publication_alias}.feed_id,
        (SELECT j.payload::jsonb->>'cleanup_feed' FROM jobs j
         WHERE j.kind='delete' AND j.item={publication_alias}.id AND {publication_alias}.viewed=1))"""
