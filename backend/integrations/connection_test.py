"""Connection-test boundary that deliberately performs no network I/O in Phase 11.5."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ConnectionTestResult:
    result: str
    simulated: bool
    connection_established: bool
    message: str


class IntegrationConnectionTester(Protocol):
    def test(self, *, status: str, configuration: dict) -> ConnectionTestResult: ...


class ConfigurationOnlyTester:
    def test(self, *, status: str, configuration: dict) -> ConnectionTestResult:
        valid = status == "CONFIGURED" and bool(configuration)
        return ConnectionTestResult(
            result="CONFIGURATION_VALID" if valid else "CONFIGURATION_INCOMPLETE",
            simulated=True,
            connection_established=False,
            message="Configuration fields were checked locally; no hardware or network connection was attempted.",
        )
