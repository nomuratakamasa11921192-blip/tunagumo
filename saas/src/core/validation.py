from fastapi import HTTPException


def validate_anthropic_key_format(key: str) -> None:
    """Anthropic APIキーの形式だけを軽くチェックする(タイプミスの早期発見が目的)。
    実際にAPIを叩いて検証すると顧客のクレジットを消費する・処理が遅くなるため行わない。
    管理画面からの新規発行(src/admin/routes.py)と、顧客自身によるキー更新
    (src/api/routes/account.py)の両方から使う共通ロジック。
    """
    if not key.startswith("sk-ant-"):
        raise HTTPException(
            status_code=400,
            detail="Anthropic APIキーの形式が不正です(sk-ant-で始まる必要があります)",
        )


def validate_openai_key_format(key: str) -> None:
    """OpenAI APIキーの形式だけを軽くチェックする(Phase 7 RAGの埋め込みに使う)。
    validate_anthropic_key_formatと同じ理由で、実際にAPIを叩いての検証はしない。
    """
    if not key.startswith("sk-"):
        raise HTTPException(
            status_code=400,
            detail="OpenAI APIキーの形式が不正です(sk-で始まる必要があります)",
        )


def validate_higgsfield_key_format(key_id: str, key_secret: str) -> None:
    """Higgsfield APIキー(key_id / key_secretの組)の形式だけを軽くチェックする。
    他の validate_*_key_format と同じ理由で、実際にAPIを叩いての検証はしない。
    """
    if not key_id.strip() or not key_secret.strip():
        raise HTTPException(
            status_code=400,
            detail="Higgsfield APIキーが不正です(Key IDとKey Secretの両方が必要です)",
        )
