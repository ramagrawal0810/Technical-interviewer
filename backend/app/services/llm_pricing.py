"""Centralized LLM pricing. The only place price constants live.

Token counts come from the provider's API usage fields (stored in LLMUsageLog); only the
price is assumed, so every cost derived here is an ESTIMATE.

Prices are USD per 1,000,000 tokens as (input, output). Verify them against the provider's
current pricing page and update this table when they change. Models not listed are NOT
priced (cost is None) rather than guessed.
"""

from typing import Optional

MODEL_PRICING_PER_1M_TOKENS = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-4": (30.00, 60.00),
    "gpt-3.5-turbo": (0.50, 1.50),
}

# Longest prefix first, so "gpt-4o-mini-2024-07-18" matches "gpt-4o-mini", not "gpt-4o" or "gpt-4"
_PRICED_PREFIXES = sorted(MODEL_PRICING_PER_1M_TOKENS, key=len, reverse=True)


def get_model_pricing(model: Optional[str]) -> Optional[tuple]:
    if not model:
        return None
    name = model.strip().lower()
    for prefix in _PRICED_PREFIXES:
        if name.startswith(prefix):
            return MODEL_PRICING_PER_1M_TOKENS[prefix]
    return None


def estimate_cost_usd(
    model: Optional[str],
    prompt_tokens: Optional[int],
    completion_tokens: Optional[int],
) -> Optional[float]:
    """Estimated USD cost from actual token counts, or None if the model is unpriced or tokens are missing."""
    pricing = get_model_pricing(model)
    if pricing is None or prompt_tokens is None or completion_tokens is None:
        return None
    input_price, output_price = pricing
    return round((prompt_tokens * input_price + completion_tokens * output_price) / 1_000_000, 6)
