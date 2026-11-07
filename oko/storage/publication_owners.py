"""SQL expressions for a publication's single feed owner."""


def owned_ids(feed_condition: str) -> str:
    return f"SELECT id FROM publications WHERE feed_id {feed_condition}"


def owner_expr(publication_alias: str) -> str:
    return f"{publication_alias}.feed_id"
