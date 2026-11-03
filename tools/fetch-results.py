#!/usr/bin/env python3
"""kerb-data/tools/fetch-results.py — Jolpica가 결승 결과를 올리기 전에 FIA 순위 문서로 보완 파일을 만든다.

흐름
  1. Jolpica 일정에서 "결승 종료(시작+3시간) 후 72시간 이내"인 라운드만 대상으로 한다.
  2. Jolpica에 그 라운드 결승 결과가 있으면 아무것도 하지 않는다(Jolpica 우선).
  3. 없으면 FIA 문서 목록 페이지(시즌 페이지의 이벤트 선택 목록 → 이벤트 페이지)에서
     "Final Race Classification", 없으면 "Provisional Race Classification" PDF 링크를 찾는다(URL을 만들어 쓰지 않는다).
  4. PDF에서 순위·차번·드라이버·팀·랩 수·기록/차이·상태·최고 랩을 뽑고 검증한다.
     검증 실패(드라이버 수, 차번→드라이버 매칭, 순위 중복 등) → 아무것도 쓰지 않고 종료 코드 2 + 오류 메시지 파일.
  5. 통과하면 results/{season}-{round}.json 을 쓰고 manifest를 다시 만든다(Final이 나오면 provisional을 덮어쓴다).

사용
  fetch-results.py                       # 자동 모드(워크플로 실행용)
  fetch-results.py --compare 18          # 그 라운드의 FIA 문서를 파싱해 Jolpica 결과와 필드별로 대조만 한다(파일 쓰기 없음)
  fetch-results.py --round 19 --force    # 대상 시간 조건·Jolpica 존재 여부와 상관없이 그 라운드 보완 파일 생성
옵션: --root <kerb-data 루트>(기본: 이 파일의 상위의 상위), --now <ISO UTC>(테스트용)
요구: Python 3.9+, pypdf
"""
import argparse, datetime as dt, hashlib, json, os, re, subprocess, sys, urllib.parse, urllib.request
from html.parser import HTMLParser

JOLPICA = "https://api.jolpi.ca/ergast/f1"
FIA = "https://www.fia.com"
FIA_SEASON_PAGE = FIA + "/documents/championships/fia-formula-one-world-championship-14"
# 일반 브라우저 User-Agent(개인 이메일 등을 넣지 않는다)
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
POINTS = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]   # 2026: 최고 랩 보너스 점수 없음


class Fail(Exception):
    """검증 실패 — 아무것도 쓰지 않고 issue로 알린다."""


_last_jolpica = [0.0]


def get(url, binary=False, retries=4):
    import time, urllib.error
    last = None
    for attempt in range(retries):
        try:
            if "jolpi.ca" in url:   # Jolpica 요청 제한(초당 4) 보호: 요청 사이 0.35초
                wait = 0.35 - (time.time() - _last_jolpica[0])
                if wait > 0: time.sleep(wait)
                _last_jolpica[0] = time.time()
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            return data if binary else data.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            last = e
            time.sleep(5 * (attempt + 1) if e.code == 429 else 2)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2)
    raise Fail(f"요청 실패: {url} ({last})")


def jolpica(path):
    return json.loads(get(f"{JOLPICA}/{path}"))["MRData"]


# ---------------------------------------------------------------- 일정·대상 라운드

def parse_utc(date, time):
    return dt.datetime.fromisoformat(f"{date}T{(time or '00:00:00Z').replace('Z', '+00:00')}")


def schedule(year):
    races = jolpica(f"{year}.json?limit=100")["RaceTable"]["Races"]
    out = []
    for r in races:
        out.append({"round": int(r["round"]), "name": r["raceName"], "start": parse_utc(r["date"], r.get("time"))})
    return out


def target_rounds(rounds, now):
    """결승 종료(시작+3시간) 후 72시간 이내."""
    return [r for r in rounds if dt.timedelta(0) <= now - (r["start"] + dt.timedelta(hours=3)) <= dt.timedelta(hours=72)]


def jolpica_race_results(year, rnd):
    races = jolpica(f"{year}/{rnd}/results.json?limit=100")["RaceTable"]["Races"]
    return races[0]["Results"] if races and races[0].get("Results") else []


# ---------------------------------------------------------------- FIA 문서 목록

