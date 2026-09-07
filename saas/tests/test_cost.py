from src.agent.cost import compute_cost_usd


def test_compute_cost_usd_matches_pricing_table(config):
    usage = {"input_tokens": 1000, "output_tokens": 500}
    cost = compute_cost_usd("claude-sonnet-5", usage, config.pricing)
    rate = config.pricing.models["claude-sonnet-5"]
    expected = (1000 * rate.input_per_mtok + 500 * rate.output_per_mtok) / 1_000_000
    assert cost == expected


def test_compute_cost_usd_unknown_model_returns_zero(config):
    cost = compute_cost_usd("unknown-model-xyz", {"input_tokens": 1000, "output_tokens": 500}, config.pricing)
    assert cost == 0.0
