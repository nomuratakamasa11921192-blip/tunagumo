"""Synthetic full-graph cost sample; no DB writes, delivery, approval or tenant data.

Preview: python -m scripts.benchmark_company_plan
Paid run (existing configured key): python -m scripts.benchmark_company_plan --run
Output contains synthetic drafts for quality review and usage-derived cost estimates.
The $1 conservative request envelope is not a provider-side billing limit.
"""

import argparse
import asyncio
import json
import os
import time
from types import SimpleNamespace

from openai import AsyncOpenAI

from scripts.compare_writing_models import CASES
from src.agent.config_loader import load_config
from src.agent.cost import compute_cost_usd
from src.agent.graph import build_graph
from src.agent.llm import StructuredLLM, _extract_usage
from src.agent.state import new_state
from src.core.config import active_llm_model, active_llm_model_light, settings


class BenchmarkStopped(Exception):
    pass


class Meter:
    """Meter every provider response, including structured-output validation retries.

    Serialize calls and reserve UTF-8 byte count + overhead as an intentionally
    conservative input-token estimate. Keep that reservation for the whole run.
    Stop after an unknown outcome instead of retrying potentially billed work.
    """

    def __init__(self, create, pricing, cap=1.0):
        self.create = create
        self.pricing = pricing
        self.cap = cap
        self.reserved = 0.0
        self.calls = []
        self.unknown_usage = False
        self.lock = asyncio.Lock()

    async def __call__(self, **kwargs):
        async with self.lock:
            model = kwargs["model"]
            rate = self.pricing.models.get(model)
            if self.unknown_usage or rate is None:
                raise BenchmarkStopped("Unknown usage or model pricing")
            payload_bytes = len(json.dumps(kwargs, ensure_ascii=False).encode("utf-8"))
            input_rate = max(rate.input_per_mtok, rate.cache_write_per_mtok, rate.cache_read_per_mtok)
            envelope = ((payload_bytes + 2048) * input_rate
                        + kwargs["max_completion_tokens"] * rate.output_per_mtok) / 1_000_000
            if self.reserved + envelope > self.cap:
                raise BenchmarkStopped("Conservative request budget reached")
            self.reserved += envelope
            try:
                response = await self.create(**kwargs)
                if response.usage is None:
                    raise BenchmarkStopped("Provider did not return usage")
                usage = _extract_usage(response)
            except Exception:
                self.unknown_usage = True
                raise BenchmarkStopped("Provider outcome unknown; no further calls") from None
            cost = compute_cost_usd(model, usage, self.pricing)
            self.calls.append({"model": model, "usage": usage, "estimated_usd": cost})
            if cost > envelope:
                self.unknown_usage = True
                raise BenchmarkStopped("Usage exceeded conservative estimate")
            return response


async def run():
    if settings.llm_provider != "openai" or not settings.openai_api_key:
        raise BenchmarkStopped("Existing OpenAI configuration is required")
    # config_loader substitutes environment variables, while Settings also reads .env.
    os.environ["LLM_MODEL"] = active_llm_model()
    os.environ["LLM_MODEL_LIGHT"] = active_llm_model_light()
    config = load_config("config/real_estate.yaml")
    config.limits.max_budget_usd = 1.0
    config.limits.max_total_steps = 12
    config.limits.max_parallel_depts = 1
    for department in config.departments.values():
        department.tools = []
    async with AsyncOpenAI(api_key=settings.openai_api_key, timeout=60, max_retries=0) as client:
        meter = Meter(client.chat.completions.create, config.pricing)
        proxy = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=meter)))
        llm = StructuredLLM(client=proxy, provider="openai")
        reports = []
        for index, case in enumerate(CASES, 1):
            if meter.unknown_usage:
                break
            # MemorySaver only; no repository/document retrieval or approval resume.
            graph = build_graph(config, llm=llm)
            message = ("架空の会社・物件を使う検証です。外部送信せず下書きを作成してください。"
                       "明記のない条件は推測せず要確認としてください。\n" + case["request"])
            state = new_state("synthetic-benchmark", str(index), "synthetic-user", message)
            start_call = len(meter.calls)
            started = time.monotonic()
            report = {"case": case["title"]}
            try:
                result = await graph.ainvoke(state, config={
                    "configurable": {"thread_id": f"synthetic-{index}"}, "recursion_limit": 50})
                report.update(status=result["status"], drafts=result.get("board", {}),
                              qa_verdict=result.get("qa_verdict"),
                              approval_summary=result.get("approval_summary", {}))
            except Exception as exc:
                # Avoid provider errors containing request data/credentials in the report.
                report.update(status="ERROR", error_type=type(exc).__name__)
            calls = meter.calls[start_call:]
            report.update(seconds=round(time.monotonic() - started, 2), calls=calls,
                          estimated_usd=sum(c["estimated_usd"] for c in calls))
            reports.append(report)
        return {"synthetic_only": True, "pricing_date": str(config.pricing.updated_at),
                "conservative_reserved_usd": meter.reserved,
                "usage_incomplete": meter.unknown_usage,
                "total_estimated_usd": sum(c["estimated_usd"] for c in meter.calls),
                "limits": "Text workflow only; no images, audio, RAG, delivery or monthly sufficiency guarantee",
                "cases": reports}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Use the existing paid API key")
    args = parser.parse_args()
    if not args.run:
        print(json.dumps({"mode": "preview", "api_calls": 0,
                          "cases": [c["title"] for c in CASES]}, ensure_ascii=False))
        return
    try:
        report = asyncio.run(run())
    except BenchmarkStopped as exc:
        raise SystemExit(str(exc)) from None
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
