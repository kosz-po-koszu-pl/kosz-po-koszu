
const LG_ORDER = ["EL","EC","BCL","PLK","ACB","GBL","BBL","NBA"];
const NO_LIVE = ["NBA"];   // mecze w nocy: tylko wyniki i prognozy, bez odświeżania na żywo
const TZ = "Europe/Warsaw";
const fmtDay = new Intl.DateTimeFormat("pl-PL",{timeZone:TZ,weekday:"short"});
const fmtDate = new Intl.DateTimeFormat("pl-PL",{timeZone:TZ,day:"numeric",month:"numeric"});
const fmtTime = new Intl.DateTimeFormat("pl-PL",{timeZone:TZ,hour:"2-digit",minute:"2-digit"});
const fmtFull = new Intl.DateTimeFormat("pl-PL",{timeZone:TZ,day:"numeric",month:"long",hour:"2-digit",minute:"2-digit"});
const dayKey = d => new Intl.DateTimeFormat("sv-SE",{timeZone:TZ}).format(d);
const hourOf = d => +new Intl.DateTimeFormat("pl-PL",{timeZone:TZ,hour:"numeric",hourCycle:"h23"}).format(d);
// Nocne mecze NBA (przed 9:00 naszego czasu) pokazujemy przy poprzednim dniu, żeby było je widać wieczorem
const isNight = (g, lg) => lg === "NBA" && hourOf(new Date(g.d)) < 9;
const playDay = (g, lg) => dayKey(new Date(Date.parse(g.d) - (isNight(g, lg) ? 12*36e5 : 0)));
const startTxt = (g, lg) => { const d = new Date(g.d), t = fmtTime.format(d);
  if (g.tt === 0) return "(godzina do potwierdzenia)";       // liga jeszcze nie podała godziny
  return isNight(g, lg) ? `w nocy z ${fmtDay.format(new Date(d - 12*36e5))} na ${fmtDay.format(d)} o ${t}` : `o ${t}`; };
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));


/* Greckie nazwy drużyn: znane kluby po europejsku, reszta przepisana literami łacińskimi */
const GR_CLUBS = {"ΑΕΚ":"AEK Athens","ΑΡΗΣ":"Aris","ΗΡΑΚΛΗΣ":"Iraklis","ΜΑΡΟΥΣΙ":"Maroussi","ΟΛΥΜΠΙΑΚΟΣ":"Olympiacos",
  "ΠΑΝΑΘΗΝΑΪΚΟΣ":"Panathinaikos","ΠΑΟΚ":"PAOK","ΠΕΡΙΣΤΕΡΙ":"Peristeri","ΚΑΡΔΙΤΣΑ":"Karditsa","ΚΟΛΟΣΣΟΣ":"Kolossos Rodou",
  "ΜΥΚΟΝΟΣ":"Mykonos","ΠΡΟΜΗΘΕΑΣ":"Promitheas Patras","ΔΟΞΑ":"Doxa Lefkadas","ΑΠΟΛΛΩΝ":"Apollon Patras","ΛΑΥΡΙΟ":"Lavrio","ΙΩΝΙΚΟΣ":"Ionikos"};
const GR = {"Α":"A","Β":"V","Γ":"G","Δ":"D","Ε":"E","Ζ":"Z","Η":"I","Θ":"Th","Ι":"I","Κ":"K","Λ":"L","Μ":"M","Ν":"N","Ξ":"X","Ο":"O","Π":"P",
  "Ρ":"R","Σ":"S","Τ":"T","Υ":"Y","Φ":"F","Χ":"Ch","Ψ":"Ps","Ω":"O","Ά":"A","Έ":"E","Ή":"I","Ί":"I","Ϊ":"I","Ό":"O","Ύ":"Y","Ϋ":"Y","Ώ":"O"};
