"""Facts omitted by triage must still reach routing, writing and QA."""
import pytest
from langgraph.types import Command

from src.agent.graph import build_graph
from src.agent.schemas import ApprovalSummary, QaResult, SupervisorDecision, Triage
from src.agent.state import new_state
from tests.fakes import FakeLLM

pytestmark = pytest.mark.asyncio(loop_scope="session")

REQUEST = (
    "物件紹介文を200字で1案。管理費3,000円、42.5㎡、2016年築、"
    "宅配ボックス、ペット相談可（小型犬1匹まで）。他の条件は追加しない。"
)


def queue_work(llm):
    llm.queue_structured(SupervisorDecision(
        verdict="ASSIGN", target_depts=["copy_dept"], instruction="紹介文を作る", reason="単一部署"
    ))
    llm.queue_text("## 物件紹介\n管理費3,000円、42.5㎡、宅配ボックスあり。")
    llm.queue_structured(QaResult(fact_check=[], quality_findings=[]))
    llm.queue_structured(SupervisorDecision(verdict="SUMMARIZE", reason="QA合格"))
    llm.queue_structured(ApprovalSummary(headline="紹介文", qa_result="合格"))


def stage_calls(llm, stage):
    if stage == "writing":
        return llm.text_calls
    schema = {"routing": SupervisorDecision, "qa": QaResult, "approval": ApprovalSummary}[stage]
    return [c for c in llm.structured_calls if c["output_model"] is schema]


@pytest.mark.parametrize("stage", ["routing", "writing", "qa", "approval"])
async def test_original_request_survives_lossy_triage_summary(config, stage):
    llm = FakeLLM()
    llm.queue_structured(Triage(intent="WORK_REQUEST", goal="物件紹介文を作成する", reason="業務依頼"))
    queue_work(llm)
    graph = build_graph(config, llm=llm)
    result = await graph.ainvoke(new_state("tenant-a", "facts", "user", REQUEST),
                                config={"configurable": {"thread_id": "tenant-a:facts"}})
    assert result["status"] == "AWAITING_APPROVAL"
    calls = stage_calls(llm, stage)
    assert calls
    for call in calls:
        assert REQUEST in call["user_message"]
        assert REQUEST not in call["system_prompt"]


async def test_clarified_facts_and_qa_verdict_reach_later_stages(config):
    llm = FakeLLM()
    llm.queue_structured(Triage(intent="WORK_REQUEST", goal="物件紹介文を作成する", reason="業務依頼"))
    llm.queue_structured(SupervisorDecision(verdict="CLARIFY", missing_info=["賃料"], reason="不足"))
    graph = build_graph(config, llm=llm)
    cfg = {"configurable": {"thread_id": "tenant-a:clarified-facts"}}
    first = await graph.ainvoke(new_state("tenant-a", "clarified-facts", "user", REQUEST), config=cfg)
    assert first["status"] == "CLARIFYING"
    llm.structured_calls.clear()
    queue_work(llm)
    result = await graph.ainvoke(Command(resume={"賃料": "6.8万円"}), config=cfg)
    assert result["status"] == "AWAITING_APPROVAL"
    for call in llm.structured_calls + llm.text_calls:
        assert REQUEST in call["user_message"]
        assert "賃料: 6.8万円" in call["user_message"]
    assert "【QA判定】PASS" in stage_calls(llm, "routing")[-1]["user_message"]


async def test_available_retrieval_is_explained_without_passing_documents_to_router(config):
    from src.agent.nodes import ceo_office_node
    llm = FakeLLM()
    llm.queue_structured(SupervisorDecision(verdict="ASSIGN", target_depts=["planning_dept"], reason="資料検索"))
    state = new_state("tenant-a", "rag-routing", "user", "登録済み社内資料から定休日を教えて")
    state["goal"] = state["raw_message"]
    await ceo_office_node(state, app_config=config, llm=llm, retrieval_available=True)
    prompt = llm.structured_calls[0]["user_message"]
    assert "まず該当部署にASSIGNして検索" in prompt
    assert "<retrieved_document" not in prompt


async def test_approval_summary_preserves_unresolved_qa_issues(config):
    from src.agent.nodes import _build_approval_summary, _CostTracker
    llm = FakeLLM()
    llm.queue_structured(ApprovalSummary(headline="回答", qa_result="QAの警告事項はありません"))
    state = new_state("tenant-a", "qa-warning", "user", "回答して")
    state.update(qa_verdict="FAIL", qa_findings=[{"issue": "資料との照合が必要"}], qa_warnings=[])
    result = await _build_approval_summary(state, app_config=config, llm=llm, cost=_CostTracker(config))
    assert "資料との照合が必要" in llm.structured_calls[0]["user_message"]
    assert "資料との照合が必要" in result.qa_result
    assert "要確認" in result.qa_result
    assert "警告事項はありません" not in result.qa_result
