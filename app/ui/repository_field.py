"""Collection picker — choose (or add by name) a collection from the vocabulary.

build_repository_field(session_factory, label, ...) -> dict

The one widget for "which collection holds this specimen", shared by Digitize →
"other collection" and the Records re-home field so the two cannot drift. It is the
same custom dropdown as every other controlled-vocabulary field (``build_vocab_field``:
existing matches + ``✚ add <typed name>``, no free-text escape) — only the backing
table differs, so this module is just the adapter that makes the ``repository`` table
look like a vocabulary to it.

A collection is identified by its **name**; its ``collectionCode`` (if it has one) is
shown as the pill beside the name. A collection added here gets a name and nothing
else — codes, institution and contact person are added in Controlled Vocabularies.

commit(session) returns the ``repository_id`` (the FK to store); call it inside the
save transaction, exactly like a person / vocab field.
"""
from __future__ import annotations

import app.services.repositories as repo_svc
from app.ui.vocab_field import build_vocab_field


class _RepositoryVocab:
    """The two methods ``build_vocab_field`` needs, answered from ``repository``."""

    def __init__(self, *, exclude_default: bool):
        self.exclude_default = exclude_default

    def entries(self, session) -> list[tuple[str, str | None]]:
        return [
            (r.collection_full_name, r.collection_code)
            for r in repo_svc.list_repositories(session)
            if not (self.exclude_default and r.is_default)
        ]

    def get_or_create(self, session, name: str, *, code: str | None = None):
        # The name alone identifies a collection (UNIQUE); `code` is display only.
        repo = repo_svc.get_or_create_by_name(session, name)
        if self.exclude_default and repo.is_default:
            raise ValueError(
                f"{repo.collection_full_name!r} is your own collection — its specimens "
                "are entered in the standard Digitize mode, with a reserved identifier.")
        return repo


def build_repository_field(
    session_factory,
    label: str = "Collection",
    *,
    exclude_default: bool = False,
    initial_value: str | None = None,
    on_change=None,
    classes: str = "flex-1",
) -> dict:
    """Render the collection picker. ``exclude_default`` hides the user's own collection
    (and refuses it at commit) — for the "other collection" entry mode. Returns the
    ``build_vocab_field`` handle (get_value / set_value / commit / refresh / …)."""
    return build_vocab_field(
        session_factory, _RepositoryVocab(exclude_default=exclude_default), label,
        initial_value=initial_value, on_change=on_change, classes=classes,
    )
