from pydantic import BaseModel, Field


class VideoScene(BaseModel):
    narration: str  # 音声合成でそのまま読み上げる。記号・見出しを含めない話し言葉
    on_screen_text: str = ""  # 画面に短く表示する強調テキスト(空でもよい)
    duration_hint_seconds: float = 0.0  # 目安。実際の尺はTTS生成後の実測値を使う


class VideoScript(BaseModel):
    title: str
    scenes: list[VideoScene] = Field(min_length=1)
