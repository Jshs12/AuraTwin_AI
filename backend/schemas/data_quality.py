from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class QualityState(str, Enum):
    VALID = "VALID"
    STALE = "STALE"
    MISSING = "MISSING"
    INVALID = "INVALID"
    OUT_OF_RANGE = "OUT_OF_RANGE"


class SignalQualityAssessment(BaseModel):
    signal: str
    state: QualityState
    source: str = "unknown"
    observation_timestamp: Optional[datetime] = None
    simulated: bool = False
    reason_code: Optional[str] = None


class ZoneDataQualityReport(BaseModel):
    signals: dict[str, SignalQualityAssessment] = Field(default_factory=dict)

    def critical_failures(self, critical_signals: tuple[str, ...]) -> dict[str, SignalQualityAssessment]:
        return {
            name: self.signals[name]
            for name in critical_signals
            if name not in self.signals or self.signals[name].state != QualityState.VALID
        }