class _Options(HTMLParser):
    def __init__(self):
        super().__init__()
        self.options, self._v = [], None
    def handle_starttag(self, tag, attrs):
        if tag == "option":
            self._v = dict(attrs).get("value")
    def handle_data(self, data):
        if self._v is not None and data.strip():
            self.options.append((self._v, data.strip()))
            self._v = None


class _Docs(HTMLParser):
    """문서 행: <li class="document-row"> <a href=...> ... <div class="title">Doc 86 - Final Race Classification</div>"""
    def __init__(self):
        super().__init__()
        self.docs, self._href, self._in_title, self._buf = [], None, False, ""
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href", "").startswith("/system/files/decision-document/"):
            self._href = a["href"]
        if tag == "div" and a.get("class") == "title" and self._href:
            self._in_title, self._buf = True, ""
    def handle_endtag(self, tag):
        if tag == "div" and self._in_title:
            self.docs.append((" ".join(self._buf.split()), self._href))
            self._in_title, self._href = False, None
    def handle_data(self, data):
        if self._in_title:
            self._buf += data


def find_classification(year, race_name):
    """(문서 제목, PDF 주소, 'final'|'provisional') — 목록 페이지의 링크만 쓴다. 없으면 None."""
    season_url = None
    page = get(FIA_SEASON_PAGE)
    # 시즌 선택 목록에서 그 해 페이지 주소를 찾는다
    p = _Options(); p.feed(page)
    for value, label in p.options:
        if label.strip().upper() == f"SEASON {year}":
            season_url = FIA + value if value.startswith("/") else value
    if season_url and "/season/" not in season_url:   # 최신 시즌은 시즌 목록의 첫 항목이 시즌 루트 주소
        season_page = page
    else:
        season_page = get(season_url) if season_url else page
    p = _Options(); p.feed(season_page)
    event_url = None
    for value, label in p.options:
        if "/event/" in value and label.strip().lower() == race_name.strip().lower():
            event_url = FIA + value if value.startswith("/") else value
    if not event_url:
        return None
    d = _Docs(); d.feed(get(event_url))
    for kind, pattern in (("final", r"final race classification"), ("provisional", r"provisional race classification")):
        for title, href in d.docs:
            if re.search(pattern, title, re.I) and not re.search(r"sprint", title, re.I):
                return title, FIA + href, kind
    return None


# ---------------------------------------------------------------- PDF 파싱

def pdf_text(path_or_bytes):
    from pypdf import PdfReader
    import io
    data = path_or_bytes if isinstance(path_or_bytes, bytes) else open(path_or_bytes, "rb").read()
    return "\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(data)).pages)


def to_ms(t):
    """'1:35:16.650' | '35:16.650' | '16.650' → 밀리초"""
    parts = t.split(":")
    sec = float(parts[-1])
    mins = int(parts[-2]) if len(parts) > 1 else 0
    hrs = int(parts[-3]) if len(parts) > 2 else 0
    return round(((hrs * 60 + mins) * 60 + sec) * 1000)


def fmt_gap(ms):
    """Jolpica 표기: '+13.078' / '+1:22.809'"""
    s, milli = divmod(ms, 1000)
    m, s = divmod(s, 60)
    return f"+{m}:{s:02d}.{milli:03d}" if m else f"+{s}.{milli:03d}"


ROW1 = re.compile(r"^(?P<pos>\d+) (?P<no>\d+) (?P<name>.+?)(?: \*)?$")
ROW1_NC = re.compile(r"^(?P<no>\d+) (?P<name>[A-Za-zÀ-ÿ'.\- ]+?)(?: \*)?$")
TIME = r"\d+(?::\d{2}){0,2}\.\d{3}|\d+:\d{2}:\d{2}\.\d{3}"
ROW2 = re.compile(
    r"^(?P<team>.+?) (?P<laps>\d+) (?P<time>\d+:\d{2}:\d{2}\.\d{3}|\d+:\d{2}\.\d{3}) (?P<rest>.*?)"
    r"(?P<kmh>\d{2,3}\.\d{3}) (?P<fast>\d+:\d{2}\.\d{3}) (?P<on>\d+)(?: (?P<pts>\d+))?$")
