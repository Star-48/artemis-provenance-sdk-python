"""Artemis Provenance client SDK (C4). Thin by design — contains NO marking
logic; it serializes calls to the customer-deployed data plane (spec §9).
"""

from .client import (
    TEXT_VERDICTS,
    Client,
    MarkedAsset,
    MarkedText,
    MarkingFailedError,
    MarkingUnavailableError,
    VerifyResult,
    VerifyTextResult,
)

__version__ = "0.1.0"

__all__ = [
    "Client",
    "MarkedAsset",
    "MarkedText",
    "MarkingFailedError",
    "MarkingUnavailableError",
    "TEXT_VERDICTS",
    "VerifyResult",
    "VerifyTextResult",
    "__version__",
]
