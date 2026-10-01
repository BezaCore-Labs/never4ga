"""Application services.

The behaviour behind every interface. core/05 section 15 and
details/api-cli-mcp-contract.md section 1 require the CLI, the HTTP API and the
MCP server to be thin clients over these -- no interface owns business logic of
its own.

Services depend on ports, never on adapters. Choosing a concrete adapter is a
composition root's job.
"""

from __future__ import annotations

from never4ga.services.authoring import (
    NEVER4GA_ACTOR,
    OWNER_ACTOR,
    Clock,
    build_concept,
    format_timestamp,
    utc_now,
)
from never4ga.services.capture import Captured, CaptureError, CaptureService
from never4ga.services.context import ContextService
from never4ga.services.creation import (
    ConceptCreationError,
    ContentService,
    Created,
    directory_name_for,
    filename_for,
)
from never4ga.services.credentials import (
    LOCAL_API_CREDENTIAL,
    credential_matches,
    ensure_local_credential,
)
from never4ga.services.doctor import Diagnosis, Doctor, Finding
from never4ga.services.indexing import (
    IndexHealth,
    IndexRun,
    IndexService,
    UnresolvedRelation,
)
from never4ga.services.search import (
    ConceptView,
    SearchRequest,
    SearchResponse,
    SearchResult,
    SearchService,
    foreign_note_at,
    not_a_concept,
    parse_query,
)
from never4ga.services.session import SessionFactory, VaultSession
from never4ga.services.validation import validate_vault
from never4ga.services.vault import InitializationResult, VaultInitializer
from never4ga.services.workspaces import ScopeResolution, WorkspaceService, scope_refusal

__all__ = [
    "LOCAL_API_CREDENTIAL",
    "NEVER4GA_ACTOR",
    "OWNER_ACTOR",
    "CaptureError",
    "CaptureService",
    "Captured",
    "Clock",
    "ConceptCreationError",
    "ConceptView",
    "ContentService",
    "ContextService",
    "Created",
    "Diagnosis",
    "Doctor",
    "Finding",
    "IndexHealth",
    "IndexRun",
    "IndexService",
    "InitializationResult",
    "ScopeResolution",
    "SearchRequest",
    "SearchResponse",
    "SearchResult",
    "SearchService",
    "SessionFactory",
    "UnresolvedRelation",
    "VaultInitializer",
    "VaultSession",
    "WorkspaceService",
    "build_concept",
    "credential_matches",
    "directory_name_for",
    "ensure_local_credential",
    "filename_for",
    "foreign_note_at",
    "format_timestamp",
    "not_a_concept",
    "parse_query",
    "scope_refusal",
    "utc_now",
    "validate_vault",
]
