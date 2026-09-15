from src.core.property_csv_export import CSV_FIELDS, generate_property_csv


def test_generate_property_csv_includes_header_and_values():
    csv_bytes = generate_property_csv({"物件名": "テストマンション101", "所在地": "埼玉県春日部市"})

    text = csv_bytes.decode("utf-8-sig")
    lines = text.splitlines()
    assert lines[0] == ",".join(CSV_FIELDS)
    assert "テストマンション101" in lines[1]
    assert "埼玉県春日部市" in lines[1]


def test_generate_property_csv_leaves_missing_fields_empty():
    csv_bytes = generate_property_csv({"物件名": "テスト"})
    text = csv_bytes.decode("utf-8-sig")
    lines = text.splitlines()
    # ヘッダーと同じ列数のカンマ区切りになっていること(空欄はあっても列数は揃う)
    assert lines[1].count(",") == len(CSV_FIELDS) - 1


def test_generate_property_csv_ignores_unknown_keys():
    csv_bytes = generate_property_csv({"物件名": "テスト", "未知の項目": "無視される"})
    text = csv_bytes.decode("utf-8-sig")
    assert "未知の項目" not in text
    assert "無視される" not in text


def test_generate_property_csv_has_utf8_bom():
    csv_bytes = generate_property_csv({"物件名": "テスト"})
    assert csv_bytes.startswith(b"\xef\xbb\xbf")


def test_formula_like_values_are_neutralized():
    import csv
    import io

    from src.core.property_csv_export import generate_property_csv

    data = generate_property_csv(
        {
            "物件名": '=HYPERLINK("https://evil.example","クリック")',
            "物件説明": "@SUM(1+1)",
            "設備・特徴": "+cmd",
            "価格・賃料": "-3,000",
            "敷金": "-cmd|' /C calc'!A0",
            "所在地": "東京都千代田区",
        }
    )
    row = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))[0]
    assert row["物件名"].startswith("'=")
    assert row["物件説明"] == "'@SUM(1+1)"
    assert row["設備・特徴"] == "'+cmd"
    assert row["価格・賃料"] == "-3,000"  # 数値はそのまま
    assert row["敷金"].startswith("'-")
    assert row["所在地"] == "東京都千代田区"
