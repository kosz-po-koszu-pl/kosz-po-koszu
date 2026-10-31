r"""Kosz po Koszu: zadania w chmurze na repozytorium GitHub.

Dane lig leżą w data/<liga>.json, ustawienia modelu w data/params.json. Aplikacja czyta je przez jsDelivr
(cdn.jsdelivr.net/gh/<repo>@<commit>/data/...), a w bazie aplikacji jest tylko znacznik data/head
z numerem commita. Model uruchamiający to zadanie zapisuje do bazy wyłącznie ten znacznik.

  python kpk_cloud.py daily        wszystkie ligi + przegląd danych + dostrajanie
  python kpk_cloud.py nba          tylko NBA
  python kpk_cloud.py due          ligi z meczami, które powinny się już skończyć
  python kpk_cloud.py live LG ...  jedna runda na żywo
  python kpk_cloud.py stop LG ...  koniec odświeżania na żywo (znacznik running: false)

Ostatnia linia wyniku: HEAD {...} - dokładnie ten JSON trzeba zapisać jako dokument data/head.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
PY = sys.executable
NOW = datetime.now(timezone.utc)


def sh(*args, check=True):
    p = subprocess.run(list(args), cwd=BASE, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and p.returncode:
        raise RuntimeError(f"{' '.join(args)}: {p.stderr.strip()[-400:]}")
    return p.stdout.strip()


def load(path):
    return json.load(open(path, encoding="utf-8"))


def finished_at_head(lg):
    """Liczba rozegranych meczów ligi w ostatnim zapisanym stanie (commit HEAD)."""
    try:
        d = json.loads(sh("git", "show", f"HEAD:data/{lg}.json"))
        return sum(g.get("st") == "F" for g in d.get("games", []))
    except Exception:
        return 0


def fetch(args, timeout):
    p = subprocess.run([PY, os.path.join(BASE, "fetch.py"), DATA] + args, cwd=BASE, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    out = (p.stdout or "") + (p.stderr or "")[-1500:]
    print(out.strip())
    return out


def guard(out):
    """Liga z błędem albo z mniejszą liczbą rozegranych meczów (bez archiwum) wraca do poprzedniego stanu."""
    archived = {l.split()[1].split("-")[0] for l in out.splitlines() if l.startswith("ARCHIWUM ")}
    problems = []
    for f in sorted(os.listdir(DATA)):
        if not f.endswith(".json") or f in ("params.json", "health.json"):
            continue
        lg = f[:-5]
        failed = any(l.startswith(lg + ":") and "BŁĄD" in l for l in out.splitlines())
        old, new = finished_at_head(lg), sum(g.get("st") == "F" for g in load(os.path.join(DATA, f)).get("games", []))
        if failed or (new < old and lg not in archived):
            sh("git", "checkout", "--", f"data/{f}", check=False)
            problems.append(f"{lg}: " + ("pobieranie nie powiodło się" if failed else f"ubyło rozegranych meczów ({old} -> {new})")
                            + ", zostają poprzednie dane")
            print("POMINIĘTE " + problems[-1])
    return problems


def health(problems):
    """Przegląd danych (aplikacja pokazuje problemy jako ostrzeżenie)."""
    t = lambda g: datetime.fromisoformat(g["d"].replace("Z", "+00:00"))
    for f in sorted(os.listdir(DATA)):
        if not f.endswith(".json") or f in ("params.json", "health.json"):
            continue
        lg, games = f[:-5], load(os.path.join(DATA, f)).get("games", [])
        ids = [g["id"] for g in games]
        if len(ids) != len(set(ids)):
            problems.append(f"{lg}: zdublowane mecze")
        stuck = sum(g.get("st") == "L" and NOW - t(g) > timedelta(hours=4) for g in games)
        nores = sum(g.get("st") == "S" and NOW - t(g) > timedelta(hours=12) for g in games)
        badq = sum(g.get("st") == "F" and bool(g.get("hs")) and sum(g["hs"].get("q") or []) != g["hs"].get("pts") for g in games)
        if stuck:
            problems.append(f"{lg}: {stuck} mecz(e) wciąż oznaczone jako trwające")
        if nores:
            problems.append(f"{lg}: {nores} mecz(e) bez wyniku ponad 12 h po starcie")
        if badq:
            problems.append(f"{lg}: {badq} mecz(e) z kwartami niezgodnymi z wynikiem")
    json.dump({"at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), "ok": not problems, "problems": problems[:12]},
              open(os.path.join(DATA, "health.json"), "w", encoding="utf-8"), ensure_ascii=False)
    for p in problems:
        print("PROBLEM " + p)


def tune():
    tdata = os.path.join(BASE, ".tdata")
    os.makedirs(tdata, exist_ok=True)
    for f in os.listdir(tdata):
        os.remove(os.path.join(tdata, f))
    for f in os.listdir(DATA):
        if f.endswith(".json") and f not in ("params.json", "health.json"):
            with open(os.path.join(DATA, f), encoding="utf-8") as a, open(os.path.join(tdata, f), "w", encoding="utf-8") as b:
                b.write(a.read())
    out_path = os.path.join(BASE, ".params_new.json")
    p = subprocess.run(["node", os.path.join(BASE, "tune.js"), os.path.join(BASE, "engine.js"), tdata, out_path,
                        os.path.join(DATA, "params.json")], cwd=BASE, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=900)
    out = (p.stdout or "") + (p.stderr or "")[-1500:]
    print("\n".join(l for l in out.splitlines() if not l.startswith("   ")))
    if "ZAPISANO" in out:
        os.replace(out_path, os.path.join(DATA, "params.json"))


def commit(msg):
    """Zapisuje zmiany w data/ do repozytorium i wysyła je (przy równoległym zadaniu: dociąga i ponawia)."""
    if not sh("git", "config", "user.email", check=False):
        sh("git", "config", "user.email", "kosz-po-koszu@users.noreply.github.com")
        sh("git", "config", "user.name", "Kosz po Koszu")
    sh("git", "add", "data")
    if sh("git", "status", "--porcelain", "data", check=False):
        sh("git", "commit", "-q", "-m", msg)
    branch = sh("git", "rev-parse", "--abbrev-ref", "HEAD")
    for i in range(3):
        p = subprocess.run(["git", "push", "-q", "origin", f"HEAD:{branch}"], cwd=BASE, capture_output=True, text=True)
        if p.returncode == 0:
            break
        if i == 2:
            print("BŁĄD WYSYŁANIA: " + p.stderr.strip()[-300:])
        subprocess.run(["git", "pull", "-q", "--rebase", "-X", "theirs", "origin", branch], cwd=BASE, capture_output=True)
    return sh("git", "rev-parse", "HEAD")


def head(sha, running, leagues):
    print("HEAD " + json.dumps({"sha": sha, "at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), "running": running, "leagues": leagues}))


def main():
    mode, rest = sys.argv[1], sys.argv[2:]
    if mode == "daily":
        out = fetch([], 1500)
        problems = guard(out)
        health(problems)
        tune()
        head(commit(f"Dane dzienne {NOW:%Y-%m-%d}"), False, [])
    elif mode == "nba":
        guard(fetch(["NBA"], 900))
        head(commit(f"NBA wieczorem {NOW:%Y-%m-%d %H:%M}"), False, [])
    elif mode == "due":
        out = fetch(["--due"], 900)
        if "BRAK" not in out:
            guard(out)
        head(commit(f"Wyniki {NOW:%Y-%m-%d %H:%M}"), False, [])
    elif mode == "live":
        out = fetch(rest + ["--live"], 300)
        end = any(l.startswith("KONIEC") for l in out.splitlines())
        sha = commit(f"Na żywo {NOW:%H:%M} {' '.join(rest)}")
        if end:
            print("KONIEC")
        head(sha, not end, rest)
    elif mode == "stop":
        head(sh("git", "rev-parse", "HEAD"), False, rest)


if __name__ == "__main__":
    main()
