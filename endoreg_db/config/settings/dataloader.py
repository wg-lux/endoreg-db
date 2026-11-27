"""Pinned loader identities; bump the version when publishing changed data.

Successful imports are tracked by database receipts, never process-local flags.
Clinical commands still require an explicit package; test helpers select the
clinical fixture package here.
"""

from lx_dtypes.models.contracts.knowledge_base import KnowledgeBaseIdentity

APPLICATION_PACKAGE = KnowledgeBaseIdentity(
    knowledge_base_module="endoreg_reference", knowledge_base_version="2.0.0"
)
CLINICAL_PACKAGE = KnowledgeBaseIdentity(
    knowledge_base_module="coloreg", knowledge_base_version="0.3.0"
)
