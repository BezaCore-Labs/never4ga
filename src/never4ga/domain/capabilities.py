"""Backend and provider capability vocabularies.

core/06 section 21 and section 23: core behaviour checks declared capabilities
instead of hard-coding vendor names, so that a provider may expose extensions
without the portable core being reduced to a lowest common denominator.

Each vocabulary is deliberately a separate enum. core/06 section 15 warns that a
graph provider which also offers vector indexes must not let graph vendor
selection silently become vector vendor selection; keeping the vocabularies
disjoint makes that mistake a type error.
"""

from __future__ import annotations

import enum
from collections.abc import Collection

from never4ga.errors import CapabilityNotSupportedError

__all__ = [
    "GraphIndexCapability",
    "MemoryCapability",
    "MetadataIndexCapability",
    "TemporalGraphCapability",
    "TextIndexCapability",
    "VectorIndexCapability",
    "WorkManagementCapability",
    "require_capability",
]


class MetadataIndexCapability(enum.StrEnum):
    RANGE_FILTERS = "metadata_range_filters"
    SET_FILTERS = "metadata_set_filters"
    ORDERING = "metadata_ordering"


class TextIndexCapability(enum.StrEnum):
    EXACT_IDENTIFIERS = "exact_identifiers"
    PREFIX_TERMS = "prefix_terms"
    PHRASE_TERMS = "phrase_terms"
    FIELD_WEIGHTS = "field_weights"
    SCOPED_FILTERING = "text_scoped_filtering"


class VectorIndexCapability(enum.StrEnum):
    EXACT_KNN = "exact_knn"
    ANN = "ann"
    METADATA_FILTERING = "vector_metadata_filtering"
    MULTIPLE_VECTORS = "multiple_vectors"
    SPARSE_VECTORS = "sparse_vectors"
    HYBRID_NATIVE = "hybrid_native"
    QUANTIZATION = "quantization"


class GraphIndexCapability(enum.StrEnum):
    NEIGHBORS = "neighbors"
    RECURSIVE_TRAVERSAL = "recursive_traversal"
    SHORTEST_PATH = "shortest_path"
    GRAPH_ALGORITHMS = "graph_algorithms"


class WorkManagementCapability(enum.StrEnum):
    READ_WORK_ITEMS = "read_work_items"
    SEARCH_WORK_ITEMS = "search_work_items"
    RELATIONS = "work_item_relations"
    HIERARCHY = "work_item_hierarchy"
    CUSTOM_FIELDS = "work_item_custom_fields"
    SPRINTS = "work_item_sprints"
    # The three writes core/03 section 25 names `create_item`, `update_item`
    # and `comment`, spelled to this vocabulary's convention. A capability
    # belongs here only once code exercises it.
    CREATE_WORK_ITEM = "create_work_item"
    UPDATE_WORK_ITEM = "update_work_item"
    COMMENT_WORK_ITEM = "comment_work_item"
    # Reading relations is `RELATIONS`, above: the collection answering is what
    # proves it. Writing one is a separate fact and a separate link --
    # `addRelation` on a work package, which OpenProject omits for a token that
    # may not.
    RELATE_WORK_ITEMS = "relate_work_items"
    # Still deliberately absent: `delete`, `time_tracking` and `webhooks`.
    # details/openproject-adapter.md section 11 places webhooks after v0.1, and
    # nothing in the product deletes a work item or logs time -- a capability
    # nothing can act on is a claim, not a capability. Every work package
    # advertises `delete`, `logTime`, `addWatcher` and `addAttachment` links,
    # and a test asserts none of them becomes one.
    #
    # A provider reports what *its instance* can do rather than what the
    # product could: section 12 makes capability a function of version,
    # configured modules and the token's permissions, so this is a vocabulary
    # for answering that question, never a feature list.


class MemoryCapability(enum.StrEnum):
    SEARCH = "search"
    PROMPT_CONTEXT = "prompt_context"
    EPISODIC_INGEST = "episodic_ingest"
    SEMANTIC_INGEST = "semantic_ingest"
    ENTITY_MEMORY = "entity_memory"
    DELETE = "delete"
    EXPORT = "export"
    SELF_HOSTED = "self_hosted"


class TemporalGraphCapability(enum.StrEnum):
    TEMPORAL_FACTS = "temporal_facts"
    HISTORY = "history"
    GRAPH_SEARCH = "graph_search"
    VALID_AT = "valid_at"
    REBUILDABLE = "rebuildable"


def require_capability[C: enum.StrEnum](
    owner: str,
    declared: Collection[C],
    needed: C,
) -> None:
    """Raise unless ``owner`` declares ``needed``."""
    if needed not in declared:
        raise CapabilityNotSupportedError(f"{owner} does not support {needed.value!r}")
