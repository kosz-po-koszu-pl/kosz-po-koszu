"""Kosz po Koszu: pobieranie meczów z 7 rozgrywek do wspólnego formatu.

Użycie: python fetch.py OUT_DIR [liga ...]   (ligi: EL EC PLK BCL ACB GBL BBL)
Jeśli OUT_DIR/<liga>.json istnieje, rozegrane mecze z niego nie są pobierane ponownie.

Format meczu:
  {id, d: data UTC ISO, h: gospodarz, a: gość, st: "F" rozegrany | "L" trwa | "S" zaplanowany,
   hs/as: {pts, q:[pkt w kwartach + dogrywki], f2m,f2a,f3m,f3a,ftm,fta, orb, tov}}
"""
import json, os, re, sys, time, html as htmlmod
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
NOW = datetime.now(timezone.utc)
AHEAD = timedelta(days=10)


SLOW = ("euroleague.net",)   # serwery z limitem zapytań: odpytujemy je wolniej


def get(url, tries=6):
    if any(h in url for h in SLOW):
        time.sleep(1.2)
    for i in range(tries):
        try:
            with urlopen(Request(url, headers={"User-Agent": UA}), timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            if i == tries - 1:
                raise
            wait = 2
            if getattr(e, "code", None) == 429:     # serwer mówi, ile odczekać (Retry-After)
                try:
                    wait = min(240, int(e.headers.get("Retry-After") or 60)) + 5
                except Exception:
                    wait = 60
            time.sleep(wait)


def getj(url):
    return json.loads(get(url))


def stat(pts, q, f2m, f2a, f3m, f3a, ftm, fta, orb, tov):
    if sum(q) < pts:  # źródło nie rozpisuje dogrywki
        q = q + [pts - sum(q)]
    return {"pts": pts, "q": q, "f2m": f2m, "f2a": f2a, "f3m": f3m, "f3a": f3a,
            "ftm": ftm, "fta": fta, "orb": orb, "tov": tov}


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def upcoming(dt):
    return NOW - timedelta(hours=12) <= dt <= NOW + AHEAD  # trwające i niedawne mecze bez wyniku zostają na liście



# ---------- Zawodnicy ----------
# Każdy mecz dostaje listy hp/ap: [[zawodnik, punkty, sekundy gry], ...] (tylko ci, którzy grali).
# Błąd w odczycie zawodników nigdy nie blokuje zapisu wyniku meczu.

def secs(v):
    if isinstance(v, (int, float)):
        return int(v)
    parts = [int(x) for x in re.findall(r"\d+", str(v or ""))]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return 0


def lines(rows):
    out = []
    for r in rows:
        n, p, t = r[0], r[1], r[2]
        f = r[3] if len(r) > 3 else None
        if n and t > 0:
            out.append([n.strip(), int(p or 0), int(t), None if f is None else int(f or 0)])
    return sorted(out, key=lambda x: -x[1])


def safe(fn, *a):
    try:
        return fn(*a)
    except Exception:
        return None


def pl_el(b):
    return [lines((x["Player"], x.get("Points"), secs(x.get("Minutes")), x.get("FoulsCommited")) for x in s["PlayersStats"]) for s in b["Stats"][:2]]


def pl_plk(s):
    names = {}
    for m in re.finditer(r'"id":(\d+),"firstName":"([^"]*)","lastName":"([^"]*)"', s):
        names.setdefault(m.group(1), f"{m.group(2)} {m.group(3)}")
    for m in re.finditer(r'"firstName":"([^"]*)","lastName":"([^"]*)"[^{}]*?"playerId":(\d+)', s):
        names.setdefault(m.group(3), f"{m.group(1)} {m.group(2)}")
    for m in re.finditer(r'"playerId":(\d+)[^{}]*?"firstName":"([^"]*)","lastName":"([^"]*)"', s):
        names.setdefault(m.group(1), f"{m.group(2)} {m.group(3)}")
    out = []
    for bm in list(re.finditer(r'"gameStatistics":', s))[:2]:
        b = json.JSONDecoder().raw_decode(s, bm.end())[0]
        out.append(lines((names.get(str(r["playerId"]), str(r["playerId"])), r.get("points"), r.get("playTimeSeconds") or 0,
                          r.get("foulsPersonal")) for r in b["playersStatistics"]))
    return out


def pl_plk_live(d):
    return [lines((p.get("name") or f"{p.get('firstName', '')} {p.get('familyName', '')}", p.get("sPoints"), secs(p.get("sMinutes")), p.get("sFoulsPersonal"))
                  for p in (d["tm"][k].get("pl") or {}).values()) for k in ("1", "2")]


def pl_bcl(det, h):
    out = []
    for c in det["c"][:2]:
        rows = []
        for ch in c.get("Children", []):
            pid = ch["Id"][2:]
            m = re.search(r'"personId":%s,"firstName":"([^"]*)","lastName":"([^"]*)"' % pid, h)
            st = ch.get("Stats") or {}
            name = f"{m.group(1)} {m.group(2)}" if m else (ch.get("Name") or pid)
            rows.append((name, st.get("PTS"), secs(st.get("TP")), st.get("PF")))
        out.append(lines(rows))
    return out


def pl_acb(rows):
    out, cur = [], []
    for r in rows:
        if r and r[0] == "Totales":
            out.append(lines(cur))
            cur = []
            if len(out) == 2:
                break
        elif len(r) > 2 and re.match(r"^\d+\s+\S", r[0]) and re.match(r"^\d+:\d\d$", r[1]):
            cur.append((re.sub(r"^\d+\s+", "", r[0]), int(r[2]) if r[2].isdigit() else 0, secs(r[1]),
                        int(r[18]) if len(r) > 18 and r[18].isdigit() else None))
    return out if len(out) == 2 else None


def pl_gbl(txt):
    parts = txt.split("ΑΝΑΛΥΤΙΚΑ ΣΤΑΤΙΣΤΙΚΑ")[1:3]
    rx = re.compile(r"#\d+ (.+?) (\d+) \d+ - \d+ \d+ - \d+ \d+ - \d+ (?:\d+ ){7}(\d+) (?:\d+ ){2}(\d\d:\d\d:\d\d)")
    out = [lines((m.group(1), int(m.group(2)), secs(m.group(4)), int(m.group(3))) for m in rx.finditer(p.split("ΣΥΝΟΛΟ")[0])) for p in parts]
    return out if len(out) == 2 else None


def pl_bbl(gs):
    return [lines((f"{p['seasonPlayer']['firstName']} {p['seasonPlayer']['lastName']}", p.get("points"), p.get("secondsPlayed") or 0, p.get("foulsCommitted"))
                  for p in gs[k].get("playerStats", [])) for k in ("homeTeam", "guestTeam")]


def pl_nba(summary, home_name):
    out = {}
    for t in summary["boxscore"]["players"]:
        st = t["statistics"][0]
        i_min, i_pts = st["names"].index("MIN"), st["names"].index("PTS")
        i_pf = st["names"].index("PF") if "PF" in st["names"] else None
        rows = []
        for a in st["athletes"]:
            v = a.get("stats") or []
            if a.get("didNotPlay") or not v:
                continue
            rows.append((a["athlete"]["displayName"], int(v[i_pts]) if str(v[i_pts]).isdigit() else 0,
                         int(v[i_min]) * 60 if str(v[i_min]).isdigit() else 0,
                         int(v[i_pf]) if i_pf is not None and str(v[i_pf]).isdigit() else None))
        out["h" if t["team"]["displayName"] == home_name else "a"] = lines(rows)
    return [out.get("h"), out.get("a")]


def add_players(rec, pls):
    if pls and len(pls) == 2 and pls[0] and pls[1]:
        rec["hp"], rec["ap"] = pls[0], pls[1]
    return rec


def update_players(doc):
    """Stała historia zawodników ligi: players[drużyna][zawodnik] = [mecze, punkty, sekundy, data ostatniego meczu]."""
    pl, seen = doc.setdefault("players", {}), set(doc.setdefault("pgames", []))
    for g in sorted(doc["games"], key=lambda g: g["d"]):
        if g.get("st") != "F" or g["id"] in seen or not g.get("hp") or g["d"] < f"{season_year(10)}-07-01":
            continue
        for team, key in ((g["h"], "hp"), (g["a"], "ap")):
            t = pl.setdefault(team, {})
            for name, pts, sec, *_ in g.get(key) or []:
                r = t.setdefault(name, [0, 0, 0, ""])
                r[0] += 1
                r[1] += pts
                r[2] += sec
                r[3] = g["d"][:10]
        seen.add(g["id"])
    doc["pgames"] = sorted(seen)
    # Listy zawodników przy meczach trzymamy tylko w 3 ostatnich meczach drużyny (historia jest w players)
    fin = sorted((g for g in doc["games"] if g.get("st") == "F"), key=lambda g: g["d"], reverse=True)
    cnt = {}
    for g in fin:
        keep = False
        for t in (g["h"], g["a"]):
            cnt[t] = cnt.get(t, 0) + 1
            keep = keep or cnt[t] <= 3
        if not keep:
            g.pop("hp", None)
            g.pop("ap", None)
    return doc


def fix_quarters(g):
    """Wynik i kwarty bywają odświeżane przez źródło w różnych chwilach: brakujące punkty dopisujemy do bieżącej kwarty."""
    for k in ("hs", "as"):
        t = g.get(k) or {}
        q = t.get("q") or []
        d = (t.get("pts") or 0) - sum(x or 0 for x in q)
        if q and d and (q[-1] or 0) + d >= 0:
            q[-1] = (q[-1] or 0) + d


def clk_min(c):
    """Minuty do końca kwarty z zegara "MM:SS" (99, gdy nie da się odczytać)."""
    m = re.match(r"(\d+):(\d+)", str(c or ""))
    return int(m.group(1)) + int(m.group(2)) / 60 if m else 99


SNAP_KEYS = ("pts", "q", "f2m", "f2a", "f3m", "f3a", "ftm", "fta", "orb", "tov")


def snap_shots(old, new):
    """Stan meczu (wynik, rzuty, zegar) na koniec 2. i 3. kwarty, do nauki przeliczania na żywo.
    Bierzemy odczyt z zegarem 00:00 tej kwarty, a gdy go nie było, ostatni odczyt przed jej końcem (z jego zegarem).
    Bez zegara (np. liga grecka) nie zapisujemy, bo nie wiadomo, ile czasu zostało."""
    sq = dict(new.get("sq") or old.get("sq") or {})
    snap = lambda g: {"per": g.get("per"), "clk": g.get("clk"),
                      **{k: {x: (g.get(k) or {}).get(x) for x in SNAP_KEYS} for k in ("hs", "as")}}
    for k in (2, 3):
        if new.get("per") == k and new.get("clk") in ("00:00", "0:00"):
            sq[str(k)] = snap(new)
        elif (str(k) not in sq and old.get("st") == "L" and old.get("per") == k and old.get("clk")
              and (new.get("per") or 0) > k and clk_min(old.get("clk")) < 5):
            sq[str(k)] = snap(old)
    if sq:
        new["sq"] = sq


def snap_quarters(old, new):
    """Punkty zawodników po każdej zakończonej kwarcie (tylko mecze śledzone na żywo)."""
    if not new.get("hp"):
        return
    pq = {"h": dict((old.get("pq") or {}).get("h", {})), "a": dict((old.get("pq") or {}).get("a", {}))}
    op, np_ = old.get("per") or 1, new.get("per") or 1
    # Kwarta zakończona od ostatniego odczytu na żywo. Gdy śledzenie zaczęło się w trakcie meczu albo odczyt
    # przeskoczył kilka kwart, wcześniejszych stanów nie znamy, więc ich nie zgadujemy.
    ended = [np_ - 1] if old.get("st") == "L" and np_ > op else []
    if new.get("clk") in ("00:00", "0:00") or new.get("st") == "F":
        ended.append(np_)
    for k in ended:
        for side, key in (("h", "hp"), ("a", "ap")):
            for name, pts, *_ in new.get(key) or []:
                d = dict(pq[side].get(name, {}))
                d.setdefault(str(k), pts)
                pq[side][name] = d
    new["pq"] = pq


# ---------- Euroliga / EuroCup ----------

def euroleague(comp, known):
    season = f"{comp}{NOW.year if NOW.month >= 7 else NOW.year - 1}"
    games = getj(f"https://api-live.euroleague.net/v2/competitions/{comp}/seasons/{season}/games")["data"]
    out = []
    for g in games:
        gid = str(g["gameCode"])
        dt = datetime.fromisoformat(g["utcDate"].replace("Z", "+00:00"))
        rec = {"id": gid, "d": iso(dt), "h": g["local"]["club"]["name"], "a": g["road"]["club"]["name"]}
        if g["played"]:
            if gid in known:
                out.append(known[gid])
                continue
            b = getj(f"https://live.euroleague.net/api/Boxscore?gamecode={gid}&seasoncode={season}")
            if b.get("Live"):
                continue
            sides = []
            for i, s in enumerate(b["Stats"][:2]):
                t = s["totr"]
                bq = b["ByQuarter"][i]
                q = [bq.get(f"Quarter{n}") or 0 for n in range(1, 5)]
                q += [v for k, v in sorted(bq.items()) if k.startswith("Extra") and v]
                sides.append(stat(t["Points"], q, t["FieldGoalsMade2"], t["FieldGoalsAttempted2"],
                                  t["FieldGoalsMade3"], t["FieldGoalsAttempted3"], t["FreeThrowsMade"],
                                  t["FreeThrowsAttempted"], t["OffensiveRebounds"], t["Turnovers"]))
            rec.update(st="F", hs=sides[0], **{"as": sides[1]})
            out.append(add_players(rec, safe(pl_el, b)))
        elif upcoming(dt):
            rec["st"] = "S"
            out.append(rec)
    return out


def next_json(h, marker):
    """Obiekt JSON zaczynający się zaraz po `marker` w stronie Next.js (cudzysłowy odescapowane)."""
    i = h.find(marker)
    if i < 0:
        return None
    return json.JSONDecoder().raw_decode(h, i + len(marker))[0]


def rsc(h):
    return h.replace('\\"', '"')


def eu_local(y, mo, d, hh, mi, base):
    """Czas lokalny CET (base=1) lub EET (base=2) z letnim przesunięciem UE -> UTC."""
    def last_sunday(month):
        t = datetime(y, month, 31)
        return t - timedelta(days=(t.weekday() + 1) % 7)
    dt = datetime(y, mo, d, hh, mi)
    summer = last_sunday(3) + timedelta(hours=1 + base) <= dt < last_sunday(10) + timedelta(hours=1 + base)
    return (dt - timedelta(hours=base + summer)).replace(tzinfo=timezone.utc)


def season_year(month):
    start = NOW.year if NOW.month >= 7 else NOW.year - 1
    return start if month >= 7 else start + 1


# ---------- ORLEN Basket Liga ----------

def plk(known):
    h = rsc(get("https://plk.pl/terminarz"))
    seen, out = set(), []
    for m in re.finditer(r'\{"id":"(\d+)","seasonId"', h):
        try:
            g = json.JSONDecoder().raw_decode(h, m.start())[0]
        except ValueError:
            continue
        gid = g["id"]
        if gid in seen or "date" not in g:
            continue
        seen.add(gid)
        dt = datetime.fromisoformat(g["date"].replace("Z", "+00:00"))
        rec = {"id": gid, "d": iso(dt), "h": g["homeTeamName"], "a": g["guestTeamName"]}
        if g.get("isFinished"):
            if gid in known:
                out.append(known[gid])
                continue
            s = rsc(get(f"https://plk.pl/mecz/{gid}/{g['slug']}/statystyki"))
            blocks = []
            for bm in re.finditer(r'"gameStatistics":', s):
                blocks.append(json.JSONDecoder().raw_decode(s, bm.end())[0])
                if len(blocks) == 2:
                    break
            q = [[int(x) for x in p.split(":")] for p in g.get("quarters") or []]
            sides = []
            for i, b in enumerate(blocks):
                rows = b["playersStatistics"] + [b["teamStatistics"]]
                t = lambda k: sum(r.get(k) or 0 for r in rows)
                sides.append(stat(t("points"), [p[i] for p in q], t("madeTwoPts"), t("attemptTwoPts"),
                                  t("madeThreePts"), t("attemptThreePts"), t("madeFreeThrowPts"),
                                  t("attemptFreeThrowPts"), t("reboundsOffensive"), t("turnovers")))
            if len(sides) == 2:
                rec.update(st="F", hs=sides[0], **{"as": sides[1]})
                out.append(add_players(rec, safe(pl_plk, s)))
        elif upcoming(dt):
            rec["st"] = "S"
            out.append(rec)
    return out


# ---------- Liga Mistrzów FIBA ----------

BCL = "https://www.championsleague.basketball/en/games"


def bcl(known):
    h = rsc(get(BCL))
    seen, out = set(), []
    for m in re.finditer(r'\{"gameId":(\d+),"gameName"', h):
        try:
            g = json.JSONDecoder().raw_decode(h, m.start())[0]
        except ValueError:
            continue
        gid = str(g["gameId"])
        if gid in seen or not g.get("gameDateTimeUTC") or not g.get("teamA") or not g.get("teamB"):
            continue
        seen.add(gid)
        dt = datetime.fromisoformat(g["gameDateTimeUTC"]).replace(tzinfo=timezone.utc)
        rec = {"id": gid, "d": iso(dt), "h": g["teamA"]["shortName"], "a": g["teamB"]["shortName"]}
        if g.get("liveGameStatus") == 999 and g.get("teamAScore") is not None:
            if gid in known:
                out.append(known[gid])
                continue
            page = rsc(get(f"{BCL}/{gid}-{g['teamA']['code']}-{g['teamB']['code']}"))
            det = next_json(page, '"gameDetails":')
            if not det:
                if upcoming(dt):
                    rec["st"] = "S"
                    out.append(rec)
                continue
            sides = []
            for c in det["c"][:2]:
                t = c["Stats"]
                sides.append(stat(c["Score"], [p["Score"] for p in c["Periods"]], t["FG2M"], t["FG2A"],
                                  t["FG3M"], t["FG3A"], t["FTM"], t["FTA"], t["OR"], t["TO"]))
            rec.update(st="F", hs=sides[0], **{"as": sides[1]})
            out.append(add_players(rec, safe(pl_bcl, det, page)))
        elif upcoming(dt):          # także trwające: wynik dopisze tryb na żywo albo kolejne pobranie
            rec["st"] = "S"
            out.append(rec)
    return out


# ---------- Hiszpania (ACB) ----------

ES_MONTHS = {m: i + 1 for i, m in enumerate("enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split())}


def cells_of(h):
    rows = []
    for r in re.findall(r"<tr[^>]*>(.*?)</tr>", h, re.S):
        rows.append([htmlmod.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                     for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)])
    return rows


def acb(known):
    h = get("https://acb.com/es/liga/calendario")
    # Godziny meczów są w danych wbudowanych w stronę (startDateTime w UTC), widoczny kalendarz ich nie pokazuje
    hj = h.replace('\\"', '"')
    starts = {m.group(1): (m.group(2), m.group(3)) for m in re.finditer(
        r'\{"id":(\d+),"homeTeam":"[^"]*","awayTeam":"[^"]*","homeTeamScore":\d+,"awayTeamScore":\d+,'
        r'"startDateTime":"([^"]+)","matchStatus":"(\w+)"', hj)}
    marks = [(m.start(), "date", m) for m in re.finditer(r"(\d{1,2}) de ([a-z]+) de (\d{4})", h)]
    marks += [(m.start(), "game", m) for m in re.finditer(r'roundMatch__time', h)]
    marks.sort(key=lambda x: x[0])
    day, seen, out = None, set(), []
    for k, (pos, kind, m) in enumerate(marks):
        if kind == "date":
            day = (int(m.group(3)), ES_MONTHS.get(m.group(2)), int(m.group(1)))
            continue
        end = marks[k + 1][0] if k + 1 < len(marks) else len(h)
        block = h[pos:end]
        link = re.search(r"live\.acb\.com/partidos/([a-z0-9-]+-(\d+))/", block)
        names = re.findall(r"teamName--fullName\">([^<]+)<", block)
        if not link or len(names) < 2 or not day or not day[1]:
            continue
        gid = link.group(2)
        if gid in seen:
            continue
        seen.add(gid)
        tm = re.search(r"roundMatch__time[^>]*>(\d{1,2}):(\d{2})<", block)
        st0 = starts.get(gid)
        if st0 and st0[1] != "UNSCHEDULED":
            dt, tm = datetime.fromisoformat(st0[0].replace("Z", "+00:00")), True
        else:
            dt = eu_local(*day, *(int(x) for x in tm.groups()) if tm else (18, 0), 1)
        rec = {"id": gid, "d": iso(dt), "h": htmlmod.unescape(names[0]), "a": htmlmod.unescape(names[1])}
        if not tm:
            rec["tt"] = 0          # godzina jeszcze nieogłoszona, wpisane zastępczo 18:00
        if "teamScore" in block and re.search(r"teamScore[^>]*>\d+<", block) and dt <= NOW - timedelta(hours=2, minutes=30):
            if gid in known:
                out.append(known[gid])
                continue
            base = f"https://live.acb.com/es/partidos/{link.group(1)}"
            srows = cells_of(get(base + "/estadisticas"))
            tot = [r for r in srows if r and r[0] == "Totales"][:2]
            qrows = cells_of(get(base + "/resumen"))
            qi = next((i for i, r in enumerate(qrows) if r[:2] == ["Equipo", "1C"]), None)
            if len(tot) < 2 or qi is None:
                continue
            sides = []
            for i, r in enumerate(tot):
                sp = lambda c: [int(x) for x in r[c].split("/")]
                f2, f3, ft = sp(3), sp(5), sp(7)
                q = [int(x) for x in qrows[qi + 1 + i][1:] if x.isdigit()]
                sides.append(stat(int(r[2]), q, f2[0], f2[1], f3[0], f3[1], ft[0], ft[1], int(r[9]), int(r[13])))
            rec.update(st="F", hs=sides[0], **{"as": sides[1]})
            out.append(add_players(rec, safe(pl_acb, srows)))
        elif upcoming(dt):
            rec["st"] = "S"
            out.append(rec)
    return out


# ---------- Grecja (GBL) ----------

GR_MONTHS = {"Ιαν": 1, "Φεβ": 2, "Μαρ": 3, "Απρ": 4, "Μαϊ": 5, "Μαΐ": 5, "Ιουν": 6, "Ιουλ": 7, "Αυγ": 8,
             "Σεπ": 9, "Οκτ": 10, "Νοε": 11, "Δεκ": 12}
ESAKE = "https://www.esake.gr/el/action/"


def gbl_champ():
    h = get(ESAKE + "EsakeResults")
    m = re.search(r'name="idchampionship"[^>]*>(.*?)</select>', h, re.S)
    return re.findall(r'value="([0-9A-F]{8})"', m.group(1))[0]


def gbl(known):
    champ = gbl_champ()
    out, seen, empty = [], set(), 0
    for rnd in range(1, 40):
        h = get(f"{ESAKE}EsakeResults?idchampionship={champ}&series={rnd}")
        blocks = h.split('class="esake-program-game"')[1:]
        if not blocks:
            break
        future = 0
        for b in blocks:
            idm = re.search(r"idgame=([0-9A-F]+)", b)
            dm = re.search(r"clock\.svg[^>]*>\s*\S+\s+(\d+)\s+(\S+)\s+-\s+(\d+):(\d+)", b)
            spans = re.findall(r"<span>([^<]*(?:<br\s*/?>[^<]*)*)</span>", b)
            si = next((i for i, x in enumerate(spans) if re.fullmatch(r"\s*(\d+)?&nbsp;-&nbsp;(\d+)?\s*", x)), None)
            if not idm or not dm or si is None or si == 0 or si + 1 >= len(spans):
                continue
            names = [re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<br\s*/?>", " ", spans[j]))).strip() for j in (si - 1, si + 1)]
            played = re.fullmatch(r"\s*\d+&nbsp;-&nbsp;\d+\s*", spans[si])
            gid = idm.group(1)
            if gid in seen:
                continue
            seen.add(gid)
            mo = GR_MONTHS.get(dm.group(2)[:4].rstrip(".")) or GR_MONTHS.get(dm.group(2)[:3])
            dt = eu_local(season_year(mo), mo, int(dm.group(1)), int(dm.group(3)), int(dm.group(4)), 2)
            rec = {"id": gid, "d": iso(dt), "h": names[0], "a": names[1]}
            if played and dt <= NOW - timedelta(hours=2, minutes=30):
                if gid in known:
                    out.append(known[gid])
                    continue
                g = get(f"{ESAKE}EsakegameView?idgame={gid}&mode=3")
                qs = re.findall(r"<tr><td>(\d+)</td><td>(?:Q\d|ΟΤ\d|OT\d)</td><td>(\d+)</td></tr>", g)
                txt = re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", " ", g)))
                tots = re.findall(r"ΣΥΝΟΛΟ (\d+) (\d+) - (\d+) (\d+) - (\d+) (\d+) - (\d+) (\d+) (\d+) (\d+) (\d+) (\d+) (\d+) (\d+) (\d+) (\d+) (\d+)", txt)
                if len(tots) < 2 or not qs:
                    continue
                sides = []
                for i, t in enumerate(tots[:2]):
                    t = [int(x) for x in t]
                    sides.append(stat(t[0], [int(q[i]) for q in qs], t[1], t[2], t[3], t[4], t[5], t[6], t[9], t[16]))
                rec.update(st="F", hs=sides[0], **{"as": sides[1]})
                out.append(add_players(rec, safe(pl_gbl, txt)))
            elif upcoming(dt):
                rec["st"] = "S"
                out.append(rec)
            else:
                future += 1
        if future == len(blocks):
            empty += 1
            if empty >= 2:
                break
    return out


# ---------- Niemcy (BBL) ----------

def bbl_page(gid):
    h = get(f"https://www.easycredit-bbl.de/spiele/{gid}")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', h, re.S)
    return json.loads(m.group(1))["props"]["pageProps"] if m else {}


def bbl(known):
    h = get("https://www.easycredit-bbl.de/saison/aktuelle-spiele")
    start = re.search(r'"items":\s*\[\{"id":\s*"(\d+)"', h).group(1)
    info, queue, done, out = {}, [start], set(), []
    season_start = datetime(season_year(9), 9, 1, tzinfo=timezone.utc)

    def note(g):
        if not g.get("scheduledTime"):
            return
        dt = datetime.fromisoformat(g["scheduledTime"].replace("Z", "+00:00"))
        if g["id"] not in info and season_start <= dt <= NOW + AHEAD and g.get("competition", "BBL") == "BBL":
            info[g["id"]] = g
            queue.append(g["id"])

    def load(gid):
        try:
            pp = bbl_page(gid)
        except Exception:
            return None
        for lst in (pp.get("initialRecentGames") or {}).values():
            for g in lst:
                note(g)
        gd, gs = pp.get("initialGameData"), pp.get("initialGameStats")
        if gd:
            note(gd)
            if gd.get("competition", "BBL") != "BBL":
                return gd
            if gd.get("result") and gs and (gd.get("status") == "OFFICIAL" or gd.get("gameProgress") == "E"):
                r = gd["result"]
                sides = []
                for side, key in (("home", "homeTeam"), ("guest", "guestTeam")):
                    t = gs[key]["gameStat"]
                    q = [r[f"{side}TeamQ{n}Score"] for n in range(1, 5)]
                    q += [r[f"{side}TeamOT{n}Score"] for n in range(1, 10) if r.get(f"homeTeamOT{n}Score") or r.get(f"guestTeamOT{n}Score")]
                    sides.append(stat(t["points"], q, t["twoPointShotsMade"], t["twoPointShotsAttempted"],
                                      t["threePointShotsMade"], t["threePointShotsAttempted"], t["freeThrowsMade"],
                                      t["freeThrowsAttempted"], t["offensiveRebounds"], t["turnovers"]))
                if not gd.get("scheduledTime"):
                    return gd
                known[gid] = {"id": gid, "d": iso(datetime.fromisoformat(gd["scheduledTime"].replace("Z", "+00:00"))),
                              "h": gd["homeTeam"]["name"], "a": gd["guestTeam"]["name"], "st": "F",
                              "hs": sides[0], "as": sides[1]}
                add_players(known[gid], safe(pl_bbl, gs))
        return gd

    while queue:
        gid = queue.pop()
        if gid in done:
            continue
        done.add(gid)
        if gid not in known:
            load(gid)

    # Strona pokazuje tylko bieżące kolejki, a numery meczów nie idą po kolei w czasie
    # (wcześniejsze kolejki mają wyższe numery). Sprawdzamy więc numery wokół znanych meczów,
    # aż każda drużyna ma co najmniej 5 rozegranych meczów albo przez 30 kolejnych numerów
    # nie pojawi się żaden nowy rozegrany mecz (drużyna mogła po prostu zagrać mniej).
    def short_teams():
        cnt = {}
        for g in known.values():
            for t in (g["h"], g["a"]):
                cnt[t] = cnt.get(t, 0) + 1
        teams = {t for g in info.values() for t in (g["homeTeam"]["name"], g["guestTeam"]["name"])}
        return [t for t in teams | set(cnt) if cnt.get(t, 0) < 5]

    ids = [int(i) for i in known] or [int(i) for i in info]
    for step, origin in ((1, max(ids)), (-1, min(ids))):
        gid, misses, idle = origin + step, 0, 0
        while short_teams() and misses < 25 and idle < 30 and abs(gid - origin) < 400:
            idle += 1
            if str(gid) not in known:
                before = len(known)
                gd = load(str(gid))
                if len(known) > before:
                    idle = 0
                if not gd:
                    misses += 1
                elif gd.get("scheduledTime") and datetime.fromisoformat(gd["scheduledTime"].replace("Z", "+00:00")) < season_start:
                    break
                else:
                    misses = 0
            gid += step

    out.extend(known.values())
    for gid, g in info.items():
        if gid not in known:
            dt = datetime.fromisoformat(g["scheduledTime"].replace("Z", "+00:00"))
            if upcoming(dt) and g.get("status") != "OFFICIAL" and g.get("gameProgress") != "E":
                out.append({"id": gid, "d": iso(dt), "h": g["homeTeam"]["name"], "a": g["guestTeam"]["name"], "st": "S"})
    return out


# ---------- NBA (dane ESPN; serwer nba.com odrzuca zapytania spoza przeglądarki) ----------

ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
NBA_KEEP = 500   # tyle najnowszych rozegranych meczów trzymamy (cały sezon nie zmieściłby się w bazie)


def nba_injuries():
    """Lista kontuzji ESPN: {drużyna: [[zawodnik, O/D/Q/DTD]]} (O nie zagra, D raczej nie, Q niepewny, DTD z dnia na dzień)."""
    d = json.loads(get(ESPN + "/injuries"))
    ab = {"out": "O", "doubtful": "D", "questionable": "Q", "day-to-day": "DTD"}
    out = {}
    for t in d.get("injuries", []):
        rows = [[i["athlete"]["displayName"], ab.get((i.get("status") or "").lower(), "DTD")]
                for i in t.get("injuries", []) if i.get("athlete")]
        if rows:
            out[t["displayName"]] = rows
    return out


def nba(known):
    # Pierwsze pobranie: od początku sezonu; potem tylko ostatnie dni i terminarz na 10 dni.
    # Liczy się tylko bieżący sezon (decyzja użytkownika), więc meczów z poprzedniego nie pobieramy.
    y = season_year(10)
    days = []
    if not known:
        start = datetime(y, 10, 1, tzinfo=timezone.utc)
    else:
        start = NOW - timedelta(days=3)
    d = start
    while d <= NOW + AHEAD:
        days.append(d)
        d += timedelta(days=1)
    games = {k: g for k, g in known.items() if g["d"] >= f"{y}-08-01"}
    # Zapytania równolegle: najpierw wszystkie dni, potem statystyki rozegranych meczów
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(8) as pool:
        boards = list(pool.map(lambda d: getj(f"{ESPN}/scoreboard?dates={d:%Y%m%d}"), days))
        fin_ids = [e["id"] for sb in boards for e in sb.get("events", [])
                   if e["status"]["type"]["name"] == "STATUS_FINAL" and e["id"] not in known
                   and e.get("season", {}).get("type") != 1]
        summaries = dict(zip(fin_ids, pool.map(lambda i: getj(f"{ESPN}/summary?event={i}"), fin_ids)))
    for sb in boards:
        for e in sb.get("events", []):
            c = e["competitions"][0]
            teams = {t["homeAway"]: t for t in c["competitors"]}
            if "home" not in teams or "away" not in teams or e.get("season", {}).get("type") == 1:
                continue   # pomijamy mecze przedsezonowe (grają w nich głównie rezerwowi)
            gid = e["id"]
            dt = datetime.fromisoformat(e["date"].replace("Z", "+00:00"))
            rec = {"id": gid, "d": iso(dt), "h": teams["home"]["team"]["displayName"], "a": teams["away"]["team"]["displayName"]}
            state = e["status"]["type"]["name"]
            if state == "STATUS_FINAL":
                if gid in known:
                    continue
                box = summaries[gid]["boxscore"]["teams"]
                st = {t["homeAway"]: {s["name"]: s["displayValue"] for s in t["statistics"]} for t in box}
                sides = []
                for side in ("home", "away"):
                    s = st[side]
                    fg = [int(x) for x in s["fieldGoalsMade-fieldGoalsAttempted"].split("-")]
                    f3 = [int(x) for x in s["threePointFieldGoalsMade-threePointFieldGoalsAttempted"].split("-")]
                    ft = [int(x) for x in s["freeThrowsMade-freeThrowsAttempted"].split("-")]
                    q = [int(l["value"]) for l in teams[side].get("linescores", [])]
                    sides.append(stat(int(teams[side]["score"]), q, fg[0] - f3[0], fg[1] - f3[1], f3[0], f3[1], ft[0], ft[1],
                                      int(s.get("offensiveRebounds", 0)), int(s.get("totalTurnovers", s.get("turnovers", 0)))))
                games[gid] = add_players(dict(rec, st="F", hs=sides[0], **{"as": sides[1]}), safe(pl_nba, summaries[gid], rec["h"]))
            elif state == "STATUS_SCHEDULED" and upcoming(dt):
                games[gid] = dict(rec, st="S")
    # Przycinanie: najwyżej NBA_KEEP najnowszych rozegranych meczów. Liczba nie maleje między pobraniami
    # (poranne zadanie odrzuca ligę, w której ubyło rozegranych meczów), a dokument mieści się w bazie.
    fin = sorted((g for g in games.values() if g["st"] == "F"), key=lambda g: g["d"], reverse=True)
    keep = {g["id"] for g in fin[:NBA_KEEP]}
    return [g for g in games.values() if g["st"] != "F" and upcoming(datetime.fromisoformat(g["d"].replace("Z", "+00:00"))) or g["id"] in keep]


LEAGUES = {
    "EL": ("Euroliga", lambda k: euroleague("E", k)),
    "EC": ("EuroCup", lambda k: euroleague("U", k)),
    "PLK": ("ORLEN Basket Liga", plk),
    "BCL": ("Liga Mistrzów FIBA", bcl),
    "ACB": ("Liga ACB", acb),
    "GBL": ("Grecka GBL", gbl),
    "BBL": ("Niemiecka BBL", bbl),
    "NBA": ("NBA", nba),
}


# ---------- Na żywo ----------
# Każda funkcja dostaje mecze ligi, które już się zaczęły i nie mają wyniku końcowego,
# a zwraca {id: zaktualizowany mecz}. Mecz trwający ma st="L", per = bieżąca kwarta
# (5 i więcej to dogrywki), clk = czas do końca kwarty "MM:SS" albo None, gdy źródło go nie podaje.

def live_euroleague(comp, games):
    season = f"{comp}{season_year(9)}"
    out = {}
    for g in games:
        b = getj(f"https://live.euroleague.net/api/Boxscore?gamecode={g['id']}&seasoncode={season}")
        if not b.get("Stats"):
            continue
        sides = []
        for i, s in enumerate(b["Stats"][:2]):
            t, bq = s["totr"], b["ByQuarter"][i]
            q = [bq.get(f"Quarter{n}") for n in range(1, 5)] + [v for k, v in sorted(bq.items()) if k.startswith("Extra") and v]
            sides.append(stat(t["Points"], [v or 0 for v in q if v is not None], t["FieldGoalsMade2"], t["FieldGoalsAttempted2"],
                              t["FieldGoalsMade3"], t["FieldGoalsAttempted3"], t["FreeThrowsMade"],
                              t["FreeThrowsAttempted"], t["OffensiveRebounds"], t["Turnovers"]))
        if not sides[0]["pts"] and not sides[1]["pts"]:
            continue
        # Bieżąca kwarta i zegar z nagłówka meczu (GameTime = minuty od początku, RemainingPartialTime = do końca kwarty)
        hd = getj(f"https://live.euroleague.net/api/Header?gamecode={g['id']}&seasoncode={season}")
        mm, ss = (int(x) for x in (str(hd.get("GameTime") or "0:0").split(":") + ["0"])[:2])
        el = mm + ss / 60
        per = (min(4, max(1, -(-el // 10))) if el <= 40 else 4 + int(-(-(el - 40) // 5))) if el else 1
        per = int(per)
        # Na zmianie kwart GameTime spóźnia się względem zegara kwarty. Minuty gry + czas do końca kwarty
        # to zawsze pełna kwarta (10, 20, 30, 40, 45...), więc z tej sumy ustalamy kwartę.
        rp = re.match(r"(\d+):(\d+)", str(hd.get("RemainingPartialTime") or ""))
        if rp and el:
            tot = el + int(rp.group(1)) + int(rp.group(2)) / 60
            per = max(1, round(tot / 10)) if tot <= 40.5 else 4 + max(1, round((tot - 40) / 5))
        for s in sides:
            s["q"] = (s["q"] + [0] * per)[:per]
        live = b.get("Live") or hd.get("Live")
        out[g["id"]] = dict(g, st="L" if live else "F", per=per,
                            clk=(hd.get("RemainingPartialTime") or None) if live else None, hs=sides[0], **{"as": sides[1]})
        add_players(out[g["id"]], safe(pl_el, b))
    return out


def trim_future(sides, per_hint=None):
    """W trakcie meczu źródło może pokazywać przyszłe kwarty jako 0: obcinamy końcowe kwarty bez punktów obu drużyn."""
    q1, q2 = sides[0]["q"], sides[1]["q"]
    n = max(len(q1), len(q2))
    while n > 1 and not (q1[n-1:n] and q1[n-1]) and not (q2[n-1:n] and q2[n-1]):
        n -= 1
    for s in sides:
        s["q"] = s["q"][:n]
    return n


def live_plk(games):
    h = rsc(get("https://plk.pl/terminarz"))
    ids = sorted(set(re.findall(r"geniussports\.com/u/POL/(\d+)", h)))
    by_home = {g["h"]: g for g in games}
    out = {}
    for lid in ids:
        d = getj(f"https://fibalivestats.dcd.shared.geniussports.com/data/{lid}/data.json")
        t1, t2 = d["tm"]["1"], d["tm"]["2"]
        g = by_home.get(t1.get("name"))
        if not g:
            continue
        sides = []
        for t in (t1, t2):
            q = [t.get(f"p{n}_score") for n in range(1, 10)]
            v = lambda k: t.get("tot_s" + k) or 0
            sides.append(stat(t.get("score") or 0, [x for x in q if x is not None], v("TwoPointersMade"), v("TwoPointersAttempted"),
                              v("ThreePointersMade"), v("ThreePointersAttempted"), v("FreeThrowsMade"),
                              v("FreeThrowsAttempted"), v("ReboundsOffensive"), v("Turnovers")))
        per = d.get("period") or 1
        if d.get("inOT"):
            per += 4 if per < 5 else 0
        done = d.get("clock") in ("00:00", "0:00") and per >= 4 and sides[0]["pts"] != sides[1]["pts"]
        out[g["id"]] = dict(g, st="F" if done else "L", per=per, clk=d.get("clock"), hs=sides[0], **{"as": sides[1]})
        add_players(out[g["id"]], safe(pl_plk_live, d))
    return out


def live_bcl(games):
    h = rsc(get(BCL))
    want = {g["id"]: g for g in games}
    out = {}
    for m in re.finditer(r'\{"gameId":(\d+),"gameName"', h):
        gid = m.group(1)
        if gid not in want:
            continue
        try:
            s = json.JSONDecoder().raw_decode(h, m.start())[0]
        except ValueError:
            continue
        # W trakcie meczu strona meczu nie ma statystyk; strona dociąga je z tego adresu
        det = json.loads(get(f"https://www.championsleague.basketball/en/events/api/game-live-info/{gid}/detail"))["game"]["content"]
        if not det or len(det.get("c") or []) < 2:
            continue
        page = safe(lambda: rsc(get(f"{BCL}/{gid}-{s['teamA']['code']}-{s['teamB']['code']}"))) or ""   # pełne nazwiska
        sides = []
        for c in det["c"][:2]:
            t = c.get("Stats") or {}
            sides.append(stat(c.get("Score") or 0, [p["Score"] for p in c.get("Periods", []) if p.get("Score") is not None],
                              t.get("FG2M", 0), t.get("FG2A", 0), t.get("FG3M", 0), t.get("FG3A", 0),
                              t.get("FTM", 0), t.get("FTA", 0), t.get("OR", 0), t.get("TO", 0)))
        cp = str(det.get("CurrentPeriod") or s.get("currentPeriod") or "Q1")
        clk = det.get("RC") or s.get("chrono")
        per = int(re.sub(r"\D", "", cp) or 1) + (4 if cp.upper().startswith("OT") else 0)
        fin = s.get("liveGameStatus") == 999
        out[gid] = dict(want[gid], st="F" if fin else "L", per=per, clk=None if fin else clk, hs=sides[0], **{"as": sides[1]})
        add_players(out[gid], safe(pl_bcl, det, page))
    return out


def live_acb(games):
    # Strony live.acb.com działają po samym numerze meczu, więc nie potrzebujemy terminarza z acb.com
    out = {}
    for g in games:
        base = f"https://live.acb.com/es/partidos/{g['id']}"
        srows = cells_of(safe(get, base + "/estadisticas", 2) or "")    # przed startem strona może jeszcze nie istnieć
        tot = [r for r in srows if r and r[0] == "Totales"][:2]
        if len(tot) < 2:
            continue
        qrows = cells_of(safe(get, base + "/resumen", 2) or "")
        qi = next((i for i, r in enumerate(qrows) if r[:2] == ["Equipo", "1C"]), None)
        if len(tot) < 2 or qi is None:
            continue
        sides = []
        for i, r in enumerate(tot):
            sp = lambda c: [int(x) for x in r[c].split("/")]
            f2, f3, ft = sp(3), sp(5), sp(7)
            q = [int(x) for x in qrows[qi + 1 + i][1:] if x.isdigit()]
            sides.append(stat(int(r[2]), q, f2[0], f2[1], f3[0], f3[1], ft[0], ft[1], int(r[9]), int(r[13])))
        start = datetime.fromisoformat(g["d"].replace("Z", "+00:00"))
        done = start <= NOW - timedelta(hours=2, minutes=30)   # strona nie podaje końca meczu wprost
        out[g["id"]] = dict(g, st="F" if done else "L", per=trim_future(sides), clk=None, hs=sides[0], **{"as": sides[1]})
        add_players(out[g["id"]], safe(pl_acb, srows))
    return out


def live_gbl(games):
    out = {}
    for g in games:
        h = get(f"{ESAKE}EsakegameView?idgame={g['id']}&mode=3")
        qs = re.findall(r"<tr><td>(\d+)</td><td>(?:Q\d|ΟΤ\d|OT\d)</td><td>(\d+)</td></tr>", h)
        txt = re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", " ", h)))
        tots = re.findall(r"ΣΥΝΟΛΟ (\d+) (\d+) - (\d+) (\d+) - (\d+) (\d+) - (\d+) (\d+) (\d+) (\d+) (\d+) (\d+) (\d+) (\d+) (\d+) (\d+) (\d+)", txt)
        if len(tots) < 2 or not qs:
            continue
        sides = []
        for i, t in enumerate(tots[:2]):
            t = [int(x) for x in t]
            sides.append(stat(t[0], [int(q[i]) for q in qs], t[1], t[2], t[3], t[4], t[5], t[6], t[9], t[16]))
        per = trim_future(sides)
        # Strona nie podaje zegara ani końca meczu: koniec = 4. kwarta lub dogrywka, co najmniej 2 h od startu,
        # wynik nierozstrzygnięty remisem i taki sam jak przy poprzednim odczycie
        old = (g.get("hs") or {}).get("pts"), (g.get("as") or {}).get("pts")
        done = (per >= 4 and sides[0]["pts"] != sides[1]["pts"] and old == (sides[0]["pts"], sides[1]["pts"])
                and NOW - datetime.fromisoformat(g["d"].replace("Z", "+00:00")) >= timedelta(hours=2))
        out[g["id"]] = dict(g, st="F" if done else "L", per=per, clk=None, hs=sides[0], **{"as": sides[1]})
        add_players(out[g["id"]], safe(pl_gbl, txt))
    return out


def live_bbl(games):
    out = {}
    for g in games:
        pp = bbl_page(g["id"])
        gd, gs = pp.get("initialGameData") or {}, pp.get("initialGameStats")
        r = gd.get("result")
        if not r or not gs:
            continue
        # gameProgress = bieżąca kwarta ("Q3", "OT1", "E" po meczu), latestPeriod = ostatnia z wynikiem,
        # gameTime = czas do końca bieżącej kwarty
        per_of = lambda p: int(re.sub(r"\D", "", p) or 1) + (4 if p.upper().startswith("OT") else 0)
        prog = str(gd.get("gameProgress") or r.get("latestPeriod") or "Q1")
        fin = gd.get("status") == "OFFICIAL" or prog == "E"
        per = per_of(str(r.get("latestPeriod") or "Q1")) if fin else per_of(prog)
        sides = []
        for side, key in (("home", "homeTeam"), ("guest", "guestTeam")):
            t = gs[key]["gameStat"]
            q = [r.get(f"{side}TeamQ{n}Score") for n in range(1, 5)] + [r.get(f"{side}TeamOT{n}Score") for n in range(1, 10)]
            sides.append(stat(t["points"], [x or 0 for x in q[:per]], t["twoPointShotsMade"], t["twoPointShotsAttempted"],
                              t["threePointShotsMade"], t["threePointShotsAttempted"], t["freeThrowsMade"],
                              t["freeThrowsAttempted"], t["offensiveRebounds"], t["turnovers"]))
        gt = gd.get("gameTime")
        out[g["id"]] = dict(g, st="F" if fin else "L", per=per,
                            clk=gt[3:] if gt and not fin else None, hs=sides[0], **{"as": sides[1]})
        add_players(out[g["id"]], safe(pl_bbl, gs))
    return out


LIVE = {
    "EL": lambda gs: live_euroleague("E", gs),
    "EC": lambda gs: live_euroleague("U", gs),
    "PLK": live_plk, "BCL": live_bcl, "ACB": live_acb, "GBL": live_gbl, "BBL": live_bbl,
}


def live_main(out_dir, wanted):
    """Aktualizuje tylko trwające mecze wybranych lig. Wypisuje ZMIANA <liga> dla lig, w których coś się zmieniło."""
    any_live = False
    for lg in wanted:
        path = os.path.join(out_dir, f"{lg}.json")
        if not os.path.exists(path):
            continue
        doc = json.load(open(path, encoding="utf-8"))
        started = [g for g in doc["games"] if g.get("st") != "F" and
                   NOW - timedelta(hours=4) <= datetime.fromisoformat(g["d"].replace("Z", "+00:00")) <= NOW + timedelta(minutes=5)]
        soon = [g for g in doc["games"] if g.get("st") == "S" and
                NOW < datetime.fromisoformat(g["d"].replace("Z", "+00:00")) <= NOW + timedelta(minutes=60)]
        if started or soon:
            any_live = True
        if not started:
            print(f"{lg}: brak trwających meczów" + (f", {len(soon)} zaczyna się w ciągu godziny" if soon else ""))
            continue
        try:
            upd = LIVE[lg](started)
        except Exception as e:
            print(f"{lg}: BŁĄD {e!r}")
            continue
        changed = False
        for i, g in enumerate(doc["games"]):
            if g["id"] in upd and upd[g["id"]] != g:
                fix_quarters(upd[g["id"]])
                snap_quarters(g, upd[g["id"]])
                snap_shots(g, upd[g["id"]])
                doc["games"][i] = upd[g["id"]]
                changed = True
        if changed:
            doc["updated"] = iso(NOW)
            update_players(doc)
            json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
            print(f"ZMIANA {lg}: " + ", ".join(f"{u['h']} {u['hs']['pts']}:{u['as']['pts']} {u['a']} ({u['st']}, kw. {u.get('per')})" for u in upd.values()))
        else:
            print(f"{lg}: bez zmian ({len(started)} trwających)")
    if not any_live:
        print("KONIEC: w wybranych ligach nie ma trwających meczów ani meczów w ciągu godziny")


ARCHIVE_AT = 180_000    # bajtów; limit dokumentu w bazie to 256 KB


def archive(doc, out_dir, lg):
    """Przenosi najstarsze rozegrane mecze do pliku archive/<liga>-<data>.json, zostawiając w dokumencie
    co najmniej 12 ostatnich meczów każdej drużyny i 80 ostatnich meczów ligi (tyle potrzebują model i analiza).
    Historia zawodników zostaje w dokumencie ligi."""
    if len(json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode()) < ARCHIVE_AT:
        return
    fin = sorted((g for g in doc["games"] if g["st"] == "F"), key=lambda g: g["d"], reverse=True)
    cnt, keep = {}, set(g["id"] for g in fin[:80])
    for g in fin:
        for t in (g["h"], g["a"]):
            cnt[t] = cnt.get(t, 0) + 1
            if cnt[t] <= 12:
                keep.add(g["id"])
    old = [g for g in fin if g["id"] not in keep]
    if not old:
        return
    os.makedirs(os.path.join(out_dir, "archive"), exist_ok=True)
    name = f"{lg}-{NOW:%Y%m%d}"
    json.dump({"lg": lg, "games": sorted(old, key=lambda g: g["d"])},
              open(os.path.join(out_dir, "archive", name + ".json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    ids = {g["id"] for g in old}
    doc["games"] = [g for g in doc["games"] if g["id"] not in ids]
    print(f"ARCHIWUM {name}: {len(old)} meczów")


def due(path):
    """Czy w lidze jest mecz, który powinien już się skończyć, a nie ma jeszcze wyniku."""
    if not os.path.exists(path):
        return True
    for g in json.load(open(path, encoding="utf-8")).get("games", []):
        start = datetime.fromisoformat(g["d"].replace("Z", "+00:00"))
        if g.get("st") != "F" and NOW - timedelta(hours=12) <= start <= NOW - timedelta(minutes=110):
            return True
        # Mecz dziś albo jutro bez ogłoszonej godziny: pobieramy terminarz ponownie, żeby złapać godzinę, gdy się pojawi
        if g.get("tt") == 0 and g.get("st") == "S" and start - NOW <= timedelta(hours=36):
            return True
    return False


def main():
    """--due: tylko ligi z meczami, które powinny się już skończyć (szybkie sprawdzenie co godzinę)."""
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out_dir = args[0]
    wanted = args[1:] or list(LEAGUES)
    os.makedirs(out_dir, exist_ok=True)
    if "--live" in sys.argv:
        return live_main(out_dir, wanted)
    if "--due" in sys.argv:
        wanted = [lg for lg in wanted if due(os.path.join(out_dir, f"{lg}.json"))]
        if not wanted:
            print("BRAK: żaden mecz nie czeka na wynik")
    for lg in wanted:
        name, fn = LEAGUES[lg]
        path = os.path.join(out_dir, f"{lg}.json")
        known = {}
        if os.path.exists(path):
            for g in json.load(open(path, encoding="utf-8")).get("games", []):
                if g.get("st") == "F" and g["d"] <= iso(NOW - timedelta(hours=6)):
                    known[g["id"]] = g
        try:
            games = fn(known)
        except Exception as e:
            if "--due" in sys.argv and os.path.exists(path):
                doc = json.load(open(path, encoding="utf-8"))
                waiting = [g for g in doc["games"] if g.get("st") != "F" and
                           NOW - timedelta(hours=12) <= datetime.fromisoformat(g["d"].replace("Z", "+00:00")) <= NOW - timedelta(minutes=110)]
                try:
                    upd = LIVE[lg](waiting)
                except Exception as e2:
                    print(f"{lg}: BŁĄD {e!r} / {e2!r}")
                    continue
                games = [upd.get(g["id"], g) for g in doc["games"]]
                print(f"{lg}: pełne pobranie nie działa ({e!r}), uzupełniono {len(upd)} meczów ze stron meczów")
            else:
                print(f"{lg}: BŁĄD {e!r}")
                continue
        games.sort(key=lambda g: g["d"])
        prev = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
        # Mecz z ostatnich 12 godzin bez wyniku, którego źródło chwilowo nie podało (trwa, rozgrzewka), zostaje na liście
        ids = {g["id"] for g in games}
        games += [g for g in prev.get("games", []) if g["id"] not in ids and g.get("st") != "F"
                  and datetime.fromisoformat(g["d"].replace("Z", "+00:00")) > NOW - timedelta(hours=12)]
        games.sort(key=lambda g: g["d"])
        old_live = {g["id"]: g for g in prev.get("games", []) if g.get("pq") or g.get("sq")}
        for g in games:
            for key in ("pq", "sq"):
                if g["id"] in old_live and not g.get(key) and old_live[g["id"]].get(key):
                    g[key] = old_live[g["id"]][key]
        doc = update_players({"lg": lg, "name": name, "updated": iso(NOW), "games": games,
                              "players": prev.get("players", {}), "pgames": prev.get("pgames", [])})
        if lg == "NBA":
            inj = safe(nba_injuries)
            doc["inj"] = inj if inj is not None else prev.get("inj", {})
            doc["injAt"] = iso(NOW) if inj is not None else prev.get("injAt")
        archive(doc, out_dir, lg)
        json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
        fin = sum(g["st"] == "F" for g in doc["games"])
        print(f"{lg}: {fin} rozegranych, {len(games) - fin} zaplanowanych, {os.path.getsize(path)//1024} KB")


if __name__ == "__main__":
    main()
