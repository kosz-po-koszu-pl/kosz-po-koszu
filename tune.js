// Kosz po Koszu: dostrajanie ustawień modelu osobno dla każdej ligi i etapu meczu.
// Użycie: node tune.js ENGINE.js LEAGUES_DIR OUT.json [stare_params.json]
// ENGINE.js to część skryptu aplikacji z modelem (do znacznika "Widoki").
// Każda prognoza korzysta tylko z meczów rozegranych przed danym meczem.
const fs = require("fs");
const [enginePath, dir, outPath, oldPath] = process.argv.slice(2);
const api = new Function("localStorage", "document", "window",
  fs.readFileSync(enginePath, "utf8") +
  ";return {predict, setL: l => { leagues = l }, setP: p => { params = p }, clear: () => simCache.clear()};")(
  {getItem: () => null, setItem: () => {}}, {getElementById: () => ({})}, {});

const L = {};
for (const f of fs.readdirSync(dir)) if (f.endsWith(".json")) { const d = JSON.parse(fs.readFileSync(`${dir}/${f}`, "utf8")); L[d.lg || f.slice(0, -5)] = d; }
api.setL(L);
const old = oldPath && fs.existsSync(oldPath) ? JSON.parse(fs.readFileSync(oldPath, "utf8")) : {};

const N = 300, TOLS = [3, 5, 10], MIN_GAMES = 15;
const DEF = {hca: null, vf: .6, form: .65, h2h: .3, bl: .05, fat: .8, haW: .5, s2: {k: 40, vf: .6}, s3: {k: 40, vf: .6},
             shk: {p2: 60, p3: 150, ft: 80}};
const MIN_SNAP = 15;     // tyle meczów z zapisem stanu z końca kwarty potrzeba, żeby dostrajać przeliczanie z rzutów
const SMOOTH = .4;       // dziennie ustawienia przesuwają się o 40% drogi od wczorajszych (bez skoków z dnia na dzień)

function partial(s, k) {
  return {pts: s.q.slice(0, k).reduce((a, b) => a + (b || 0), 0), q: s.q.slice(0, k), est: true,
          f2m: 0, f2a: 0, f3m: 0, f3a: 0, ftm: 0, fta: 0, orb: 0, tov: 0};
}
function stage(g, k) {
  if (!k) return {...g, st: "S"};
  // Mecz śledzony na żywo: prawdziwy stan z końca kwarty (z rzutami), bez list zawodników z dalszej części meczu
  const sn = g.sq && g.sq[k];
  if (sn && sn.clk && sn.hs && sn.hs.q) return {...g, id: g.id + "|s" + k, st: "L", per: sn.per, clk: sn.clk, hs: sn.hs, as: sn.as, hp: undefined, ap: undefined};
  return {...g, st: "L", per: k, clk: "00:00", hs: partial(g.hs, k), as: partial(g.as, k)};
}

// Ocena ustawień P na etapie k: błąd punktów i rozjazd między obiecanymi a faktycznymi trafieniami w przedziały
function evaluate(lg, games, P, k) {
  const all = {}; all[lg] = P; api.setP(all); api.clear();
  let err = 0, errM = 0, n = 0, promised = 0, hit = 0, cnt = 0;
  for (const g of games) {
    if (k && (g.hs.q || []).length < k) continue;
    const R = api.predict(stage(g, k), lg, N);
    n++;
    err += Math.abs(R.h - g.hs.pts) + Math.abs(R.a - g.as.pts);
    errM += Math.abs((R.h - R.a) - (g.hs.pts - g.as.pts));
    for (const [side, key] of [["h", "hs"], ["a", "as"]]) {
      const c = Math.round(R[side]);
      for (const t of TOLS) {
        promised += R.sims.filter(x => Math.abs(x[side].pts - c) <= t).length / R.sims.length;
        hit += Math.abs(g[key].pts - c) <= t; cnt++;
      }
    }
  }
  return {n, err: err / (2 * n), errM: errM / n, gap: Math.abs(promised - hit) / cnt, promised: promised / cnt, hit: hit / cnt};
}

