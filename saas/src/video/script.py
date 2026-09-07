"""動画の台本生成(Phase 9-2: 台本生成 ○)。テナント自身のAnthropic APIキーで動く
既存のStructuredLLMをそのまま使うので、この機能単体では新しい従量課金は発生しない
(音声合成・動画エンコードとは別)。
"""

from src.agent.llm import LLMFatalError, LLMOutputError, StructuredLLM
from src.video.schemas import VideoScript

SCRIPT_SYSTEM_PROMPT = (
    "あなたは短尺動画の台本作成担当です。依頼内容をもとに、ナレーション付きの台本を"
    "シーン単位で作成してください。\n"
    "- narrationは音声合成でそのまま読み上げられます。見出し記号(##など)や箇条書き記号を"
    "含めず、自然に話し言葉として読める文章にしてください。\n"
    "- on_screen_textは画面に短く表示する強調テキストです(不要なら空文字列でよい)。\n"
    "- duration_hint_secondsは、そのナレーションを読み上げるのにかかるおおよその秒数の"
    "目安です(実際の尺は音声合成後の実測値を優先します)。"
)


class ScriptGenerationError(Exception):
    pass


async def generate_script(
    *, llm: StructuredLLM, model: str, topic: str, target_seconds: int | None = None
) -> tuple[VideoScript, dict]:
    user_message = f"【依頼内容】\n{topic}"
    if target_seconds:
        user_message += f"\n\n【目標尺】約{target_seconds}秒"

    try:
        script, usage = await llm.call_structured(
            model=model,
            system_prompt=SCRIPT_SYSTEM_PROMPT,
            user_message=user_message,
            output_model=VideoScript,
        )
    except (LLMOutputError, LLMFatalError) as e:
        raise ScriptGenerationError(str(e)) from e

    return script, usage
