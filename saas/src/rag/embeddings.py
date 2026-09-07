"""埋め込みプロバイダの抽象化(Phase 7-3)。差し替え可能にしておく
(仕様書はローカルモデルを第一候補としていたが、VPSのメモリ制約(8GB以上必要)により、
2026-08-24の判断で外部API(OpenAI)方式を採用した。将来ローカルモデルに変更する場合も
このインターフェースの実装を1つ追加するだけで済む)。
"""

from typing import Protocol

import httpx

from src.core.models import EMBEDDING_DIM

OPENAI_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"


class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """テキストのリストを受け取り、同じ順序で埋め込みベクトルのリストを返す。"""
        ...


class EmbeddingError(Exception):
    pass


class OpenAIEmbeddingProvider:
    """顧客自身のOpenAI APIキーを使う(事業モデル: AI利用の実費は顧客が自分で負担する。
    Anthropicキーと同じ考え方)。"""

    def __init__(self, api_key: str):
        self._api_key = api_key

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.post(
                OPENAI_EMBEDDINGS_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": OPENAI_EMBEDDING_MODEL, "input": texts},
            )
        if res.status_code >= 400:
            raise EmbeddingError(f"OpenAI embeddings APIエラー(status={res.status_code}): {res.text[:300]}")

        data = res.json()["data"]
        # OpenAIは並び順を保証していると明記しているが、indexで並べ直して確実にする
        ordered = sorted(data, key=lambda d: d["index"])
        vectors = [d["embedding"] for d in ordered]
        for v in vectors:
            if len(v) != EMBEDDING_DIM:
                raise EmbeddingError(
                    f"埋め込みの次元数が想定と異なります(期待={EMBEDDING_DIM}, 実際={len(v)})。"
                    "OPENAI_EMBEDDING_MODELの変更にはマイグレーションでの列変更が必要です。"
                )
        return vectors
