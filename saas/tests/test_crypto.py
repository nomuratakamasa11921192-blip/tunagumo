import pytest

from src.core.crypto import CryptoConfigError, decrypt_secret, encrypt_secret


def test_encrypt_then_decrypt_round_trips():
    plaintext = "sk-ant-api03-realsecretvalue"
    ciphertext = encrypt_secret(plaintext)

    assert ciphertext != plaintext
    assert decrypt_secret(ciphertext) == plaintext


def test_decrypting_garbage_raises_crypto_config_error():
    with pytest.raises(CryptoConfigError):
        decrypt_secret("not-a-real-fernet-token")


def test_decrypting_plaintext_that_looks_like_a_key_raises():
    """暗号化前(移行前)の平文値が誤って渡された場合も、静かに壊れた値を返さず
    はっきりエラーにする(復号失敗を握りつぶさない)。"""
    with pytest.raises(CryptoConfigError):
        decrypt_secret("sk-ant-api03-oldplaintextvalue")
