from pydantic import BaseModel, Field
from datetime import datetime
from backend.core.time import utc_now

class AIInsight(BaseModel):
    query: str = Field(..., description="User's query or prompt")
    insight: str = Field(..., description="Reasoning and response from the AI")
    data_sources_used: list[str] = Field(default_factory=list, description="List of tools/functions called")
    timestamp: datetime = Field(default_factory=utc_now)

class AutomationEvent(BaseModel):
    event_id: str = Field(..., description="Unique event identifier")
    workflow_name: str = Field(..., description="Name of the n8n workflow triggered")
    trigger_source: str = Field(..., description="Source that triggered the workflow")
    payload: dict = Field(default_factory=dict, description="Data sent to the workflow")
    timestamp: datetime = Field(default_factory=utc_now)