function nm(name){
  if (!/[\u0370-\u03ff]/.test(name)) return name;
  const words = name.split(/\s+/);
  const club = GR_CLUBS[words[0].toUpperCase()];
  if (club) return club;
  return words.map(w => [...w.toUpperCase()].map(c => GR[c] ?? c).join("")).map(w => /[A-Z]{3,}/.test(w) ? w[0]+w.slice(1).toLowerCase() : w).join(" ");
}
let leagues = {};            // lg -> {name, updated, games}
let params = {};             // nauczone ustawienia modelu (model/params), opcjonalne
const state = {day: dayKey(new Date()), lg: "ALL", game: null,
  my: [], uid: null, canToggle: false, isOwner: false, liveReady: false, liveStatus: null};
let dbRef = null;
state.tab = "list";
state.ana = {lg: "ALL", team: "", days: 14};
try { const s = JSON.parse(localStorage.getItem("kpk.view")||"{}"); if (s.lg) state.lg = s.lg; if (s.tab) state.tab = s.tab; if (s.ana) Object.assign(state.ana, s.ana); } catch(e) {}

/* ---------- Model ---------- */
const SHRINK = 3;          // ile "meczów średniej ligi" dokładamy do drużyny
const LAST_BY = lg => lg === "NBA" ? 12 : 7;   // forma: tyle ostatnich meczów drużyny (nowsze ważą więcej)
const poss = s => s.f2a + s.f3a - s.orb + s.tov + 0.44*s.fta;

