"""Shared API dependencies.

Authentication is opt-in by configuration but *mandatory in effect* for any
non-loopback deployment: this service starts containers and runs untrusted code,
so an unauthenticated public bind would be a remote code execution primitive.
``agentbench serve`` refuses such a bind, and :func:`require_write_access`
rejects mutating requests whenever a token is configured.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from agentbench.config import Settings, get_settings
from agentbench.db.session import session_scope


def get_db() -> Iterator[Session]:
    with session_scope() as session:
        yield session


DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


def require_write_access(
    authorization: Annotated[str | None, Header()] = None,
    settings: AppSettings = None,  # type: ignore[assignment]
) -> None:
    """Gate every mutating endpoint behind the bearer token, when configured."""
    settings = settings or get_settings()
    if not settings.api_token:
        return
    expected = f"Bearer {settings.api_token}"
    if authorization != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="a valid bearer token is required for mutating requests",
            headers={"WWW-Authenticate": "Bearer"},
        )


WriteAccess = Annotated[None, Depends(require_write_access)]


class Page:
    """Validated pagination parameters."""

    def __init__(
        self,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> None:
        self.limit = limit
        self.offset = offset

    def envelope(self, items: list[Any], total: int) -> dict[str, Any]:
        return {
            "items": items,
            "pagination": {
                "total": total,
                "limit": self.limit,
                "offset": self.offset,
                "has_more": self.offset + len(items) < total,
            },
        }


Pagination = Annotated[Page, Depends(Page)]


def not_found(what: str, identifier: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found: {identifier}"
    )


def bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
