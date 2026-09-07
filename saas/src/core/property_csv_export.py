"""物件情報を汎用CSVに書き出す(ポータル一括配信サービス連携用)。

SUUMO等のポータルへ直接登録する公開APIは存在しない(robots.txtで内部APIへの
アクセスも禁止されている)。そのため、ieRabuchCLOUD・RealterWorksのような
不動産業界向け一括配信サービスに読み込ませる想定の汎用CSVを出力する
(各サービスの正確な列名・並び順は、実際のインポート画面で顧客自身に
マッピングしてもらう必要がある。ここでは業界で共通してよく使われる項目を
網羅した「読み込みやすい」フォーマットにするに留める)。
"""

import csv
import io

# 列の並び順はよくある賃貸/売買物件の一括登録テンプレートを参考にした一般的な項目。
CSV_FIELDS = [
    "物件名",
    "取引態様",  # 賃貸 / 売買
    "所在地",
    "最寄り駅",
    "徒歩分",
    "価格・賃料",
    "管理費・共益費",
    "敷金",
    "礼金",
    "間取り",
    "専有面積",
    "築年数",
    "構造",
    "方位",
    "設備・特徴",
    "物件説明",
    "画像URL1",
    "画像URL2",
    "画像URL3",
    "画像URL4",
    "画像URL5",
]


class PropertyCsvExportError(Exception):
    pass


def generate_property_csv(fields: dict) -> bytes:
    """fieldsはCSV_FIELDSのキー(の一部)を持つdict。無い項目は空欄になる。
    Excelでの文字化けを防ぐため、UTF-8 BOM付きで返す。
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerow({k: fields.get(k, "") for k in CSV_FIELDS})
    return buffer.getvalue().encode("utf-8-sig")