ROW2_NC = re.compile(
    r"^(?P<team>.+?) (?P<laps>\d+) (?P<time>\d+:\d{2}:\d{2}\.\d{3}|\d+:\d{2}\.\d{3}) (?P<status>DNF|DNS|DSQ|NC) "
    r"(?P<kmh>\d{2,3}\.\d{3}) (?P<fast>\d+:\d{2}\.\d{3}) (?P<on>\d+)$")


def parse_classification(text):
    """FIA 순위 PDF 텍스트 → [{'pos','no','name','team','laps','time','nc','fast','on','pts'}] (classified + not classified)"""
    lines = [l.rstrip() for l in text.splitlines()]
    start = next((i for i, l in enumerate(lines) if l.startswith("NO DRIVER")), None)
    if start is None:
        raise Fail("순위표 머리글(NO DRIVER …)을 찾지 못함")
    rows, nc, i = [], False, start + 1
    while i < len(lines):
        l = lines[i].strip()
        if l.startswith("FASTEST LAP") or l.startswith("* PENALTIES") or l.startswith("NOTES") or l.startswith("Timekeeper"):
            break
        if l == "NOT CLASSIFIED":
            nc = True; i += 1; continue
        if not l:
            i += 1; continue
        m = (ROW1_NC if nc else ROW1).match(l)
        if not m:
            raise Fail(f"드라이버 줄을 해석하지 못함: {l!r}")
        if i + 1 >= len(lines):
            raise Fail("팀/기록 줄이 없음")
        l2 = lines[i + 1].strip()
        m2 = (ROW2_NC if nc else ROW2).match(l2)
        if not m2:
            raise Fail(f"기록 줄을 해석하지 못함: {l2!r}")
        g = m2.groupdict()
        rows.append({
            "pos": None if nc else int(m.group("pos")), "no": int(m.group("no")), "name": " ".join(m.group("name").split()),
            "team": g["team"], "laps": int(g["laps"]), "time": g["time"], "nc": nc,
            "status": g.get("status") if nc else None, "rest": g.get("rest"),
            "fast": g["fast"], "on": int(g["on"]), "pts": int(g["pts"]) if g.get("pts") else 0,
        })
        i += 2
    return rows


def _letters(s):
    import unicodedata
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower())


def names_match(fia_family, jolpica_family):
    a, b = _letters(fia_family), _letters(jolpica_family)
    return bool(a) and bool(b) and (a in b or b in a)


def split_name(name):
    """'Kimi ANTONELLI' → ('Kimi', 'ANTONELLI'). 성은 대문자 단어들."""
    words = name.split()
    fam = [w for w in words if w.isupper() or (len(w) > 1 and w.upper() == w)]
    given = [w for w in words if w not in fam]
    return " ".join(given), " ".join(fam)


# ---------------------------------------------------------------- Jolpica 매칭

def season_driver_maps(year, through_round, include_round):
    """차번 → Jolpica 드라이버 항목, 드라이버/팀 이름 보강용. 이전 라운드 결과(+필요하면 대상 라운드)에서 모은다."""
    by_no, team_by_driver = {}, {}
    drivers = jolpica(f"{year}/drivers.json?limit=100")["DriverTable"]["Drivers"]
    for d in drivers:
        if d.get("permanentNumber"):
            by_no[int(d["permanentNumber"])] = d
    # 시즌 전체 결과를 100건씩 받아(5~6페이지) 차번 → 드라이버, 드라이버 → 팀을 만든다
    offset = 0
    while True:
        md = jolpica(f"{year}/results.json?limit=100&offset={offset}")
        for race in md["RaceTable"]["Races"]:
            if int(race["round"]) > (through_round if include_round else through_round - 1):
                continue
            for e in race["Results"]:
                by_no[int(e["number"])] = e["Driver"]
                team_by_driver[e["Driver"]["driverId"]] = e["Constructor"]["name"]
        offset += 100
        if offset >= int(md["total"]):
            break
    constructors = [c["name"] for c in jolpica(f"{year}/constructors.json?limit=100")["ConstructorTable"]["Constructors"]]
    return by_no, team_by_driver, constructors


def match_constructor(entrant, names):
    """FIA 팀 이름('Oracle Red Bull Racing') → Jolpica 팀 이름('Red Bull'): 그 팀 이름의 단어가 모두 들어 있는 것 중 가장 긴 것."""
    norm = lambda s: re.sub(r"formula 1", "f1", s.lower())
    ent = set(re.findall(r"[a-z0-9]+", norm(entrant)))
    best, best_len = None, 0
    for n in names:
        words = [w for w in re.findall(r"[a-z0-9]+", norm(n)) if w not in ("f1", "team")]
        if words and all(w in ent for w in words) and len(words) > best_len:
            best, best_len = n, len(words)
    return best


