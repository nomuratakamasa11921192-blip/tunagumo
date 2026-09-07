"""テナント内ユーザー(TenantUser)のパスワードハッシュ化。

src/core/crypto.py(Fernet対称鍵)とは別物: あちらは復号して元の値を取り出す必要が
ある秘密情報(顧客のAnthropic APIキー等)向け、こちらは復号する必要が無い一方向ハッシュ
(パスワード本体はどこにも保存しない)。新規の依存を増やさないため、標準ライブラリの
hashlib.pbkdf2_hmacのみで実装する。
"""

import hashlib
import hmac
import secrets

_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 600_000  # OWASP推奨値(2023年時点でのpbkdf2-sha256の下限目安)
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    salt = secrets.token_hex(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS)
    return f"{_ALGORITHM}${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    try:
        algorithm, iterations_str, salt, expected_hex = hashed.split("$")
        if algorithm != _ALGORITHM:
            return False
        iterations = int(iterations_str)
    except (ValueError, AttributeError):
        return False

    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return hmac.compare_digest(digest.hex(), expected_hex)