function buildModel(lg, before){
  const all = (leagues[lg]?.games||[]).filter(g => g.st==="F" && g.d < before);
  const P = params[lg] || {};
  // Średnie ligi
  let L = {pts:0,poss:0,n:0,f2m:0,f2a:0,f3m:0,f3a:0,ftm:0,fta:0,hd:0,q:[0,0,0,0]};
  for (const g of all) for (const [s,o] of [[g.hs,g.as],[g.as,g.hs]]) {
    const p = (poss(s)+poss(o))/2;
    L.pts+=s.pts; L.poss+=p; L.n++; for (const k of ["f2m","f2a","f3m","f3a","ftm","fta"]) L[k]+=s[k];
    for (let i=0;i<4;i++) L.q[i]+=s.q[i]||0;
  }
  for (const g of all) L.hd += g.hs.pts - g.as.pts;
  const lgN = Math.max(L.n,1);
  const lgOR = L.poss ? L.pts/L.poss*100 : 110;
  const lgPace = L.poss ? L.poss/lgN : 72;
  const lgP2 = L.f2a ? L.f2m/L.f2a : .52, lgP3 = L.f3a ? L.f3m/L.f3a : .35, lgFT = L.fta ? L.ftm/L.fta : .76;
  const per = x => L.poss ? x/L.poss : 0;
  const lgR2 = per(L.f2a)||.48, lgR3 = per(L.f3a)||.38, lgRFT = per(L.fta)||.28;
  const qTot = L.q.reduce((a,b)=>a+b,0) || 1;
  const lgQ = L.q.map(x=>x/qTot);
  const games = all.length;
  const hca = P.hca ?? (games ? (L.hd/games) * games/(games+20) + 2.5 * 20/(games+20) : 2.5);
  const form = P.form ?? .65;     // ile waży forma z ostatnich meczów, a ile siła z całego sezonu (dostrajane)
  const LAST = LAST_BY(lg);
  // Statystyki drużyny z listy meczów z wagami
  const agg = (list, wf) => {
    let A = {pts:0,opp:0,poss:0,n:0,f2m:0,f2a:0,f3m:0,f3a:0,ftm:0,fta:0,o2m:0,o2a:0,o3m:0,o3a:0,q:[0,0,0,0]};
    list.forEach(([g,side],i)=>{
      const w = wf(i);
      const s = side==="h"?g.hs:g.as, o = side==="h"?g.as:g.hs, p = (poss(s)+poss(o))/2;
      A.pts+=w*s.pts; A.opp+=w*o.pts; A.poss+=w*p; A.n+=w;
      for (const k of ["f2m","f2a","f3m","f3a","ftm","fta"]) A[k]+=w*s[k];
      A.o2m+=w*o.f2m; A.o2a+=w*o.f2a; A.o3m+=w*o.f3m; A.o3a+=w*o.f3a;
      for (let i2=0;i2<4;i2++) A.q[i2]+=w*(s.q[i2]||0);
    });
    const k = SHRINK*lgPace;                    // wirtualne posiadania średniej ligi
    const mix = (num, den, lgRate) => (num + k*lgRate)/(den + k);
    const qs = A.q.reduce((a,b)=>a+b,0);
    return {
      or: mix(A.pts, A.poss, lgOR/100)*100, dr: mix(A.opp, A.poss, lgOR/100)*100,
      pace: (A.poss + SHRINK*lgPace)/(A.n + SHRINK),
      r2: mix(A.f2a, A.poss, lgR2), r3: mix(A.f3a, A.poss, lgR3), rft: mix(A.fta, A.poss, lgRFT),
      p2: (A.f2m + 30*lgP2)/(A.f2a + 30), p3: (A.f3m + 30*lgP3)/(A.f3a + 30), pft: (A.ftm + 30*lgFT)/(A.fta + 30),
      a2: (A.o2m + 30*lgP2)/(A.o2a + 30), a3: (A.o3m + 30*lgP3)/(A.o3a + 30),
      q: lgQ.map((l,i)=> ((A.q[i]) + 1.5*lgPace*lgOR/100*l) / (qs + 1.5*lgPace*lgOR/100))
    };
  };
  // Drużyny: forma (ostatnie mecze) połączona z siłą z całego sezonu
  const teams = {};
  const byTeam = {};
  for (const g of all) { (byTeam[g.h] ||= []).push([g,"h"]); (byTeam[g.a] ||= []).push([g,"a"]); }
  for (const [t, arr] of Object.entries(byTeam)) {
    arr.sort((x,y)=> x[0].d < y[0].d ? 1 : -1);
    const recent = arr.slice(0, LAST);
    const R = agg(recent, i => Math.pow(.85, i)), S = agg(arr, i => Math.pow(.97, i));
    const b = (x, y) => form*x + (1-form)*y;
    teams[t] = {
      n: recent.length, recent: recent.map(([g,side])=>({g,side})), opps: arr.map(([g,side])=>side==="h"?g.a:g.h),
      or: b(R.or,S.or), dr: b(R.dr,S.dr), pace: b(R.pace,S.pace), r2: b(R.r2,S.r2), r3: b(R.r3,S.r3), rft: b(R.rft,S.rft),
      p2: b(R.p2,S.p2), p3: b(R.p3,S.p3), pft: b(R.pft,S.pft), a2: b(R.a2,S.a2), a3: b(R.a3,S.a3),
      q: R.q.map((x,i)=>b(x,S.q[i]))
    };
    // U siebie / na wyjeździe: o ile różnica bilansu punktów u siebie i na wyjeździe odbiega od typowej w lidze.
    // Mało meczów = wynik ciągnięty do zera (8 "meczów zaufania").
    const hm = arr.filter(x=>x[1]==="h").map(([g])=>g.hs.pts-g.as.pts), am = arr.filter(x=>x[1]==="a").map(([g])=>g.as.pts-g.hs.pts);
    const mean = a => a.reduce((x,y)=>x+y,0)/a.length;
    if (hm.length && am.length){ const nE = hm.length*am.length/(hm.length+am.length);
      teams[t].ha = (mean(hm) - mean(am) - 2*hca) * nE/(nE + 8); } else teams[t].ha = 0;
  }
  // Korekta o siłę rywali (jedno przejście)
  const ts = Object.values(teams);
  for (const t of ts) {
    const od = t.opps.map(o => teams[o]?.dr ?? lgOR), oo = t.opps.map(o => teams[o]?.or ?? lgOR);
    const avg = a => a.length ? a.reduce((x,y)=>x+y,0)/a.length : lgOR;
    t.aor = t.or * lgOR/avg(od);
    t.adr = t.dr * lgOR/avg(oo);
  }
  const M0 = {teams, lgOR, lgPace, hca, haW: P.haW ?? .5};
  const h2h = {};
  for (const g of all) {
    const E = expected(M0, g.h, g.a);
    const r = (g.hs.pts - g.as.pts) - (E.eh - E.ea);
    (h2h[g.h+"|"+g.a] ||= []).push(r);
    (h2h[g.a+"|"+g.h] ||= []).push(-r);
  }
  return {lg, teams, lgOR, lgPace, lgP2, lgP3, lgFT, lgR2, lgR3, lgRFT, lgQ, hca, games, h2h, h2hW: P.h2h ?? .3, form,
          sdPace: P.sdPace ?? .02, sdShoot: P.sdShoot ?? .03, vf: P.vf ?? .6, bl: P.bl ?? .05, fat: P.fat ?? .8, haW: P.haW ?? .5,
          shk: {p2: P.shk?.p2 ?? 60, p3: P.shk?.p3 ?? 150, ft: P.shk?.ft ?? 80},
          // Ustawienia na etap meczu (dostrajane co dzień): k = ile posiadań "zaufania" do prognozy sprzed meczu,
          // vf = rozrzut liczby rzutów; s2 do przerwy, s3 po przerwie
          s2: {k: P.s2?.k ?? 40, vf: P.s2?.vf ?? .6}, s3: {k: P.s3?.k ?? 40, vf: P.s3?.vf ?? .6},
          tuned: P.tuned || null, tunedN: P.n || 0,
          ot: lg === "NBA" ? 5/48 : 5/40};          // dogrywka jako część meczu (NBA: 48 minut gry)
}

