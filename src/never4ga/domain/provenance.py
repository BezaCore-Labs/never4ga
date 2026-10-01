"""Acquisition provenance.

core/07 section 12 requires every Context Pack item to identify how it arrived,
and section 8 requires mechanically selected material to stay distinguishable
from model-derived inference. A reason is therefore a code plus the acquisition
stage that produced it, not a free-form sentence.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Final

__all__ = ["AcquisitionReason", "AcquisitionStage", "ReasonCode"]


class AcquisitionStage(enum.StrEnum):
    """The stages of core/07 section 3, in order."""

    SCOPE = "scope"
    METADATA = "metadata"
    STRUCTURE = "structure"
    LEXICAL = "lexical"
    RELATION = "relation"
    EXTERNAL_STATE = "external_state"
    SEMANTIC = "semantic"
    ENRICHMENT = "enrichment"


#: Stages that require neither an embedding model nor an LLM. core/07 section 16
#: requires the system to stay useful when only these are available.
MECHANICAL_STAGES: Final = frozenset(
    {
        AcquisitionStage.SCOPE,
        AcquisitionStage.METADATA,
        AcquisitionStage.STRUCTURE,
        AcquisitionStage.LEXICAL,
        AcquisitionStage.RELATION,
        AcquisitionStage.EXTERNAL_STATE,
    }
)


class ReasonCode(enum.StrEnum):
    """The reason vocabulary named in core/07 section 12.

    Unknown codes are permitted -- future signal providers add their own -- but
    they must declare their stage explicitly.
    """

    WORKSPACE_REQUIRED = "workspace_required"
    PARENT_WORKSPACE = "parent_workspace"
    METADATA_FILTER = "metadata_filter"
    STRUCTURAL_LOCATION = "structural_location"
    EXACT_IDENTIFIER_MATCH = "exact_identifier_match"
    FTS_RANK = "fts_rank"
    RELATION_DEPTH = "relation_depth"
    RECENT_ACTIVITY = "recent_activity"
    FRESHNESS = "freshness"
    GIT_CHANGED_FILE = "git_changed_file"
    WORK_ITEM_CURRENT = "work_item_current"
    EXTERNAL_MEMORY_CANDIDATE = "external_memory_candidate"
    VECTOR_RANK = "vector_rank"
    LLM_RERANK = "llm_rerank"
    #: A note in registered foreign material, found by the lexical lane and
    #: held by its path rather than an identity (core/01 section 1).
    FOREIGN_MATERIAL = "foreign_material"


_STAGE_BY_CODE: Final[dict[str, AcquisitionStage]] = {
    ReasonCode.WORKSPACE_REQUIRED: AcquisitionStage.SCOPE,
    ReasonCode.PARENT_WORKSPACE: AcquisitionStage.SCOPE,
    ReasonCode.METADATA_FILTER: AcquisitionStage.METADATA,
    ReasonCode.FRESHNESS: AcquisitionStage.METADATA,
    ReasonCode.STRUCTURAL_LOCATION: AcquisitionStage.STRUCTURE,
    ReasonCode.EXACT_IDENTIFIER_MATCH: AcquisitionStage.LEXICAL,
    ReasonCode.FTS_RANK: AcquisitionStage.LEXICAL,
    ReasonCode.FOREIGN_MATERIAL: AcquisitionStage.LEXICAL,
    ReasonCode.RELATION_DEPTH: AcquisitionStage.RELATION,
    ReasonCode.RECENT_ACTIVITY: AcquisitionStage.EXTERNAL_STATE,
    ReasonCode.GIT_CHANGED_FILE: AcquisitionStage.EXTERNAL_STATE,
    ReasonCode.WORK_ITEM_CURRENT: AcquisitionStage.EXTERNAL_STATE,
    ReasonCode.EXTERNAL_MEMORY_CANDIDATE: AcquisitionStage.EXTERNAL_STATE,
    ReasonCode.VECTOR_RANK: AcquisitionStage.SEMANTIC,
    ReasonCode.LLM_RERANK: AcquisitionStage.ENRICHMENT,
}


@dataclass(frozen=True, slots=True)
class AcquisitionReason:
    """Why a candidate or Context Pack item is present."""

    code: str
    stage: AcquisitionStage
    detail: str = ""

    @classmethod
    def of(
        cls,
        code: str,
        *,
        stage: AcquisitionStage | None = None,
        detail: str = "",
    ) -> AcquisitionReason:
        resolved = stage if stage is not None else _STAGE_BY_CODE.get(code)
        if resolved is None:
            raise ValueError(
                f"unknown acquisition reason {code!r} must declare its stage explicitly"
            )
        return cls(code=str(code), stage=resolved, detail=detail)

    @property
    def is_mechanical(self) -> bool:
        """True when no embedding model or LLM was involved."""
        return self.stage in MECHANICAL_STAGES

    def __str__(self) -> str:
        return f"{self.code}({self.detail})" if self.detail else self.code
