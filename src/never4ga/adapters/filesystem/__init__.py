"""Filesystem adapter -- the canonical Markdown vault on disk.

This is the only package permitted to understand YAML. Everything
above it exchanges frontmatter as a plain ``Mapping``.
"""

from __future__ import annotations

from never4ga.adapters.filesystem.client_files import (
    ClientFileStoreError,
    FileSystemClientFileStore,
)
from never4ga.adapters.filesystem.deployments import DeploymentFile, DeploymentStoreError
from never4ga.adapters.filesystem.document_store import (
    FileSystemMarkdownStore,
    StoreProblem,
)
from never4ga.adapters.filesystem.extension_registry import (
    ExtensionRegistryError,
    ExtensionRegistryFile,
)
from never4ga.adapters.filesystem.frontmatter import (
    FrontmatterError,
    ParsedMarkdown,
    parse_document,
    render_document,
)
from never4ga.adapters.filesystem.repository_locator import GitRepositoryLocator
from never4ga.adapters.filesystem.secret_store import LocalSecretFileStore
from never4ga.adapters.filesystem.vault_files import FileSystemVaultFileStore
from never4ga.adapters.filesystem.workspace_mappings import (
    VaultWorkspaceMappings,
    WorkspaceMappingFile,
)

__all__ = [
    "ClientFileStoreError",
    "DeploymentFile",
    "DeploymentStoreError",
    "ExtensionRegistryError",
    "ExtensionRegistryFile",
    "FileSystemClientFileStore",
    "FileSystemMarkdownStore",
    "FileSystemVaultFileStore",
    "FrontmatterError",
    "GitRepositoryLocator",
    "LocalSecretFileStore",
    "ParsedMarkdown",
    "StoreProblem",
    "VaultWorkspaceMappings",
    "WorkspaceMappingFile",
    "parse_document",
    "render_document",
]