const blank = (M) => ({n:0, recent:[], or:M.lgOR, dr:M.lgOR, aor:M.lgOR, adr:M.lgOR, pace:M.lgPace,
  r2:M.lgR2, r3:M.lgR3, rft:M.lgRFT, p2:M.lgP2, p3:M.lgP3, pft:M.lgFT, a2:M.lgP2, a3:M.lgP3, q:M.lgQ});

function expected(M, h, a){
  const H = M.teams[h] || blank(M), A = M.teams[a] || blank(M);
  const pace = H.pace*A.pace/M.lgPace;
  let eh = pace*(H.aor*A.adr/M.lgOR)/100 + M.hca/2;
  let ea = pace*(A.aor*H.adr/M.lgOR)/100 - M.hca/2;
  // Drużyna mocniejsza u siebie niż zwykle (ha > 0) i gość słabszy na wyjeździe niż zwykle (ha > 0) przesuwają różnicę
  if (M.haW){ const c = M.haW*((H.ha||0) + (A.ha||0))/4; eh += c/2; ea -= c/2; }
  // Korekta pary drużyn: jeśli w ich wcześniejszych meczach gospodarz wypadał lepiej lub gorzej, niż wynikało z formy
  const hist = M.h2h?.[h+"|"+a];
  if (hist && hist.length && M.h2hW){
    const c = M.h2hW * hist.reduce((x,y)=>x+y,0)/hist.length * hist.length/(hist.length+2);
    eh += c/2; ea -= c/2;
  }
  return {H, A, pace, eh, ea};
}

// Prosty generator liczb losowych z ziarnem (powtarzalne wyniki dla tego samego meczu)
function rng(seed){ let s = seed>>>0 || 1; return () => (s = (s*1664525 + 1013904223)>>>0) / 4294967296; }
function gauss(r){ let u=0,v=0; while(!u) u=r(); v=r(); return Math.sqrt(-2*Math.log(u))*Math.cos(2*Math.PI*v); }
// Liczba rzutów: średnia z modelu i rozrzut mniejszy niż w rozkładzie Poissona,
// bo posiadań w meczu jest ograniczona liczba (VF = część wariancji Poissona).
function attempts(r, l, vf){ return Math.max(0, Math.round(l + Math.sqrt(l*vf)*gauss(r))); }
function binom(r, n, p){ let x=0; for(let i=0;i<n;i++) if (r()<p) x++; return x; }
function hash(s){ let h=2166136261; for (const c of s) h = Math.imul(h ^ c.charCodeAt(0), 16777619); return h>>>0; }

