# 営業リード(Notion「ツナグモ 不動産営業リード」)のDM文面を、サイトの価格・提供内容に合わせて一括修正する。
# 2026-09-22: 月5万円→月39,800円〜、サイトに無い動画制作代行(¥15,000〜)の案内を内製のルームツアー動画の一文へ差し替え、SaaS→AIサービス。
# 既定は試算のみ。--apply で書き込む。元の文面は saas/workspace/lead_dm_backup/ に保存してから実行すること。
import sys, re, json, time, collections
sys.path.insert(0, "scripts")
import notion_sync as N, auto_post

APPLY = "--apply" in sys.argv
token = auto_post.load_env()["NOTION_TOKEN"]
db = "895f1c19-b95d-403b-9a2b-20fbf4f30c87"

rows, cursor = [], None
while True:
    body = {"page_size": 100}
    if cursor: body["start_cursor"] = cursor
    r = N.api(token, "POST", f"/databases/{db}/query", body)
    rows += r["results"]
    if not r.get("has_more"): break
    cursor = r["next_cursor"]

NEW_SENT = "夜間・休日の反響へのすぐの返信や、ルームツアー動画づくりもおまかせいただけます。"
VIDEO = re.compile(r"ショート動画|動画制作|制作代行|[¥￥]\s?1[25],000|15,000円|50,000")

def transform(dm):
    parts = re.split(r"(?<=[。\n])", dm)
    out, inserted = [], False
    for s in parts:
        if VIDEO.search(s):
            if not inserted:
                prefix = re.match(r"^\s*[②2][\.．、)）]?\s*", s)
                out.append((prefix.group(0) if prefix else "") + NEW_SENT + ("\n" if s.endswith("\n") else ""))
                inserted = True
            continue
        out.append(s)
    t = "".join(out)
    t = re.sub(r"月額\s?[5５]\s?万円", "月額39,800円〜", t)
    t = re.sub(r"月\s?[5５]\s?万円(の一律プラン|の一律|一律)?", "月39,800円〜", t)
    t = t.replace("の一律プラン", "")
    t = re.sub(r"AI\s?活用の\s?SaaS", "AIサービス", t)
    t = re.sub(r"AI\s?SaaS", "AIサービス", t)
    t = t.replace("SaaS", "サービス")
    return t

changes, bad = [], collections.Counter()
for p in rows:
    old = N.plain_text(p["properties"]["DM文面"]); new = transform(old)
    if new == old: continue
    changes.append((p["id"], N.plain_text(p["properties"]["会社名/店名"]), old, new))
    if re.search(r"[5５]万円", new): bad["5万円が残った"] += 1
    if re.search(r"15,000|50,000", new): bad["動画代行の価格が残った"] += 1
    if "SaaS" in new: bad["SaaSが残った"] += 1
    if "tunagumo.com" in old and "tunagumo.com" not in new: bad["URLが消えた"] += 1
    if "はじめまして" in old and "はじめまして" not in new: bad["挨拶が消えた"] += 1
    if new.count(NEW_SENT) > 1: bad["差し替え文が重複"] += 1
    if len(new) < 80: bad["短すぎる"] += 1
    if len(new) > 2000: bad["2000字超"] += 1

print(f"全{len(rows)}件 / 書き換え対象 {len(changes)}件")
print("不変条件の違反:", dict(bad) or "なし")
if not APPLY or bad:
    sys.exit(0 if not bad else 1)

ok = ng = 0
for pid, name, old, new in changes:
    for attempt in range(4):
        try:
            N.api(token, "PATCH", f"/pages/{pid}",
                  {"properties": {"DM文面": {"rich_text": [{"text": {"content": new}}]}}})
            ok += 1
            break
        except RuntimeError as e:
            if "429" in str(e) or "502" in str(e) or "503" in str(e):
                time.sleep(2 * (attempt + 1)); continue
            print("失敗:", name, str(e)[:120]); ng += 1; break
    time.sleep(0.35)  # Notion APIの目安(毎秒3件)を守る
print(f"書き込み完了: 成功 {ok}件 / 失敗 {ng}件")
