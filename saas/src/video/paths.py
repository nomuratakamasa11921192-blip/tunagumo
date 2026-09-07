"""動画パイプラインの入出力パスを workspace/ 配下に固定する(Phase 9-3)。
外部から渡された任意のパスをそのままサブプロセスに渡さないためのガード
(パストラバーサル対策)。
"""

from pathlib import Path

WORKSPACE_ROOT = (Path(__file__).resolve().parent.parent.parent / "workspace").resolve()


class WorkspacePathError(Exception):
    pass


def ensure_in_workspace(path: str | Path) -> Path:
    """pathをworkspace/配下の絶対パスとして解決する。workspace/の外を指す場合は拒否する。"""
    candidate = Path(path)
    resolved = candidate.resolve() if candidate.is_absolute() else (WORKSPACE_ROOT / candidate).resolve()

    try:
        resolved.relative_to(WORKSPACE_ROOT)
    except ValueError:
        raise WorkspacePathError(f"workspace/配下以外のパスは扱えません: {path}") from None

    return resolved
