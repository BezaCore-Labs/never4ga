"""The local HTTP API (core/05 sections 12 and 13).

One of three interfaces over the same application services. This package is the
only place `fastapi` and `pydantic` may be imported: core/05 section 15 keeps
business logic out of the interfaces, and confining the models is what stops
one of them drifting into the role of domain model.
"""

from __future__ import annotations

from never4ga.api.app import API_PREFIX, API_VERSION, create_app

__all__ = ["API_PREFIX", "API_VERSION", "create_app"]
