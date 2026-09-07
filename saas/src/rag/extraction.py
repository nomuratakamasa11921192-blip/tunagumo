"""アップロードされたファイルから、チャンク分割前のプレーンテキストを取り出す(Phase 7-1)。"""

import hashlib
import io

import pypdf
from docx import Document as DocxDocument

SUPPORTED_MIME_TYPES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/plain": "txt",
    "text/markdown": "md",
}

MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20MB


class ExtractionError(Exception):
    pass


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_text(data: bytes, mime_type: str) -> str:
    """拡張子だけでなくMIMEタイプで種別を判定し、対応する方法でテキストを取り出す。
    画像のみのスキャンPDF等、テキストが1文字も取れない場合は明示的にエラーにする
    (OCRは対象外、7-1-5)。
    """
    kind = SUPPORTED_MIME_TYPES.get(mime_type)
    if kind is None:
        raise ExtractionError(
            f"対応していないファイル形式です(mime_type={mime_type})。PDF/DOCX/TXT/MDのみ対応しています。"
        )

    if kind == "pdf":
        text = _extract_pdf(data)
    elif kind == "docx":
        text = _extract_docx(data)
    else:  # txt / md
        text = _extract_plain(data)

    if not text.strip():
        raise ExtractionError(
            "文書からテキストを取得できませんでした。画像のみのスキャンPDF等、"
            "文字情報を含まないファイルには対応していません(OCRは対象外です)。"
        )
    return text


def _extract_pdf(data: bytes) -> str:
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
    except Exception as e:
        raise ExtractionError(f"PDFの読み込みに失敗しました: {e}") from e
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _extract_docx(data: bytes) -> str:
    try:
        doc = DocxDocument(io.BytesIO(data))
    except Exception as e:
        raise ExtractionError(f"DOCXの読み込みに失敗しました: {e}") from e
    return "\n".join(p.text for p in doc.paragraphs)


def _extract_plain(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ExtractionError(f"テキストファイルの文字コードが読み取れませんでした(UTF-8を想定): {e}") from e
