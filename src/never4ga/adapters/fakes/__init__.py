"""In-memory fakes.

core/06 section 25 Tests A and C require that a fake backend can replace a real
one without touching higher-level code. These fakes are shipped rather than
confined to the test tree so that future backend adapters can be diffed against
a known-good reference implementation of each contract.
"""

from never4ga.adapters.fakes.agent_adapter import FakeAgentAdapter
from never4ga.adapters.fakes.document_store import InMemoryDocumentStore
from never4ga.adapters.fakes.extension_registry import InMemoryExtensionRegistry
from never4ga.adapters.fakes.graph_index import InMemoryGraphIndex
from never4ga.adapters.fakes.index_state import InMemoryIndexState
from never4ga.adapters.fakes.maintenance_findings import InMemoryMaintenanceFindings
from never4ga.adapters.fakes.memory import FakeMemoryAugmentor
from never4ga.adapters.fakes.metadata_index import InMemoryMetadataIndex
from never4ga.adapters.fakes.repository_locator import FakeRepositoryLocator
from never4ga.adapters.fakes.secret_store import InMemorySecretStore
from never4ga.adapters.fakes.service_manager import FakeServiceManager
from never4ga.adapters.fakes.text_index import InMemoryTextIndex
from never4ga.adapters.fakes.tracker_cache import InMemoryTrackerCache
from never4ga.adapters.fakes.vault_files import InMemoryVaultFileStore
from never4ga.adapters.fakes.work_management import (
    FakeWorkManagementProvider,
    FakeWorkManagementWriter,
)
from never4ga.adapters.fakes.workspace_mappings import InMemoryWorkspaceMappingStore

__all__ = [
    "FakeAgentAdapter",
    "FakeMemoryAugmentor",
    "FakeRepositoryLocator",
    "FakeServiceManager",
    "FakeWorkManagementProvider",
    "FakeWorkManagementWriter",
    "InMemoryDocumentStore",
    "InMemoryExtensionRegistry",
    "InMemoryGraphIndex",
    "InMemoryIndexState",
    "InMemoryMaintenanceFindings",
    "InMemoryMetadataIndex",
    "InMemorySecretStore",
    "InMemoryTextIndex",
    "InMemoryTrackerCache",
    "InMemoryVaultFileStore",
    "InMemoryWorkspaceMappingStore",
]
