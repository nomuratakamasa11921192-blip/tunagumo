"""文書のチャンク分割(Phase 7-2)。日本語前提: 単語数ベースではなく文字数ベースで区切り、
句点(。)と改行を優先した境界で切る(文の途中で切らない)。Markdownの見出しがあれば
チャンク先頭のメタ情報として保持する(検索精度に直結するため)。
"""

import re
from dataclasses import dataclass

TARGET_CHUNK_SIZE = 600  # 400〜800字の目安の中間値
MAX_CHUNK_SIZE = 800
MIN_CHUNK_SIZE = 400
OVERLAP_RATIO = 0.12  # 10〜15%の目安の中間値

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
# 句点・改行の直後を区切り候補にする(文の途中で切らないため)
_BOUNDARY_RE = re.compile(r"(?<=[。\n])")


@dataclass
class Chunk:
    content: str
    heading: str | None
    chunk_index: int


def _split_into_sentences(text: str) -> list[str]:
    """句点・改行の直後で分割する。空文字列は除く。"""
    parts = _BOUNDARY_RE.split(text)
    return [p for p in parts if p.strip()]


def _heading_at(text: str, pos: int) -> str | None:
    """posより前で最後に出てきたMarkdown見出しを返す(無ければNone)。"""
    heading = None
    for m in _HEADING_RE.finditer(text[:pos]):
        heading = m.group(2).strip()
    return heading


def chunk_text(text: str) -> list[Chunk]:
    """テキストをチャンクに分割する。

    - 文字数ベース(目安400〜800字)で分割する
    - 句点(。)・改行を優先した境界で切り、文の途中で切らない
    - 前後のチャンクは10〜15%程度オーバーラップさせる(文脈の連続性のため)
    - Markdownの見出しがあれば、そのチャンクが属する直近の見出しをメタ情報として持たせる
    """
    text = text.strip()
    if not text:
        return []

    sentences = _split_into_sentences(text)
    if not sentences:
        return []

    chunks: list[Chunk] = []
    pos = 0  # textの中の現在位置(見出し検索に使う)
    i = 0
    n = len(sentences)

    while i < n:
        buf = ""
        start_i = i
        # target(600字)を超えるまで文を積む。ただしmaxを超える手前で止める
        while i < n and len(buf) + len(sentences[i]) <= MAX_CHUNK_SIZE:
            buf += sentences[i]
            i += 1
            if len(buf) >= TARGET_CHUNK_SIZE:
                break

        if not buf:
            # 1文だけでMAX_CHUNK_SIZEを超える異常系(表などが1文として扱われた場合)。
            # 文を割らずにそのまま1チャンクにする(表のまとまりを壊さないため、7-2の方針通り)。
            buf = sentences[i]
            i += 1

        chunk_start_pos = pos
        heading = _heading_at(text, chunk_start_pos)
        chunks.append(Chunk(content=buf, heading=heading, chunk_index=len(chunks)))
        pos += len(buf)

        if i >= n:
            break

        # 次のチャンクの先頭に、今のチャンクの末尾からオーバーラップ分だけ戻す
        overlap_len = int(len(buf) * OVERLAP_RATIO)
        if overlap_len > 0:
            back = 0
            j = i - 1
            while j > start_i and back < overlap_len:
                back += len(sentences[j])
                j -= 1
            i = max(j + 1, start_i + 1)  # 最低1文は進める(無限ループ防止)
            pos = chunk_start_pos + len(buf) - back

    return chunks