function profile(T, O, M, share){
  const p2 = Math.min(.9, T.p2*O.a2/M.lgP2), p3 = Math.min(.8, T.p3*O.a3/M.lgP3);
  const perPoss = T.r2*p2*2 + T.r3*p3*3 + T.rft*T.pft;
  return {p2, p3, pft:T.pft, r2:T.r2, r3:T.r3, rft:T.rft, perPoss, share};
}

/* ---------- Liderzy drużyn ----------
   Historia zawodników ligi: leagues[lg].players[drużyna][zawodnik] = [mecze, punkty, sekundy, ostatni mecz].
   Listy zawodników w meczu: g.hp / g.ap = [[zawodnik, punkty, sekundy, faule?], ...]. */
const foulLimit = lg => lg === "NBA" ? 6 : 5;

function leaders(lg, team){
  const P = leagues[lg]?.players?.[team];
  if (!P) return {top: [], ppg: 0};
  const rows = Object.entries(P).map(([n, r]) => ({n, g: r[0], pts: r[1], ppg: r[1]/r[0], mpg: r[2]/60/r[0], last: r[3]}));
  const games = Math.max(1, ...rows.map(r => r.g));
  const ppg = rows.reduce((a, r) => a + r.pts, 0) / games;
  const top = rows.filter(r => r.g >= Math.max(2, games*.4)).sort((a, b) => b.ppg - a.ppg).slice(0, 3)
    .map(r => ({...r, share: ppg ? r.ppg/ppg : 0}));
  return {top, ppg};
}

// Stan lidera w tym meczu: "gra", "nie gra" (po 1. kwarcie bez minut), "faule" (limit), "zagrożony" (limit - 1)
function leaderState(lg, g, side, name, elapsed){
  const list = g[side + "p"];
  if (!list) return {st: "?", pts: null, f: null};
  const row = list.find(x => x[0] === name);
  if (!row) return {st: elapsed >= 10 || g.st === "F" ? "nie gra" : "?", pts: 0, f: null};
  const f = row[3] ?? null, lim = foulLimit(lg);
  return {st: f != null && f >= lim ? "faule" : f != null && f >= lim - 1 ? "zagrożony" : "gra", pts: row[1], f};
}

// Jaka część punktów drużyny „wypada” przez nieobecnych lub wykluczonych liderów
function leaderLoss(lg, g, side, elapsed){
  const team = side === "h" ? g.h : g.a;
  let loss = 0;
  for (const L of leaders(lg, team).top){
    const s = leaderState(lg, g, side, L.n, elapsed).st;
    loss += s === "nie gra" || s === "faule" ? L.share : s === "zagrożony" ? L.share*.3 : 0;
  }
  return Math.min(.6, loss);
}

/* Kontuzje (NBA, lista ESPN): przed meczem odejmujemy udział lidera, który grał w ostatnich 3 meczach,
   a teraz jest na liście. Dłuższe nieobecności są już w formie drużyny, więc ich nie liczymy drugi raz. */
const INJ = {O: [1, "nie zagra"], D: [.75, "raczej nie zagra"], Q: [.4, "niepewny"], DTD: [.2, "z dnia na dzień"]};
function injury(lg, team, name){ return leagues[lg]?.inj?.[team]?.find(x => x[0] === name)?.[1] || null; }
function injLoss(lg, g, side){
  const team = side === "h" ? g.h : g.a;
  if (g.st !== "S" || !leagues[lg]?.inj?.[team] || Date.parse(g.d) - Date.now() > 2*864e5) return 0;
  const recent = (leagues[lg].games || []).filter(x => x.st === "F" && x.d < g.d && (x.h === team || x.a === team)).slice(-3);
  let loss = 0;
  for (const L of leaders(lg, team).top){
    const st = injury(lg, team, L.n);
    if (!st || !recent.some(x => (x.h === team ? x.hp : x.ap)?.some(p => p[0] === L.n && p[2] > 0))) continue;
    loss += L.share * (INJ[st]?.[0] ?? .2);
  }
  return Math.min(.6, loss);
}

