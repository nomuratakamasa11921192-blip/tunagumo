from src.agent.config_models import Pricing


def compute_cost_usd(model: str, usage: dict, pricing: Pricing) -> float:
    """usage(input_tokens/output_tokens/cache_write_tokens/cache_read_tokens)と
    設定側の単価表(百万トークンあたりUSD)から実測コストを計算する(4-1)。

    単価表に無いモデルは0として扱う(単価未設定=課金額不明を無言で握りつぶさないよう、
    呼び出し側でstep_historyに記録することを想定)。
    """
    rate = pricing.models.get(model)
    if rate is None:
        return 0.0

    input_tokens = usage.get("input_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0
    cache_write_tokens = usage.get("cache_write_tokens", 0) or 0
    cache_read_tokens = usage.get("cache_read_tokens", 0) or 0

    return (
        input_tokens * rate.input_per_mtok
        + output_tokens * rate.output_per_mtok
        + cache_write_tokens * rate.cache_write_per_mtok
        + cache_read_tokens * rate.cache_read_per_mtok
    ) / 1_000_000