def build(year, rnd, rows, source, status, include_round_in_maps):
    by_no, team_by_driver, constructors = season_driver_maps(year, rnd, include_round_in_maps)
    # 검증 1: 출전 인원 = 직전 라운드 결과 수
    prev = jolpica(f"{year}/{rnd - 1}/results.json?limit=100")["RaceTable"]["Races"] if rnd > 1 else []
    expected = len(prev[0]["Results"]) if prev else None
    if expected and len(rows) != expected:
        raise Fail(f"드라이버 수 불일치: FIA {len(rows)}명, 직전 라운드 {expected}명")
    # 검증 2: 순위 중복·연속
    poss = [r["pos"] for r in rows if not r["nc"]]
    if len(set(poss)) != len(poss) or poss != list(range(1, len(poss) + 1)):
        raise Fail(f"순위가 연속이 아니거나 중복: {poss}")
    nos = [r["no"] for r in rows]
    if len(set(nos)) != len(nos):
        raise Fail("차번 중복")
    winner = rows[0]
    winner_ms = to_ms(winner["time"])
    # 최고 랩 순위(기록이 빠른 순)
    ranked = sorted(rows, key=lambda r: (to_ms(r["fast"]), r["on"]))
    rank_of = {r["no"]: i + 1 for i, r in enumerate(ranked)}
    out, position = [], 0
    for r in rows:
        d = by_no.get(r["no"])
        if d is None:   # 검증 3: 차번 → 드라이버
            raise Fail(f"차번 {r['no']}({r['name']})을 Jolpica 드라이버 목록에서 찾지 못함")
        given, family = split_name(r["name"])
        if not names_match(family, d["familyName"]):   # 차번이 바뀌었는지 이름으로 교차 확인
            raise Fail(f"차번 {r['no']}: FIA '{r['name']}' ≠ Jolpica '{d['givenName']} {d['familyName']}'")
        position += 1
        if r["nc"]:
            pos_text, status_txt, time = "R", "Retired", None
        else:
            pos_text = str(r["pos"])
            lapped = r["laps"] < winner["laps"]
            status_txt = "Lapped" if lapped else "Finished"
            time = r["time"] if r["pos"] == 1 else fmt_gap(to_ms(r["time"]) - winner_ms)
        team = team_by_driver.get(d["driverId"]) or match_constructor(r["team"], constructors) or r["team"]
        points = float(POINTS[r["pos"] - 1]) if (not r["nc"] and r["pos"] <= len(POINTS)) else 0.0
        item = {
            "position": position, "positionText": pos_text, "driverId": d["driverId"],
            "driverCode": d.get("code") or family[:3].upper(), "givenName": d["givenName"], "familyName": d["familyName"],
            "constructorName": team, "grid": 0, "laps": r["laps"], "status": status_txt, "time": time, "points": points,
            "fastestLap": {"rank": rank_of[r["no"]], "lap": r["on"], "time": r["fast"]},
        }
        if time is None:
            del item["time"]
        out.append(item)
    return {
        "status": status, "season": year, "round": rnd, "source": source,
        "generatedAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "race": out,
    }


# ---------------------------------------------------------------- 대조

def compare(result, jol):
    """보완 결과 vs Jolpica Results → 차이 목록(빈 목록이면 같음)."""
    diffs = []
    by_id = {e["Driver"]["driverId"]: e for e in jol}
    if len(result["race"]) != len(jol):
        diffs.append(f"인원 {len(result['race'])} ≠ {len(jol)}")
    for r in result["race"]:
        e = by_id.get(r["driverId"])
        if not e:
            diffs.append(f"{r['driverId']} Jolpica에 없음"); continue
        checks = [
            ("position", r["position"], int(e["position"])), ("positionText", r["positionText"], e["positionText"]),
            ("laps", r["laps"], int(e["laps"])), ("points", r["points"], float(e["points"])),
            ("time", r.get("time"), (e.get("Time") or {}).get("time")), ("constructor", r["constructorName"], e["Constructor"]["name"]),
        ]
        fl = e.get("FastestLap")
        if fl:
            checks += [("fl.time", r["fastestLap"]["time"], fl["Time"]["time"]), ("fl.lap", r["fastestLap"]["lap"], int(fl["lap"])),
                       ("fl.rank", r["fastestLap"]["rank"], int(fl["rank"]))]
        for name, a, b in checks:
            if a != b:
                diffs.append(f"{r['driverId']} {name}: 보완={a!r} Jolpica={b!r}")
    return diffs


