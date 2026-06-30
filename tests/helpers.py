"""Shared test helpers."""
import app.services.repositories as repo_svc


def ensure_repo(session, code="TEST", institution=None):
    """Get-or-create a repository for ``code`` and return its id.

    Specimens reference their owning collection by ``repository_id`` (FK, #75), so
    every test that creates a CollectionObject needs a repository to point at.
    """
    return repo_svc.resolve_id(session, collection_code=code, institution_code=institution)
