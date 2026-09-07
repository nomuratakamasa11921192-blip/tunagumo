import sys

from src.agent.config_loader import ConfigError, load_config


def main() -> int:
    if len(sys.argv) != 2:
        print("使い方: python -m src.validate_config <config.yaml>")
        return 1

    path = sys.argv[1]
    try:
        config = load_config(path)
    except ConfigError as e:
        print(f"設定エラー: {e}")
        return 1

    print(f"OK: {path}")
    print(f"  会社: {config.company.name}")
    print(f"  部署数: {len(config.departments)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
