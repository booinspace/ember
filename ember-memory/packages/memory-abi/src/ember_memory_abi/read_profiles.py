"""Optional diagnostics for a trusted bounded deep-read handle, without storage types."""

from typing import Protocol, runtime_checkable

from .common import ContractModel, NonnegativeInteger


class ReadUsage(ContractModel):
    """Counts represent actual materialized rows/statements, not similarity truth scores."""

    sql_statements: NonnegativeInteger = 0
    materialized_rows: NonnegativeInteger = 0
    materialized_bytes: NonnegativeInteger = 0
    limits_hit: tuple[str, ...] = ()


@runtime_checkable
class ReadUsageClient(Protocol):
    """Optional request-local counter snapshot; it neither changes admission nor grants."""

    def read_usage(self) -> ReadUsage:
        """Return bounded content-free counters without blocking native work."""
        ...
