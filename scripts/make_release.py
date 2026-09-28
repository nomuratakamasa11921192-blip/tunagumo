"""deploy_files.py用のrelease.jsonを作る。使い方: python scripts/make_release.py <旧commit> <新commit> <saas相対パス...> [-o 出力先]"""
import argparse, base64, hashlib, json, subprocess

p = argparse.ArgumentParser()
p.add_argument("old"); p.add_argument("new"); p.add_argument("files", nargs="+"); p.add_argument("-o", default="release.json")
a = p.parse_args()
show = lambda c, f: subprocess.check_output(["git", "show", f"{c}:saas/{f}"])
files = {f: {"sha256": hashlib.sha256(b).hexdigest(), "data": base64.b64encode(b).decode()} for f in a.files for b in [show(a.new, f)]}
old = {f: hashlib.sha256(show(a.old, f)).hexdigest() for f in a.files}
commit = subprocess.check_output(["git", "rev-parse", a.new]).decode().strip()
json.dump({"commit": commit, "files": files, "old_sha256": old}, open(a.o, "w"))
print(commit[:7], {f: v["sha256"][:12] for f, v in files.items()})
