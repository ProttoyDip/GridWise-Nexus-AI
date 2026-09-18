"""GET /llm/status — reports which LLM provider(s)/model(s) are configured.

Purely informational and additive: it is not part of the judge-fixed
/optimize-energy request/response schema, so it is safe to extend
without risking contract validation on the judge side. Never returns
API keys.
"""

from fastapi import APIRouter

from app.llm.provider import LLMProviderError, get_provider_chain

router = APIRouter()


@router.get("/llm/status")
def get_llm_status() -> dict:
    try:
        chain = get_provider_chain()
    except LLMProviderError as exc:
        return {"configured": False, "reason": str(exc), "providers": []}

    return {
        "configured": True,
        "primary": chain[0][0],
        "providers": [{"provider": name, "model": provider.model} for name, provider in chain],
    }
