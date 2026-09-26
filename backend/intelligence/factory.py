import os
from collections.abc import Mapping

from backend.intelligence.providers import IntelligenceProvider, MockIntelligenceProvider


def intelligence_provider_from_environment(environ: Mapping[str, str] | None = None) -> IntelligenceProvider:
    env = environ if environ is not None else os.environ
    mode = env.get("INTELLIGENCE_PROVIDER", "mock").strip().lower()
    if mode == "lyzr":
        # Construction is safe even when credentials are absent; the first request
        # raises a sanitized provider error and RecommendationWorkflow falls back.
        from backend.integrations.lyzr.provider import LyzrIntelligenceProvider
        return LyzrIntelligenceProvider.from_environment(env)
    if mode != "mock":
        print("[WARNING] Unsupported INTELLIGENCE_PROVIDER; using mock provider.")
    return MockIntelligenceProvider()
