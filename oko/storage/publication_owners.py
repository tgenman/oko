"""SQL for selecting primary publication owners, including pending cleanup."""


def owned_ids(feed_condition: str) -> str:
    """Select owner IDs; feed_condition is an internal SQL comparison suffix.

    Each branch uses the caller's feed expression independently, so placeholders
    in it must be supplied twice. Cleanup Jobs supply owners after deletion.
    """
    return f"""SELECT p.id FROM publications p WHERE p.feed_id {feed_condition}
        UNION ALL
        SELECT j.item AS id FROM jobs j
        WHERE j.kind='delete' AND (j.payload::jsonb->>'cleanup_feed') {feed_condition}
          AND NOT EXISTS (SELECT 1 FROM publications p WHERE p.id=j.item)"""


def owner_expr(publication_alias: str) -> str:
    """SQL expression for one publication's primary feed.

    The alias is supplied by the caller's own SQL, never by external input.
    """
    return f"{publication_alias}.feed_id"
