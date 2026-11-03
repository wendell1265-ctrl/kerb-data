#!/usr/bin/env python3
"""tools/make-manifest.py — 배포 폴더(tracks/*.json + glossary.json + circuit-map.json)로 manifest.json을 만든다.

manifest.json: { dataVersion, minAppVersion, generatedAt, files: [{path, sha256, size}] }
- dataVersion: 폴더에 이전 manifest가 있고 파일 해시가 하나라도 바뀌었으면 +1, 그대로면 유지, 처음이면 1.
- minAppVersion: --min-app으로 지정, 없으면 이전 값, 그것도 없으면 "1.0".
사용: python3 tools/make-manifest.py <배포 폴더> [--min-app 1.0]
"""
import hashlib, json, pathlib, sys, datetime

# KERB Pro 원격 설정(선택): tools/pro-config.json 의 freeTracks(무료 주행 상세 트랙 id 목록), earlyAccessUntil(ISO 날짜).
# 파일이 없거나 키가 없으면 manifest에 넣지 않는다(앱은 번들 기본값 사용). 값이 바뀌면 dataVersion을 올린다.
PRO_KEYS = ("freeTracks", "earlyAccessUntil")

def pro_extras(prev=None):
    """tools/pro-config.json이 있으면 그 값, 없으면 이전 manifest의 값을 그대로 이어 쓴다(워크플로가 Pro 설정을 지우지 않게)."""
    p = pathlib.Path(__file__).resolve().parent / "pro-config.json"
    if not p.exists():
        return {k: prev[k] for k in PRO_KEYS if prev and k in prev}
    data = json.loads(p.read_text())
    return {k: data[k] for k in PRO_KEYS if k in data}

def main():
    args = sys.argv[1:]
    if not args:
        sys.exit("사용법: make-manifest.py <배포 폴더> [--min-app X.Y]")
    root = pathlib.Path(args[0])
    min_app = None
    if "--min-app" in args:
        min_app = args[args.index("--min-app") + 1]
    paths = sorted(p for p in (root / "tracks").glob("*.json")) + [root / "glossary.json", root / "circuit-map.json"]
    # FIA 결과 보완 파일(kerb-data 워크플로가 만든다): results/{시즌}-{라운드}.json
    paths += sorted((root / "results").glob("*.json")) if (root / "results").is_dir() else []
    files = []
    for p in paths:
        data = p.read_bytes()
        files.append({"path": p.relative_to(root).as_posix(), "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
    prev = None
    prev_path = root / "manifest.json"
    if prev_path.exists():
        try:
            prev = json.loads(prev_path.read_text())
        except ValueError:
            prev = None
    if prev is None:
        version = 1
    else:
        old = {f["path"]: f["sha256"] for f in prev.get("files", [])}
        new = {f["path"]: f["sha256"] for f in files}
        prev_extras = {k: prev[k] for k in PRO_KEYS if k in prev}
        version = prev["dataVersion"] + (0 if old == new and prev_extras == pro_extras(prev) else 1)
    new_min = min_app or (prev or {}).get("minAppVersion", "1.0")
    if prev is not None and version == prev["dataVersion"] and new_min == prev.get("minAppVersion"):
        # 파일도 최소 앱 버전도 그대로면 manifest를 다시 쓰지 않는다(generatedAt만 달라져 불필요한 배포가 생기는 것을 막는다)
        print(f"manifest: 변경 없음 dataVersion={version} minAppVersion={new_min} files={len(files)}")
        return
    manifest = {
        "dataVersion": version,
        "minAppVersion": min_app or (prev or {}).get("minAppVersion", "1.0"),
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": files,
        **pro_extras(prev),
    }
    prev_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(f"manifest: dataVersion={version} minAppVersion={manifest['minAppVersion']} files={len(files)}")

main()