# ---------------------------------------------------------------- 저장·manifest

def same_content(a, b):
    strip = lambda d: {k: v for k, v in d.items() if k != "generatedAt"}
    return strip(a) == strip(b)


def write_result(root, result):
    path = os.path.join(root, "results", f"{result['season']}-{result['round']}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        old = json.load(open(path))
        if old.get("status") == "final" and result["status"] == "provisional":
            return False   # final을 provisional로 되돌리지 않는다
        if same_content(old, result):
            return False   # 달라진 게 없으면 쓰지 않는다(불필요한 커밋·버전 증가 방지)
    with open(path, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2); f.write("\n")
    return True


def rebuild_manifest(root):
    subprocess.run([sys.executable, os.path.join(root, "tools", "make-manifest.py"), root], check=True)


# ---------------------------------------------------------------- main

def run_round(year, rnd, name, root, force, write, compare_with_jolpica=False):
    jol = jolpica_race_results(year, rnd)
    if jol and not (force or compare_with_jolpica):
        print(f"R{rnd} {name}: Jolpica 결과가 있어 아무것도 하지 않는다.")
        return "skip"
    found = find_classification(year, name)
    if not found:
        print(f"R{rnd} {name}: FIA 순위 문서가 아직 없다.")
        return "none"
    title, url, kind = found
    print(f"R{rnd} {name}: {title} → {url}")
    existing = os.path.join(root, "results", f"{year}-{rnd}.json")
    if write and not compare_with_jolpica and os.path.exists(existing):
        old = json.load(open(existing))
        if old.get("status") == "final" and kind == "provisional":
            print("이미 final 보완 파일이 있다."); return "skip"
    text = pdf_text(get(url, binary=True))
    rows = parse_classification(text)
    result = build(year, rnd, rows, url, kind, include_round_in_maps=bool(jol))
    if compare_with_jolpica:
        diffs = compare(result, jol)
        print(f"대조: 드라이버 {len(result['race'])}명, 차이 {len(diffs)}건")
        for d in diffs: print("  -", d)
        return "diff" if diffs else "same"
    if write:
        if write_result(root, result):
            rebuild_manifest(root)
            print(f"results/{year}-{rnd}.json 작성({kind}) + manifest 갱신")
            return "wrote"
        print("달라진 내용이 없어 쓰지 않는다.")
        return "same"
    print(json.dumps(result, ensure_ascii=False, indent=2)[:1500])
    return "dry"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    ap.add_argument("--now")
    ap.add_argument("--compare", type=int, help="그 라운드의 FIA 문서를 Jolpica 결과와 대조만 한다")
    ap.add_argument("--round", type=int)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--error-file", default=os.environ.get("FETCH_RESULTS_ERROR_FILE", "/tmp/fetch-results-error.txt"))
    a = ap.parse_args()
    now = dt.datetime.fromisoformat(a.now.replace("Z", "+00:00")) if a.now else dt.datetime.now(dt.timezone.utc)
    year = now.year
    try:
        rounds = schedule(year)
        if a.compare:
            r = next(x for x in rounds if x["round"] == a.compare)
            res = run_round(year, r["round"], r["name"], a.root, True, False, compare_with_jolpica=True)
            sys.exit(0 if res == "same" else 1)
        targets = [r for r in rounds if r["round"] == a.round] if a.round else target_rounds(rounds, now)
        if not targets:
            print("대상 라운드 없음(결승 종료 후 72시간 이내인 라운드가 없다)."); return
        for r in targets:
            run_round(year, r["round"], r["name"], a.root, a.force, not a.dry_run)
    except Fail as e:
        msg = f"fetch-results 실패: {e}"
        print(msg, file=sys.stderr)
        with open(a.error_file, "w") as f:
            f.write(msg + "\n")
        sys.exit(2)


if __name__ == "__main__":
    main()
