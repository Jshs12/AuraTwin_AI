"""Lyzr Agent API v3 adapter for the advisory intelligence contract."""

import json
import math
import os
import uuid
from datetime import datetime
from typing import Any, Callable, Mapping, Optional
from backend.core.time import utc_now

import httpx
from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError

from backend.intelligence.providers import IntelligenceProvider
from backend.intelligence.schemas import IntelligenceContext, IntelligenceRecommendation


class LyzrProviderError(RuntimeError):
    """Safe, credential-free provider failure message."""


class _LyzrAdvisoryPayload(BaseModel):
    """Only model-generated fields; adapter metadata is supplied locally."""

    model_config = ConfigDict(extra="forbid", strict=True)

    zone_id: StrictStr
    recommended_setpoint: Any
    rationale: StrictStr
    confidence: Any
    action_type: StrictStr


class LyzrIntelligenceProvider(IntelligenceProvider):
    provider_name = "lyzr"
    DEFAULT_API_URL = "https://agent-prod.studio.lyzr.ai/v3/inference/chat/"
    DEFAULT_TIMEOUT_SECONDS = 8.0
    SERVICE_USER_ID = "auratwin-service"

    def __init__(
        self,
        api_key: Optional[str],
        agent_id: Optional[str],
        *,
        user_id: str = SERVICE_USER_ID,
        api_url: str = DEFAULT_API_URL,
        timeout_seconds: float | str = DEFAULT_TIMEOUT_SECONDS,
        http_post: Optional[Callable[..., Any]] = None,
    ):
        self._api_key = api_key.strip() if api_key else None
        self.agent_id = agent_id.strip() if agent_id else None
        self.user_id = user_id.strip()
        self.api_url = api_url.strip()
        self.configuration_error: Optional[str] = None
        try:
            parsed_timeout = float(timeout_seconds)
        except (TypeError, ValueError):
            parsed_timeout = self.DEFAULT_TIMEOUT_SECONDS
            self.configuration_error = "LYZR_TIMEOUT_SECONDS is invalid."
        if not math.isfinite(parsed_timeout) or parsed_timeout <= 0:
            parsed_timeout = self.DEFAULT_TIMEOUT_SECONDS
            self.configuration_error = "LYZR_TIMEOUT_SECONDS is invalid."
        self.timeout_seconds = parsed_timeout
        self._http_post = http_post or httpx.post

    @classmethod
    def from_environment(cls, environ: Optional[Mapping[str, str]] = None) -> "LyzrIntelligenceProvider":
        env = environ if environ is not None else os.environ
        return cls(
            api_key=env.get("LYZR_API_KEY"),
            agent_id=env.get("LYZR_AGENT_ID"),
            user_id=env.get("LYZR_USER_ID", cls.SERVICE_USER_ID),
            api_url=env.get("LYZR_API_URL", cls.DEFAULT_API_URL),
            timeout_seconds=env.get("LYZR_TIMEOUT_SECONDS", str(cls.DEFAULT_TIMEOUT_SECONDS)),
        )

    def generate_recommendation(self, context: IntelligenceContext) -> IntelligenceRecommendation:
        if self.configuration_error:
            raise LyzrProviderError(self.configuration_error)
        if not self._api_key:
            raise LyzrProviderError("Lyzr API key is not configured.")
        if not self.agent_id:
            raise LyzrProviderError("Lyzr agent ID is not configured.")

        message = json.dumps(
            {
                "instructions": (
                    "Return exactly one JSON object and no markdown. Give an advisory setpoint only; "
                    "do not issue commands or call tools. Use this schema: "
                    '{"zone_id": string, "recommended_setpoint": number, "rationale": string, '
                    '"confidence": number from 0 to 1, "action_type": "setpoint_adjustment"}. '
                    "Use only the supplied context. Respect its comfort limits and current conditions. "
                    "Do not claim energy savings or invent historical context."
                ),
                "context": context.model_dump(mode="json"),
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "x-api-key": self._api_key,
        }
        payload = {
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "session_id": str(uuid.uuid4()),
            "message": message,
            "system_prompt_variables": {},
            "filter_variables": {},
            "features": [],
        }

        try:
            response = self._http_post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException:
            raise LyzrProviderError("Lyzr request timed out.") from None
        except httpx.HTTPError:
            raise LyzrProviderError("Lyzr request failed.") from None
        except Exception:
            # Do not propagate arbitrary transport errors that could contain headers.
            raise LyzrProviderError("Lyzr request failed.") from None

        if not 200 <= response.status_code < 300:
            raise LyzrProviderError(f"Lyzr service returned HTTP {response.status_code}.")
        try:
            response_body = response.json()
        except Exception:
            raise LyzrProviderError("Lyzr returned a non-JSON API response.") from None
        if not isinstance(response_body, dict) or not isinstance(response_body.get("response"), str):
            raise LyzrProviderError("Lyzr API response is missing its structured response text.")
        try:
            decoded = json.loads(response_body["response"])
            advisory = _LyzrAdvisoryPayload.model_validate(decoded)
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError):
            raise LyzrProviderError("Lyzr returned malformed advisory JSON.") from None

        if advisory.zone_id != context.zone_id:
            raise LyzrProviderError("Lyzr recommendation refers to a different zone.")
        if advisory.action_type != "setpoint_adjustment":
            raise LyzrProviderError("Lyzr recommendation contains an unsupported action.")
        if not advisory.rationale.strip():
            raise LyzrProviderError("Lyzr recommendation rationale is empty.")
        if isinstance(advisory.recommended_setpoint, bool) or not isinstance(advisory.recommended_setpoint, (int, float)):
            raise LyzrProviderError("Lyzr setpoint is not numeric.")
        if not math.isfinite(float(advisory.recommended_setpoint)):
            raise LyzrProviderError("Lyzr setpoint is not finite.")
        if isinstance(advisory.confidence, bool) or not isinstance(advisory.confidence, (int, float)):
            raise LyzrProviderError("Lyzr confidence is not numeric.")
        confidence = float(advisory.confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise LyzrProviderError("Lyzr confidence is outside the valid range.")

        return IntelligenceRecommendation(
            zone_id=advisory.zone_id,
            recommended_setpoint=float(advisory.recommended_setpoint),
            rationale=advisory.rationale.strip(),
            confidence=confidence,
            provider=self.provider_name,
            model_source="lyzr_agent_api_v3",
            timestamp=utc_now(),
            context_reference={"schema_version": context.schema_version, "zone_id": context.zone_id},
            action_type=advisory.action_type,
        )
