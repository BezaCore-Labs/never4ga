"""Vault layout -- the structural rules of core/01.

Pure path logic: no filesystem, no YAML, no I/O. It answers questions like "which
workspace owns this path", "is this filename reserved", and "must this directory
stay flat", so that both placement validation (core/02 section 23) and the
services that create vault content agree on one set of rules.
"""

from __future__ import annotations

from never4ga.layout.structure import (
    ARCHIVE_SECTIONS,
    AREA_MANIFEST,
    CHILD_WORKSPACE_DIRECTORY,
    DOMAIN_REGISTRY,
    FOREIGN_FORMAT_DIRECTORIES,
    FOREIGN_MATERIAL_FIELD,
    HOME,
    INBOX_RESERVED_NAMES,
    INBOX_SCRATCHPAD_NAME,
    INTEGRATIONS_DIRECTORY,
    KNOWLEDGE_DIRECTORIES,
    RESERVED_INDEX,
    RESERVED_LOG,
    ROOT_INDEX,
    SYSTEM_DIRECTORIES,
    SYSTEM_MANIFEST,
    TOOL_CONVENTION_FILENAMES,
    WORKSPACE_BASE_DIRECTORIES,
    WORKSPACE_MANIFEST,
    DocumentRole,
    FlatArea,
    VaultRoot,
    flat_area_of,
    foreign_names,
    is_foreign_format,
    is_foreign_note,
    is_life_area_content,
    life_area_directory_of,
    registered_foreign_material,
    role_of,
    workspace_directory_of,
    workspace_section_of,
)

__all__ = [
    "ARCHIVE_SECTIONS",
    "AREA_MANIFEST",
    "CHILD_WORKSPACE_DIRECTORY",
    "DOMAIN_REGISTRY",
    "FOREIGN_FORMAT_DIRECTORIES",
    "FOREIGN_MATERIAL_FIELD",
    "HOME",
    "INBOX_RESERVED_NAMES",
    "INBOX_SCRATCHPAD_NAME",
    "INTEGRATIONS_DIRECTORY",
    "KNOWLEDGE_DIRECTORIES",
    "RESERVED_INDEX",
    "RESERVED_LOG",
    "ROOT_INDEX",
    "SYSTEM_DIRECTORIES",
    "SYSTEM_MANIFEST",
    "TOOL_CONVENTION_FILENAMES",
    "WORKSPACE_BASE_DIRECTORIES",
    "WORKSPACE_MANIFEST",
    "DocumentRole",
    "FlatArea",
    "VaultRoot",
    "flat_area_of",
    "foreign_names",
    "is_foreign_format",
    "is_foreign_note",
    "is_life_area_content",
    "life_area_directory_of",
    "registered_foreign_material",
    "role_of",
    "workspace_directory_of",
    "workspace_section_of",
]
