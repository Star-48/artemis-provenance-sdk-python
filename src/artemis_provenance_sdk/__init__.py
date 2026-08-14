"""Artemis Provenance client SDK (C4). Thin by design — contains NO marking
logic; it serializes calls to the customer-deployed data plane (spec §9).
"""

from .client import Client, MarkedAsset, MarkingUnavailableError, VerifyResult

__version__ = "0.1.0"

__all__ = ["Client", "MarkedAsset", "MarkingUnavailableError", "VerifyResult", "__version__"]