function best(lg, games, base, k, field, values, metric) {
  let top = null;
  for (const v of values) {
    const P = JSON.parse(JSON.stringify(base));
    if (field.includes(".")) { const [a, b] = field.split("."); P[a][b] = v; } else P[field] = v;
    const e = evaluate(lg, games, P, k);
    if (!top || e[metric] < top.e[metric] - 1e-9) top = {P, e};
  }
  return top;
}

// Domyślna przewaga hali = średnia różnica gospodarz - gość w lidze, przyciągnięta do 2,5 pkt (jak w aplikacji)
function evaluateHcaDefault(lg) {
  const fin = L[lg].games.filter(g => g.st === "F" && g.hs);
  const n = fin.length, d = fin.reduce((a, g) => a + g.hs.pts - g.as.pts, 0);
  return n ? (d / n) * n / (n + 20) + 2.5 * 20 / (n + 20) : 2.5;
}

const out = {};
for (const lg of Object.keys(L)) {
  const fin = L[lg].games.filter(g => g.st === "F" && g.hs && g.as).sort((a, b) => a.d < b.d ? -1 : 1);
  const games = fin.slice(Math.floor(fin.length * .3)).slice(-60);   // pomijamy początek sezonu bez historii
  if (games.length < MIN_GAMES) {
    if (old[lg]) out[lg] = old[lg];
    console.log(`${lg}: za mało meczów (${games.length}), zostają dotychczasowe ustawienia`);
    continue;
  }
  let P = JSON.parse(JSON.stringify(DEF));
  const before = {pre: evaluate(lg, games, P, 0), s2: evaluate(lg, games, P, 2), s3: evaluate(lg, games, P, 3)};
  // Przed meczem: przewaga hali (błąd różnicy punktów), potem szerokość przedziałów (zgodność procentów)
  P = best(lg, games, P, 0, "hca", [0, 1, 2, 3, 4, 5], "errM").P;
  P = best(lg, games, P, 0, "form", [.3, .5, .65, .8, 1], "err").P;      // forma vs cały sezon
  P = best(lg, games, P, 0, "h2h", [0, .3, .6], "errM").P;              // korekta par drużyn
  P = best(lg, games, P, 0, "haW", [0, .5, 1], "errM").P;             // forma u siebie / na wyjeździe
  P = best(lg, games, P, 0, "fat", [0, .5, 1, 1.5, 2.5], "errM").P;      // zmęczenie (dni odpoczynku)
  P = best(lg, games, P, 0, "vf", [.3, .45, .6, .8, 1.0, 1.3], "gap").P;
  // Do przerwy i po przerwie: zaufanie do prognozy sprzed meczu, potem szerokość przedziałów
  for (const [k, s] of [[2, "s2"], [3, "s3"]]) {
    P = best(lg, games, P, k, `${s}.k`, [10, 20, 40, 80, 160], "err").P;
    P = best(lg, games, P, k, `${s}.vf`, [.2, .35, .5, .7, 1.0, 1.4], "gap").P;
  }
  P = best(lg, games, P, 3, "bl", [0, .03, .06, .1, .15], "err").P;      // zwalnianie przy wysokim prowadzeniu
  // Przeliczanie z rzutów na żywo: tylko na meczach z zapisanym stanem z końca 2. kwarty
  const snapG = games.filter(g => g.sq && g.sq["2"] && g.sq["2"].clk);
  if (snapG.length >= MIN_SNAP) {
    P = best(lg, snapG, P, 2, "shk.p3", [80, 150, 300], "err").P;
    P = best(lg, snapG, P, 2, "shk.p2", [30, 60, 120], "err").P;
    P = best(lg, snapG, P, 2, "shk.ft", [40, 80, 160], "err").P;
  }
  // Łagodzenie: przy małej liczbie meczów ustawienia zostają bliżej domyślnych (chroni przed dopasowaniem do przypadku)
  const w = games.length / (games.length + 30);
  const lin = (d, v) => +(d + (v - d) * w).toFixed(3);
  const lg2 = (d, v) => Math.round(Math.exp(Math.log(d) + (Math.log(v) - Math.log(d)) * w));
  const defHca = evaluateHcaDefault(lg);
  const wS = snapG.length / (snapG.length + 30);
  const lgS = (d, v) => Math.round(Math.exp(Math.log(d) + (Math.log(v) - Math.log(d)) * wS));
  P = {hca: lin(defHca, P.hca), vf: lin(DEF.vf, P.vf), form: lin(DEF.form, P.form), h2h: lin(DEF.h2h, P.h2h), bl: lin(DEF.bl, P.bl), fat: lin(DEF.fat, P.fat), haW: lin(DEF.haW, P.haW),
       s2: {k: lg2(DEF.s2.k, P.s2.k), vf: lin(DEF.s2.vf, P.s2.vf)},
       s3: {k: lg2(DEF.s3.k, P.s3.k), vf: lin(DEF.s3.vf, P.s3.vf)},
       shk: {p2: lgS(DEF.shk.p2, P.shk.p2), p3: lgS(DEF.shk.p3, P.shk.p3), ft: lgS(DEF.shk.ft, P.shk.ft)}};
  // Wygładzanie z dnia na dzień: nowe ustawienie = wczorajsze + 40% różnicy
  const O = old[lg];
  if (O) {
    const sm = (o, v, int) => { if (typeof o !== "number" || typeof v !== "number") return v; const x = o + (v - o) * SMOOTH; return int ? Math.round(x) : +x.toFixed(3); };
    for (const f of ["hca", "vf", "form", "h2h", "bl", "fat", "haW"]) P[f] = sm(O[f], P[f]);
    for (const s of ["s2", "s3"]) if (O[s]) P[s] = {k: sm(O[s].k, P[s].k, true), vf: sm(O[s].vf, P[s].vf)};
    if (O.shk) P.shk = {p2: sm(O.shk.p2, P.shk.p2, true), p3: sm(O.shk.p3, P.shk.p3, true), ft: sm(O.shk.ft, P.shk.ft, true)};
  }
  const after = {pre: evaluate(lg, games, P, 0), s2: evaluate(lg, games, P, 2), s3: evaluate(lg, games, P, 3)};
  out[lg] = {...P, n: games.length, nSnap: snapG.length, tuned: new Date().toISOString(),
    report: Object.fromEntries(Object.entries(after).map(([s, e]) => [s, {
      err: +e.err.toFixed(2), errBefore: +before[s].err.toFixed(2),
      promised: +e.promised.toFixed(3), hit: +e.hit.toFixed(3), gapBefore: +before[s].gap.toFixed(3)}]))};
  const f = (e) => `błąd ${e.err.toFixed(1)} pkt, obiecane ${(e.promised*100).toFixed(0)}% / trafione ${(e.hit*100).toFixed(0)}%`;
  console.log(`${lg} (${games.length} meczów) hala ${P.hca} forma ${P.form} pary ${P.h2h} prowadzenie ${P.bl} zmęczenie ${P.fat} dom/wyjazd ${P.haW} vf ${P.vf} | do przerwy k ${P.s2.k} vf ${P.s2.vf} | po przerwie k ${P.s3.k} vf ${P.s3.vf} | rzuty na żywo ${P.shk.p2}/${P.shk.p3}/${P.shk.ft} (zapisów ${snapG.length})`);
  for (const s of ["pre", "s2", "s3"]) console.log(`   ${s}: przed ${f(before[s])}  ->  po ${f(after[s])}`);
}
fs.writeFileSync(outPath, JSON.stringify(out));
console.log("ZAPISANO " + outPath);
