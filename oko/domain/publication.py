"""Publication identity and ownership violations."""


class PublicationOwnershipConflict(ValueError):
    """An external publication was discovered with an incompatible owner."""

    def __init__(self, *, platform: str, external_id: str, publication_id: str,
                 current_feed: str | None, discovered_feed: str) -> None:
        self.platform = platform
        self.external_id = external_id
        self.publication_id = publication_id
        self.current_feed = current_feed
        self.discovered_feed = discovered_feed
        super().__init__(
            f'Publication ownership conflict: platform={platform!r}, external_id={external_id!r}, '
            f'publication={publication_id!r}, current_feed={current_feed!r}, discovered_feed={discovered_feed!r}'
        )


class PublicationIdentityConflict(ValueError):
    """Source descriptions disagree about a saved publication's external key."""

    def __init__(self, *, publication_id: str, old_platform: str, old_external_id: str,
                 new_platform: str, new_external_id: str) -> None:
        super().__init__(
            f'Publication identity conflict: publication={publication_id!r}, '
            f'existing=({old_platform!r}, {old_external_id!r}), '
            f'incoming=({new_platform!r}, {new_external_id!r})'
        )
