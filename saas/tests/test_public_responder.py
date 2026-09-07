import pytest

from src.agent.public_responder import (
    ESCALATION_MESSAGE,
    MAX_EXCHANGES,
    PublicResponseDecision,
    check_keyword_escalation,
    respond,
)
from tests.fakes import FakeLLM

asyncio_test = pytest.mark.asyncio(loop_scope="session")


def test_keyword_escalation_detects_pricing_question():
    assert check_keyword_escalation("料金はいくらですか？") is not None


def test_keyword_escalation_detects_complaint():
    assert check_keyword_escalation("対応が最悪です、責任者を出してください") is not None


def test_keyword_escalation_none_for_normal_question():
    assert check_keyword_escalation("営業時間を教えてください") is None


@asyncio_test
async def test_keyword_hit_escalates_without_calling_llm():
    llm = FakeLLM()  # 何もqueueしない: 呼ばれたら失敗する

    result = await respond(
        llm=llm, model="claude-haiku-4-5-20251001", company_name="テスト社",
        message="契約について聞きたいです",
    )

    assert result.escalated is True
    assert result.reply == ESCALATION_MESSAGE
    assert llm.structured_calls == []


@asyncio_test
async def test_llm_decides_to_answer():
    llm = FakeLLM()
    llm.queue_structured(
        PublicResponseDecision(should_escalate=False, reply="営業時間は平日9時〜18時です。", reason="FAQで回答可能")
    )

    result = await respond(
        llm=llm, model="claude-haiku-4-5-20251001", company_name="テスト社", message="営業時間を教えてください"
    )

    assert result.escalated is False
    assert result.reply == "営業時間は平日9時〜18時です。"


@asyncio_test
async def test_llm_decides_to_escalate():
    llm = FakeLLM()
    llm.queue_structured(
        PublicResponseDecision(should_escalate=True, reply="", reason="資料に記載がない")
    )

    result = await respond(
        llm=llm, model="claude-haiku-4-5-20251001", company_name="テスト社", message="他社と比べてどうですか"
    )

    assert result.escalated is True
    assert result.reply == ESCALATION_MESSAGE


@asyncio_test
async def test_empty_reply_is_treated_as_escalation_even_if_flag_is_false():
    """LLMがshould_escalate=falseなのに空のreplyを返した場合(バグ・迷い)も、
    安全側(エスカレーション)に倒す。"""
    llm = FakeLLM()
    llm.queue_structured(PublicResponseDecision(should_escalate=False, reply="   ", reason="不明"))

    result = await respond(
        llm=llm, model="claude-haiku-4-5-20251001", company_name="テスト社", message="何か質問です"
    )

    assert result.escalated is True


@asyncio_test
async def test_exchange_limit_forces_escalation_without_calling_llm():
    llm = FakeLLM()  # 呼ばれたら失敗する
    history = []
    for i in range(MAX_EXCHANGES):
        history.append({"role": "user", "content": f"質問{i}"})
        history.append({"role": "assistant", "content": f"回答{i}"})

    result = await respond(
        llm=llm, model="claude-haiku-4-5-20251001", company_name="テスト社",
        message="まだ続けます", history=history,
    )

    assert result.escalated is True
    assert "往復上限" in result.reason
    assert llm.structured_calls == []


@asyncio_test
async def test_llm_failure_falls_back_to_escalation():
    from src.agent.llm import LLMFatalError

    llm = FakeLLM()

    async def failing_call_structured(**kwargs):
        raise LLMFatalError("APIエラー", Exception("boom"))

    llm.call_structured = failing_call_structured

    result = await respond(
        llm=llm, model="claude-haiku-4-5-20251001", company_name="テスト社", message="質問です"
    )

    assert result.escalated is True
    assert result.reply == ESCALATION_MESSAGE


@asyncio_test
async def test_rag_context_is_passed_through_to_prompt():
    llm = FakeLLM()
    llm.queue_structured(PublicResponseDecision(should_escalate=False, reply="回答", reason="資料に基づく"))

    await respond(
        llm=llm, model="claude-haiku-4-5-20251001", company_name="テスト社",
        message="質問", rag_context="<retrieved_document>公開資料の内容</retrieved_document>",
    )

    assert "公開資料の内容" in llm.structured_calls[0]["user_message"]