/* ---------- Zmęczenie ----------
   Dni odpoczynku przed meczem, licząc mecze drużyny w pucharze i w lidze krajowej.
   Tę samą drużynę w różnych ligach rozpoznajemy po charakterystycznym słowie nazwy (bez miast i sponsorów). */
const CUPS = ["EL", "EC", "BCL"], HOME_LG = ["PLK", "ACB", "GBL", "BBL"];
const STOP = new Set(("basket basketball baskets bc bk kk fc sk ac mbc club wks mks the tel aviv istanbul belgrade athens " +
  "thessaloniki warszawa betsson energa telekom aktor salud meridianbet mozzart bet rapyd").split(" "));
const ALIAS = {barca: "barcelona", munchen: "munich"};
const words = n => nm(n).normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/ł/g, "l")
  .split(/[^a-z0-9]+/).map(w => ALIAS[w] || w).filter(w => w.length >= 3 && !STOP.has(w));
let restIdx = {key: "", map: {}};
function restIndex(){
  const key = LG_ORDER.map(lg => leagues[lg]?.updated || "").join("|");
  if (restIdx.key === key) return restIdx.map;
  const map = {}, own = {};
  for (const lg of LG_ORDER) for (const g of leagues[lg]?.games || []) for (const t of [g.h, g.a])
    ((own[lg] ||= {})[t] ||= []).push(Date.parse(g.d));
  for (const lg of LG_ORDER) {
    const partners = CUPS.includes(lg) ? HOME_LG : HOME_LG.includes(lg) ? CUPS : [];
    for (const [t, ds] of Object.entries(own[lg] || {})) {
      const w = words(t), all = ds.slice();
      for (const p of partners) for (const [t2, d2] of Object.entries(own[p] || {}))
        if (words(t2).some(x => w.includes(x))) all.push(...d2);
      map[lg + "|" + t] = all.sort((a, b) => a - b);
    }
  }
  return (restIdx = {key, map}).map;
}
// Odpoczynek w dniach (najwyżej 3); za każdy mecz ponad 2 w ostatnim tygodniu odejmujemy pół dnia
function rest(lg, team, d){
  const t = Date.parse(d), ds = restIndex()[lg + "|" + team] || [];
  const prev = ds.filter(x => x < t - 36e5);
  if (!prev.length) return {days: 3, eff: 3};
  const days = (t - prev[prev.length - 1]) / 864e5, n7 = prev.filter(x => x > t - 7*864e5).length;
  return {days, eff: Math.min(3, days) - .5*Math.max(0, n7 - 2), n7};
}

/* Stan meczu na żywo: która kwarta trwa, ile jej zostało (0-1) i ile minut już minęło */
function liveState(g){
  let per = g.per || 1;
  let rem = .5;                                  // źródło bez zegara: zakładamy połowę kwarty
  if (g.clk){ const [m,s] = g.clk.split(":").map(Number); if (!isNaN(m)) rem = Math.min(1, (m + (s||0)/60)/(per > 4 ? 5 : 10)); }
  // Sprzeczne dane ze źródła: zegar na początku kwarty, a w tej kwarcie padły już punkty.
  // Wtedy ta kwarta jest w rzeczywistości zakończona i zaczyna się następna.
  const curPts = (g.hs?.q?.[per-1] || 0) + (g.as?.q?.[per-1] || 0);
  if (rem >= .99 && curPts > 0 && !g.hs?.est) per += 1;
  const len = per > 4 ? 5 : 10;
  const elapsed = per <= 4 ? (per-1)*10 + 10*(1-rem) : 40 + (per-5)*5 + 5*(1-rem);
  return {per, rem, elapsed: Math.max(elapsed, .5)};
}

