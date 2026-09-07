import pytest

from src.video.schemas import VideoScene, VideoScript
from src.video.script import ScriptGenerationError, generate_script
from tests.fakes import FakeLLM

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_generate_script_returns_llm_output_and_usage():
    llm = FakeLLM()
    llm.queue_structured(
        VideoScript(
            title="特別休暇制度の紹介",
            scenes=[
                VideoScene(narration="こんにちは、今日は特別休暇制度についてご紹介します。", duration_hint_seconds=4.0),
                VideoScene(narration="勤続1年以上で年5日取得できます。", on_screen_text="年5日", duration_hint_seconds=3.0),
            ],
        )
    )

    script, usage = await generate_script(llm=llm, model="claude-sonnet-5", topic="特別休暇制度の紹介動画を作って")

    assert script.title == "特別休暇制度の紹介"
    assert len(script.scenes) == 2
    assert usage["input_tokens"] == 10  # FakeLLMの固定値
    assert len(llm.structured_calls) == 1
    assert "特別休暇制度の紹介動画を作って" in llm.structured_calls[0]["user_message"]


async def test_generate_script_includes_target_seconds_in_prompt():
    llm = FakeLLM()
    llm.queue_structured(
        VideoScript(title="x", scenes=[VideoScene(narration="ナレーション", duration_hint_seconds=1.0)])
    )

    await generate_script(llm=llm, model="claude-sonnet-5", topic="お知らせ動画", target_seconds=30)

    assert "約30秒" in llm.structured_calls[0]["user_message"]


async def test_generate_script_raises_script_generation_error_on_llm_failure():
    from src.agent.llm import LLMFatalError

    llm = FakeLLM()

    async def failing_call_structured(**kwargs):
        raise LLMFatalError("APIエラー", Exception("boom"))

    llm.call_structured = failing_call_structured

    with pytest.raises(ScriptGenerationError):
        await generate_script(llm=llm, model="claude-sonnet-5", topic="失敗するはずの依頼")
