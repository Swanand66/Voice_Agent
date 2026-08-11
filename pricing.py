"""Provider pricing rates for cost calculation.

Rates are per unit — update whenever providers change prices. All values are
USD. VERIFY these against each provider's pricing page before trusting the
numbers displayed on the dashboard; they are seeded with reasonable defaults
but not guaranteed to be current.

Processor name matching is substring-based so it survives Pipecat suffixing
class names (e.g. "GroqLLMService#0"). Keep PRICING keys as class basenames.
"""

PRICING = {
    # Groq — https://groq.com/pricing/
    # llama-3.3-70b-versatile
    "GroqLLMService": {
        "prompt_per_1m_tokens": 0.59,      # TODO verify
        "completion_per_1m_tokens": 0.79,  # TODO verify
    },
    # Soniox — https://soniox.com/pricing/
    "SonioxSTTService": {
        "per_audio_second": 0.0000833,  # ~$0.30/hr — TODO verify
    },
    "SonioxTTSService": {
        "per_1k_chars": 0.015,  # TODO verify
    },
}


def _rates_for(processor: str) -> dict:
    """Find pricing entry whose key appears in the processor name.

    Pipecat may report processor as 'GroqLLMService', 'GroqLLMService#0',
    or a fully-qualified name. Substring match keeps us flexible.
    """
    for key, rates in PRICING.items():
        if key in processor:
            return rates
    return {}


def calc_llm_cost(processor: str, prompt_tokens: int, completion_tokens: int) -> float:
    rates = _rates_for(processor)
    p = rates.get("prompt_per_1m_tokens", 0.0)
    c = rates.get("completion_per_1m_tokens", 0.0)
    return (prompt_tokens / 1_000_000) * p + (completion_tokens / 1_000_000) * c


def calc_stt_cost(processor: str, audio_seconds: float) -> float:
    rates = _rates_for(processor)
    return audio_seconds * rates.get("per_audio_second", 0.0)


def calc_tts_cost(processor: str, characters: int) -> float:
    rates = _rates_for(processor)
    return (characters / 1000) * rates.get("per_1k_chars", 0.0)