/* Symulacja: zwraca tablice wyników, z których liczymy średnie i prawdopodobieństwa.
   W trakcie meczu zaczyna od prawdziwego wyniku i symuluje tylko pozostały czas,
   łącząc prognozę sprzed meczu z tym, jak drużyny grają w tym meczu. */
function simulate(M, g, N){
  const E = expected(M, g.h, g.a);
  // Zmęczenie: drużyna z mniejszą liczbą dni odpoczynku traci M.fat pkt różnicy na każdy dzień
  if (M.fat){ const fd = M.fat*(rest(M.lg, g.h, g.d).eff - rest(M.lg, g.a, g.d).eff); E.eh += fd/2; E.ea -= fd/2; }
  let ph = profile(E.H, E.A, M, E.H.q), pa = profile(E.A, E.H, M, E.A.q);
  let sh = E.eh/(E.pace*ph.perPoss), sa = E.ea/(E.pace*pa.perPoss);   // dopasowanie do oczekiwanych punktów
  const live = g.st === "L" && g.hs && g.as ? liveState(g) : null;
  let paceRate = E.pace/40;                                            // posiadania na minutę
  const stp = live ? (live.elapsed <= 25 ? M.s2 : M.s3) : null;
  const vf = stp ? stp.vf : M.vf;
  if (live){
    // Przy odtwarzaniu przebiegu (est) znamy tylko punkty po kwartach, więc tempo zostaje z prognozy
    const pObs = g.hs.est ? E.pace*live.elapsed/40 : (poss(g.hs) + poss(g.as))/2;
    if (!g.hs.est) paceRate = (E.pace/40*15 + pObs)/(15 + live.elapsed);   // 15 minut "zaufania" do prognozy
    const adj = (exp, pts) => { const prior = exp/E.pace; return ((prior*stp.k + pts)/(stp.k + pObs))/prior; };
    const shots = s => s.f2a + s.f3a + s.fta;
    if (!g.hs.est && shots(g.hs) >= 8 && shots(g.as) >= 8){
      // Z rzutów: liczba prób za 2, za 3 i wolnych na posiadanie oraz skuteczność, każda łączona z prognozą osobno.
      // Skuteczność za 3 w jednym meczu to głównie przypadek, więc mocno ciągniemy ją do prognozy (domyślnie 150 prób),
      // za 2 słabiej (60), wolne (80); dostrajane na zapisach z końca 2. i 3. kwarty. Liczba prób zależy od stylu gry
      // i szybko się stabilizuje (k posiadań).
      const K = M.shk;
      const lp = (pr, sc, s) => ({...pr,
        r2: (pr.r2*sc*stp.k + s.f2a)/(stp.k + pObs), r3: (pr.r3*sc*stp.k + s.f3a)/(stp.k + pObs),
        rft: (pr.rft*sc*stp.k + s.fta)/(stp.k + pObs),
        p2: (pr.p2*K.p2 + s.f2m)/(K.p2 + s.f2a), p3: (pr.p3*K.p3 + s.f3m)/(K.p3 + s.f3a), pft: (pr.pft*K.ft + s.ftm)/(K.ft + s.fta)});
      ph = lp(ph, sh, g.hs); pa = lp(pa, sa, g.as); sh = 1; sa = 1;
    } else { sh *= adj(E.eh, g.hs.pts); sa *= adj(E.ea, g.as.pts); }
    // Wysokie prowadzenie: lider zwalnia i wpuszcza rezerwowych. Za każde 10 pkt przewagi ponad 10 atak lidera
    // słabnie o M.bl (dostrajane per liga), najwyżej do przewagi 30.
    const m = g.hs.pts - g.as.pts, bw = M.bl * Math.max(0, Math.min(20, Math.abs(m) - 10))/10;
    if (bw && live.elapsed >= 20){ if (m > 0) sh *= 1 - bw; else sa *= 1 - bw; }
    // Brak lidera (nie zagrał w 1. kwarcie albo wypadł za faule) osłabia atak drużyny do końca meczu.
    // Zastępcy przejmują część rzutów, więc odejmujemy połowę jego udziału w punktach drużyny.
    if (!g.hs.est){
      sh *= 1 - .5*leaderLoss(M.lg, g, "h", live.elapsed);
      sa *= 1 - .5*leaderLoss(M.lg, g, "a", live.elapsed);
    }
  }
  if (!live){ sh *= 1 - .5*injLoss(M.lg, g, "h"); sa *= 1 - .5*injLoss(M.lg, g, "a"); }
  const r = rng(hash(M.lg + g.id + (live ? g.hs.pts+":"+g.as.pts+":"+(g.clk||"") : "")));
  const out = [];
  for (let i=0;i<N;i++){
    const pace = (live ? paceRate*40 : E.pace)*Math.exp(gauss(r)*M.sdPace);
    const fh = Math.exp(gauss(r)*M.sdShoot), fa = Math.exp(gauss(r)*M.sdShoot);
    const res = {h:team(), a:team(), ot:0};
    const play = (side, pr, sc, f, frac, qi) => {
      const ps = pace*frac;
      const n2 = attempts(r, ps*pr.r2*sc, vf), n3 = attempts(r, ps*pr.r3*sc, vf), nf = attempts(r, ps*pr.rft*sc, vf);
      const m2 = binom(r,n2,Math.min(.95,pr.p2*f)), m3 = binom(r,n3,Math.min(.9,pr.p3*f)), mf = binom(r,nf,pr.pft);
      const t = res[side], pts = 2*m2+3*m3+mf;
      t.q[qi] = (t.q[qi]||0) + pts; t.pts += pts; t.m2+=m2; t.m3+=m3; t.mf+=mf;
    };
    if (live){
      for (const [side, s] of [["h",g.hs],["a",g.as]]){
        const t = res[side]; t.q = s.q.slice(); t.pts = s.pts; t.m2 = s.f2m; t.m3 = s.f3m; t.mf = s.ftm;
      }
      const ci = live.per - 1, len = live.per > 4 ? 5 : 10, cur = live.rem*len/40;
      play("h",ph,sh,fh,cur,ci); play("a",pa,sa,fa,cur,ci);
      for (let q = live.per; q < 4; q++){ play("h",ph,sh,fh,.25,q); play("a",pa,sa,fa,.25,q); }
      res.ot = Math.max(0, live.per - 4);
      const otIdx = Math.max(4, live.per);
      while (res.h.pts===res.a.pts && res.ot<6){ res.ot++; play("h",ph,sh,fh,M.ot,otIdx); play("a",pa,sa,fa,M.ot,otIdx); }
      out.push(res);
      continue;
    }
    for (let q=0;q<4;q++){ play("h",ph,sh,fh,ph.share[q],q); play("a",pa,sa,fa,pa.share[q],q); }
    while (res.h.pts===res.a.pts && res.ot<4){ res.ot++; play("h",ph,sh,fh,M.ot,4); play("a",pa,sa,fa,M.ot,4); }
    out.push(res);
  }
  return {E, sims: out};
  function team(){ return {pts:0,q:[0,0,0,0],m2:0,m3:0,mf:0}; }
}

const simCache = new Map();
function predict(g, lg, N){
  const key = [lg, g.id, N, leagues[lg]?.updated||"", JSON.stringify(params[lg]||""), g.st, g.hs?.pts, g.as?.pts, g.per, g.clk, g.hs?.est].join("|");
  if (simCache.has(key)) return simCache.get(key);
  const M = buildModel(lg, g.d);
  const S = simulate(M, g, N);
  const s = S.sims, n = s.length, mean = f => s.reduce((a,x)=>a+f(x),0)/n;
  const P = {M, E:S.E, sims:s,
    h: mean(x=>x.h.pts), a: mean(x=>x.a.pts),
    win: s.filter(x=>x.h.pts>x.a.pts).length/n,
    total: mean(x=>x.h.pts+x.a.pts),
    sd: Math.sqrt(mean(x=>(x.h.pts+x.a.pts)**2) - mean(x=>x.h.pts+x.a.pts)**2)};
  simCache.set(key, P);
  return P;
}

