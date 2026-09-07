import os
import re
from pathlib import Path

import yaml

from src.agent.config_models import AppConfig
from src.tools.registry import all_tool_names

# 設定ファイルに書いてはいけない秘密情報のパターン。
# ${VAR_NAME} で環境変数から読ませる前提で、生の値がYAMLに直書きされていないか検査する。
_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"xoxb-[A-Za-z0-9-]+"),
    re.compile(r"xapp-[A-Za-z0-9-]+"),
]

_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigError(Exception):
    pass


class ConfigSecurityError(ConfigError):
    pass


def _check_no_hardcoded_secrets(raw_text: str) -> None:
    for pattern in _SECRET_PATTERNS:
        match = pattern.search(raw_text)
        if match:
            raise ConfigSecurityError(
                f"設定ファイルに秘密情報らしき文字列が直書きされています: {match.group()[:12]}... "
                "環境変数から読むよう ${VAR_NAME} の形式で参照してください。"
            )


def _substitute_env_vars(raw_text: str) -> str:
    def replace(m: re.Match) -> str:
        var_name = m.group(1)
        value = os.environ.get(var_name)
        if value is None:
            raise ConfigError(f"環境変数 {var_name} が設定されていません（設定ファイルが参照しています）")
        return value

    return _ENV_VAR_PATTERN.sub(replace, raw_text)


def _topological_order(departments: dict[str, "object"]) -> list[str]:
    """depends_on を解決した実行順を返す。循環参照があれば ConfigError。"""
    in_degree = {dept_id: 0 for dept_id in departments}
    dependents: dict[str, list[str]] = {dept_id: [] for dept_id in departments}

    for dept_id, dept in departments.items():
        for dep in dept.depends_on:
            if dep not in departments:
                raise ConfigError(
                    f"部署 '{dept_id}' の depends_on に存在しない部署ID '{dep}' が指定されています"
                )
            in_degree[dept_id] += 1
            dependents[dep].append(dept_id)

    queue = [dept_id for dept_id, deg in in_degree.items() if deg == 0]
    order: list[str] = []
    while queue:
        current = queue.pop(0)
        order.append(current)
        for nxt in dependents[current]:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)

    if len(order) != len(departments):
        unresolved = set(departments) - set(order)
        raise ConfigError(
            f"部署の depends_on に循環参照があります（解決できない部署: {sorted(unresolved)}）"
        )

    return order


def _check_tools_are_registered(departments: dict[str, "object"]) -> None:
    """Phase 11-2: 設定にない(=レジストリに登録されていない)ツール名が指定されたら
    起動時に検証エラーにする。"""
    known = all_tool_names()
    for dept_id, dept in departments.items():
        unknown = [t for t in dept.tools if t not in known]
        if unknown:
            raise ConfigError(
                f"部署 '{dept_id}' が未登録のツールを指定しています: {unknown}"
                f"（登録済み: {sorted(known)}）"
            )


def load_config(path: str | Path) -> AppConfig:
    path = Path(path)
    raw_text = path.read_text(encoding="utf-8")

    _check_no_hardcoded_secrets(raw_text)
    substituted = _substitute_env_vars(raw_text)

    raw = yaml.safe_load(substituted)
    config = AppConfig.model_validate(raw)

    # depends_on の存在チェック・循環参照チェック・到達可能性チェック
    # （到達不可能な部署があれば、そもそも len(order) != len(departments) になる）
    _topological_order(config.departments)
    _check_tools_are_registered(config.departments)

    return config
