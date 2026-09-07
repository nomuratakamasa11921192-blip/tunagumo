"""顧客のAnthropic APIキーなど、DBに保存する必要があるが平文で置きたくない値の
暗号化・復号(対称鍵、Fernet)。

KMS等の秘密情報管理基盤は導入していないため、鍵自体は環境変数(TENANT_SECRET_KEY)で
渡す。この鍵が漏れれば復号できてしまう点は、ADMIN_API_KEYと同程度の信頼が必要な
運用上の前提として割り切っている(単一管理者が運用する規模のSaaSであるため)。
"""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from src.core.config import settings


class CryptoConfigError(Exception):
    pass


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = settings.tenant_secret_key
    if not key:
        raise CryptoConfigError(
            "TENANT_SECRET_KEYが設定されていません。顧客のAPIキーを保存する前に、"
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\" "
            "で生成した値を環境変数に設定してください。"
        )
    return Fernet(key.encode("utf-8"))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise CryptoConfigError(
            "保存されている値の復号に失敗しました(TENANT_SECRET_KEYが変わった可能性があります)"
        ) from e
