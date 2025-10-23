import React, { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { AlertCircle, Brackets, Ghost, KeyRound, Play, Settings2, Swords, Trophy, Trash2, Upload, Download } from "lucide-react";

/**
 * Cryptid Scare Bracket — single-file React app
 * -------------------------------------------------
 * What this does
 * - Lets users enter cryptids (name + optional blurb)
 * - Supports Small (8) or Large (16) single-elimination brackets
 * - Generates bracket, simulates each matchup round-by-round
 * - Scores per rules; final is monster-vs-monster
 * - Uses OpenAI Chat Completions (JSON mode) OR an offline simulator
 * - Pretty UI using Tailwind + shadcn/ui
 *
 * Notes
 * - Store your OpenAI API key locally (Settings) — for demo use only
 * - You can test everything with the Offline Simulator (no API key required)
 */

/** @typedef {{ id: string, name: string, blurb?: string }} Cryptid */
/** @typedef {{ name: string, seed: number, id: string, blurb?: string }} Entrant */
/** @typedef {{ roundIndex: number, matchIndex: number, slot: "A"|"B" }} FromRef */
/** @typedef {{ name?: string, id?: string, from?: FromRef }} Slot */
/** @typedef {{ id: string, a: Slot, b: Slot, winner?: Slot & { scoreA?: number, scoreB?: number, summary?: string }, details?: MatchDetails }} Match */
/** @typedef {{ matches: Match[] }} Round */

/** Outcome scoring map */
const SCORE_MAP = {
  runs_away: 1,
  runs_away_crying: 2,
  defends: -1,
  stays_put: -2,
  walks_away: 0,
} as const;

/** Allowed outcomes */
const OUTCOMES = Object.keys(SCORE_MAP);

/** @typedef {{
 *  attempts: { attempt: number, outcome: keyof typeof SCORE_MAP, notes: string }[],
 *  highlights?: { most_successful?: string, least_successful?: string }
 * }} SimulationJSON
 */

/** @typedef {{
 *  aLog: SimulationJSON,
 *  bLog?: SimulationJSON, // present in final when both sides attack
 *  scoreA: number,
 *  scoreB: number,
 *  winnerId: string,
 *  highlights: { aBest?: string, aWorst?: string, bBest?: string, bWorst?: string },
 * }} MatchDetails */

function uid(prefix = "id") { return `${prefix}_${Math.random().toString(36).slice(2, 9)}`; }
function shuffle(arr) { return [...arr].sort(() => Math.random() - 0.5); }

function computeScore(log /** @type {SimulationJSON} */) {
  return log.attempts.reduce((sum, a) => sum + (SCORE_MAP[a.outcome] ?? 0), 0);
}

function outcomeLabel(key) {
  switch (key) {
    case "runs_away": return "runs away";
    case "runs_away_crying": return "runs away crying";
    case "defends": return "defends himself";
    case "stays_put": return "stays put";
    case "walks_away": return "walks away";
    default: return key;
  }
}

const SAMPLE_CRYPTIDS = [
  { name: "Mothman", blurb: "A winged, red-eyed harbinger of doom." },
  { name: "Bigfoot", blurb: "Massive, elusive primate in North American woods." },
  { name: "Loch Ness Monster", blurb: "Shy lake-dweller with a serpentine silhouette." },
  { name: "Chupacabra", blurb: "Spiny-backed livestock drainer from the dark." },
  { name: "Jersey Devil", blurb: "Hooved, winged shrieker of Pine Barrens lore." },
  { name: "Wendigo", blurb: "Gaunt, ravenous spirit of endless winter hunger." },
  { name: "Kraken", blurb: "A titanic tentacled terror from the deep." },
  { name: "Yeti", blurb: "Snowbound colossus with a thunderous roar." },
  { name: "Banshee", blurb: "Wailing omen whose cry chills the marrow." },
  { name: "Mokele-mbembe", blurb: "Swamp-bound sauropod of whispered sightings." },
  { name: "Skinwalker", blurb: "Shapeshifting mimic with malevolent intent." },
  { name: "Thunderbird", blurb: "Storm-calling avian giant with shadowed wings." },
  { name: "Shadow Person", blurb: "A living absence lurking at the edge of sight." },
  { name: "Bunyip", blurb: "Billabong beast with a dreadful bellow." },
  { name: "Mongolian Death Worm", blurb: "Arid burrower rumored to spit lightning." },
  { name: "Spring-Heeled Jack", blurb: "Grinning leaper cloaked in coal-smoke." },
];

/** Build bracket rounds from entrants (8 or 16 only) */
function buildRounds(entrants /** @type {Entrant[]} */) {
  const n = entrants.length; // 8 or 16
  const rounds = [];
  const totalRounds = Math.log2(n);
  const firstRoundMatches = n / 2;

  // Round 0 — pair sequentially
  const r0 = { matches: [] };
  for (let i = 0; i < firstRoundMatches; i++) {
    const a = entrants[i * 2];
    const b = entrants[i * 2 + 1];
    r0.matches.push({ id: uid("m"), a: { name: a.name, id: a.id }, b: { name: b.name, id: b.id } });
  }
  rounds.push(r0);

  // Subsequent rounds — references to prior winners
  for (let r = 1; r < totalRounds; r++) {
    const prev = rounds[r - 1];
    const m = { matches: [] };
    for (let i = 0; i < prev.matches.length / 2; i++) {
      m.matches.push({
        id: uid("m"),
        a: { from: { roundIndex: r - 1, matchIndex: i * 2, slot: "A" } },
        b: { from: { roundIndex: r - 1, matchIndex: i * 2 + 1, slot: "B" } },
      });
    }
    rounds.push(m);
  }
  return rounds;
}

/** Resolve a slot into an entrant name/id using rounds */
function resolveSlot(slot /** @type {Slot} */, rounds /** @type {Round[]} */) {
  if (slot?.name && slot?.id) return slot;
  if (slot?.from) {
    const { roundIndex, matchIndex } = slot.from;
    const m = rounds[roundIndex]?.matches[matchIndex];
    if (m?.winner) return { name: m.winner.name, id: m.winner.id };
  }
  return { name: "TBD", id: "tbd" };
}

/** Prompt builders */
function promptForManScenario(attacker /** @type {Slot} */, attackerBlurb /** @type {string} */) {
  return `You are simulating a horror-scare attempt sequence. Return STRICT JSON only per the provided schema.\n\nAttacker: ${attacker.name}. Description: ${attackerBlurb || "(no extra lore)"}.\nTarget: A lone, rage-prone macho man sitting on a park bench at night with his dog.\nTone: PG-13. Absolutely no gore. Keep notes brief and cinematic.\n\nRules: 10 independent attempts. For each attempt choose exactly one outcome from this list (lowercase, exact match):\n- runs_away\n- runs_away_crying\n- defends\n- stays_put\n- walks_away\n\nScoring (FYI): runs_away=+1, runs_away_crying=+2, defends=-1, stays_put=-2, walks_away=0.\n\nJSON schema:\n{\n  "attempts": [\n    { "attempt": 1, "outcome": "runs_away" | "runs_away_crying" | "defends" | "stays_put" | "walks_away", "notes": "short vivid note" }\n  ],\n  "highlights": {\n    "most_successful": "",\n    "least_successful": ""\n  }\n}\n\nRespond with JSON only.`;
}

function promptForMonsterVsMonster(a /** @type {Slot} */, aBlurb /** @type {string} */, b /** @type {Slot} */, bBlurb /** @type {string} */) {
  return `You are simulating a cryptid scaring another cryptid. Return STRICT JSON only.\n\nScenario: A night-time park bench. The defender (${b.name}) is seated alone on the bench. The attacker (${a.name}) tries to scare them away.\nAttacker description: ${aBlurb || "(no extra lore)"}.\nDefender description: ${bBlurb || "(no extra lore)"}.\nTone: PG-13. Absolutely no gore. Keep notes brief and cinematic.\n\nRules: 10 independent attempts. For each attempt choose exactly one outcome (exact lowercase):\n- runs_away\n- runs_away_crying\n- defends\n- stays_put\n- walks_away\n\nScoring (FYI): runs_away=+1, runs_away_crying=+2, defends=-1, stays_put=-2, walks_away=0.\n\nJSON schema:\n{\n  "attempts": [\n    { "attempt": 1, "outcome": "runs_away" | "runs_away_crying" | "defends" | "stays_put" | "walks_away", "notes": "short vivid note" }\n  ],\n  "highlights": {\n    "most_successful": "",\n    "least_successful": ""\n  }\n}\n\nRespond with JSON only.`;
}

/** Offline simulator for testing without API key */
function offlineSim(name) {
  const attempts = Array.from({ length: 10 }, (_, i) => {
    const r = Math.random();
    const outcome = r < 0.15 ? "runs_away_crying"
      : r < 0.45 ? "runs_away"
      : r < 0.7 ? "walks_away"
      : r < 0.9 ? "defends"
      : "stays_put";
    const snippets = {
      runs_away_crying: `${name} whispers a name from the void; the target bolts sobbing`,
      runs_away: `${name} looms from fog; the target sprints`,
      walks_away: `${name} rattles a sign; the target just stands and leaves`,
      defends: `${name} gets pelted with the man's thermos`,
      stays_put: `${name} flickers; the target stays stubbornly seated`,
    };
    return { attempt: i + 1, outcome, notes: snippets[outcome] };
  });
  return {
    attempts,
    highlights: {
      most_successful: attempts.find(a => a.outcome === "runs_away_crying")?.notes || attempts.find(a => a.outcome === "runs_away")?.notes || attempts[0].notes,
      least_successful: attempts.reverse().find(a => a.outcome === "stays_put" || a.outcome === "defends")?.notes || attempts[attempts.length - 1].notes,
    }
  };
}

async function callOpenAI({ apiKey, model, temperature, prompt, signal }) {
  const body = {
    model: model || "gpt-4o-mini",
    temperature: typeof temperature === "number" ? temperature : 0.7,
    response_format: { type: "json_object" },
    messages: [
      { role: "system", content: "You are an impartial simulation engine. Always return STRICT JSON that matches the schema with allowed outcome values only." },
      { role: "user", content: prompt }
    ],
  };

  const res = await fetch("https://api.openai.com/v1/chat/completions", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`OpenAI error ${res.status}: ${text}`);
  }
  const data = await res.json();
  const msg = data.choices?.[0]?.message?.content;
  if (!msg) throw new Error("No content from OpenAI");
  try {
    const parsed = JSON.parse(msg);
    // lightweight validation
    if (!parsed.attempts || !Array.isArray(parsed.attempts)) throw new Error("Invalid JSON (attempts missing)");
    parsed.attempts.forEach((a, i) => {
      if (!OUTCOMES.includes(a.outcome)) throw new Error(`Bad outcome at attempt ${i + 1}`);
    });
    return parsed;
  } catch (e) {
    console.warn("Parse error; raw=", msg);
    throw e;
  }
}

export default function CryptidScareBracket() {
  const [apiKey, setApiKey] = useState(() => localStorage.getItem("cryptid_api_key") || "");
  const [model, setModel] = useState("gpt-4o-mini");
  const [temperature, setTemperature] = useState(0.7);
  const [useOffline, setUseOffline] = useState(false);

  const [size, setSize] = useState(8); // 8 or 16
  const [cryptids, setCryptids] = useState(/** @type {Cryptid[]} */([]));
  const [entrants, setEntrants] = useState(/** @type {Entrant[]} */([]));
  const [rounds, setRounds] = useState(/** @type {Round[]} */([]));

  const [running, setRunning] = useState(false);
  const [logMsg, setLogMsg] = useState("");
  const [error, setError] = useState("");
  const abortRef = useRef(/** @type {AbortController|null} */(null));

  // Persist API key
  useEffect(() => { if (apiKey) localStorage.setItem("cryptid_api_key", apiKey); }, [apiKey]);

  // Derived: can start
  const canGenerate = useMemo(() => cryptids.filter(c => c.name?.trim()).length === size, [cryptids, size]);
  const canRun = useMemo(() => rounds.length > 0 && entrants.length === size, [rounds, entrants, size]);

  function addCryptid() {
    if (cryptids.length >= size) return;
    setCryptids(prev => [...prev, { id: uid("c"), name: "", blurb: "" }]);
  }
  function removeCryptid(id) { setCryptids(prev => prev.filter(c => c.id !== id)); }
  function updateCryptid(id, patch) { setCryptids(prev => prev.map(c => c.id === id ? { ...c, ...patch } : c)); }

  function fillSamples() {
    const picks = shuffle(SAMPLE_CRYPTIDS).slice(0, size).map((c) => ({ id: uid("c"), ...c }));
    setCryptids(picks);
  }

  function importList(text) {
    const lines = text.split(/\n+/).map(s => s.trim()).filter(Boolean).slice(0, size);
    if (!lines.length) return;
    setCryptids(lines.map((name) => ({ id: uid("c"), name })));
  }

  function exportTournament() {
    const payload = { entrants, rounds };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `cryptid_bracket_${size}.json`; a.click();
    URL.revokeObjectURL(url);
  }

  function generateBracket() {
    setError("");
    const cleaned = cryptids.filter(c => c.name?.trim()).slice(0, size);
    if (cleaned.length !== size) { setError(`Need exactly ${size} cryptids to generate.`); return; }
    const shuffled = shuffle(cleaned).map((c, i) => ({ id: c.id, name: c.name.trim(), blurb: c.blurb?.trim() || "", seed: i + 1 }));
    setEntrants(shuffled);
    setRounds(buildRounds(shuffled));
  }

  function getBlurbById(id) { return entrants.find(e => e.id === id)?.blurb || ""; }

  async function simulateTournament() {
    setError("");
    if (!useOffline && !apiKey) { setError("Add your OpenAI API key in Settings or enable Offline Simulator."); return; }
    if (!canRun) { setError("Generate the bracket first."); return; }

    setRunning(true);
    setLogMsg("Starting simulations…");

    const mutableRounds = JSON.parse(JSON.stringify(rounds));
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      for (let r = 0; r < mutableRounds.length; r++) {
        const isFinal = r === mutableRounds.length - 1; // last round
        for (let m = 0; m < mutableRounds[r].matches.length; m++) {
          const match = mutableRounds[r].matches[m];
          const A = resolveSlot(match.a, mutableRounds);
          const B = resolveSlot(match.b, mutableRounds);
          setLogMsg(`Round ${r + 1}: ${A.name} vs ${B.name}`);

          // Run simulation
          /** @type {SimulationJSON} */
          let aLog; /** @type {SimulationJSON|undefined} */
          let bLog; 

          if (useOffline) {
            aLog = offlineSim(A.name);
            if (isFinal) bLog = offlineSim(B.name);
          } else {
            if (!isFinal) {
              const prompt = promptForManScenario(A, getBlurbById(A.id));
              aLog = await callOpenAI({ apiKey, model, temperature, prompt, signal: controller.signal });
            } else {
              const promptA = promptForMonsterVsMonster(A, getBlurbById(A.id), B, getBlurbById(B.id));
              const promptB = promptForMonsterVsMonster(B, getBlurbById(B.id), A, getBlurbById(A.id));
              aLog = await callOpenAI({ apiKey, model, temperature, prompt: promptA, signal: controller.signal });
              bLog = await callOpenAI({ apiKey, model, temperature, prompt: promptB, signal: controller.signal });
            }
          }

          const scoreA = computeScore(aLog);
          const scoreB = isFinal ? computeScore(bLog) : 0;

          // Winner logic; tie-breakers
          let winner = A; let summary = "";
          if (!isFinal) {
            winner = scoreA > 0 ? A : B; // if A fails (<=0), bench guy held or fought back: B advances
            if (scoreA === 0) {
              // coin flip
              winner = Math.random() < 0.5 ? A : B;
            }
            summary = `${A.name} vs bench: ${scoreA} pts.`;
          } else {
            if (scoreA === scoreB) {
              // tie-breaker by runs_away_crying count, then random
              const aCry = aLog.attempts.filter(x => x.outcome === "runs_away_crying").length;
              const bCry = bLog.attempts.filter(x => x.outcome === "runs_away_crying").length;
              if (aCry !== bCry) winner = aCry > bCry ? A : B; else winner = Math.random() < 0.5 ? A : B;
            } else {
              winner = scoreA > scoreB ? A : B;
            }
            summary = `${A.name}: ${scoreA} • ${B.name}: ${scoreB}`;
          }

          /** @type {MatchDetails} */
          const details = {
            aLog,
            bLog,
            scoreA,
            scoreB,
            winnerId: winner.id,
            highlights: {
              aBest: aLog?.highlights?.most_successful,
              aWorst: aLog?.highlights?.least_successful,
              bBest: bLog?.highlights?.most_successful,
              bWorst: bLog?.highlights?.least_successful,
            }
          };

          match.details = details;
          match.winner = { name: winner.name, id: winner.id, scoreA, scoreB, summary };
          setRounds(JSON.parse(JSON.stringify(mutableRounds)));
          await new Promise(res => setTimeout(res, 250)); // tiny UI breather
        }
      }
      setLogMsg("Tournament complete! 🏆");
    } catch (e) {
      console.error(e);
      setError(e.message || "Simulation failed");
      setLogMsg("");
    } finally {
      setRunning(false);
      abortRef.current = null;
    }
  }

  function cancelRun() {
    if (abortRef.current) {
      abortRef.current.abort();
      setRunning(false);
      setLogMsg("Cancelled");
    }
  }

  // UI helpers
  const remaining = size - cryptids.length;

  return (
    <div className="min-h-screen w-full bg-gradient-to-b from-slate-50 to-white text-slate-900">
      <header className="sticky top-0 z-10 backdrop-blur bg-white/70 border-b border-slate-200">
        <div className="max-w-6xl mx-auto px-4 py-3 flex items-center gap-3">
          <Ghost className="h-6 w-6" />
          <h1 className="text-xl font-semibold">Cryptid Scare Bracket</h1>
          <div className="ml-auto flex items-center gap-2">
            <Button variant="secondary" size="sm" onClick={exportTournament} className="gap-2"><Download className="h-4 w-4"/>Export</Button>
            <SettingsMenu apiKey={apiKey} setApiKey={setApiKey} model={model} setModel={setModel} temperature={temperature} setTemperature={setTemperature} useOffline={useOffline} setUseOffline={setUseOffline} />
          </div>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-4 py-6 space-y-8">
        <section className="grid md:grid-cols-3 gap-6">
          <Card className="md:col-span-2">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2"><Brackets className="h-5 w-5"/> Setup</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid sm:grid-cols-2 gap-4">
                <div>
                  <Label className="text-sm">Bracket size</Label>
                  <Select value={String(size)} onValueChange={(v) => { setSize(Number(v)); setCryptids([]); setEntrants([]); setRounds([]); }}>
                    <SelectTrigger className="mt-1"><SelectValue placeholder="Choose size"/></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="8">Small (8)</SelectItem>
                      <SelectItem value="16">Large (16)</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="flex items-center gap-3 pt-6">
                  <Switch id="offline" checked={useOffline} onCheckedChange={setUseOffline} />
                  <Label htmlFor="offline">Use Offline Simulator</Label>
                </div>
              </div>

              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" size="sm" onClick={addCryptid} disabled={cryptids.length >= size} className="gap-2"><Ghost className="h-4 w-4"/>Add cryptid ({remaining} slots)</Button>
                <Button variant="secondary" size="sm" onClick={fillSamples} className="gap-2"><Upload className="h-4 w-4"/>Quick-fill samples</Button>
                <ImportList onImport={importList} />
              </div>

              <div className="grid md:grid-cols-2 gap-4">
                {cryptids.map((c, idx) => (
                  <div key={c.id} className="border rounded-2xl p-3 space-y-2 bg-white shadow-sm">
                    <div className="flex items-center gap-3">
                      <span className="text-xs px-2 py-0.5 rounded-full bg-slate-100">#{idx + 1}</span>
                      <Input placeholder="Name (required)" value={c.name} onChange={(e) => updateCryptid(c.id, { name: e.target.value })} />
                      <Button variant="ghost" size="icon" onClick={() => removeCryptid(c.id)}><Trash2 className="h-4 w-4"/></Button>
                    </div>
                    <Textarea placeholder="Optional blurb/lore to flavor the sim" value={c.blurb || ""} onChange={(e) => updateCryptid(c.id, { blurb: e.target.value })} className="text-sm"/>
                  </div>
                ))}
              </div>

              <div className="flex flex-wrap items-center gap-3 pt-1">
                <Button disabled={!canGenerate} onClick={generateBracket} className="gap-2"><Brackets className="h-4 w-4"/>Generate bracket</Button>
                {!canGenerate && <p className="text-sm text-slate-500">Enter exactly {size} cryptids.</p>}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2"><Swords className="h-5 w-5"/> Run</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="text-sm text-slate-600">{logMsg || "Ready."}</div>
              {error && (
                <div className="flex items-start gap-2 text-red-600 text-sm">
                  <AlertCircle className="h-4 w-4 mt-0.5"/> <span>{error}</span>
                </div>
              )}
              <div className="flex flex-wrap gap-2">
                <Button onClick={simulateTournament} disabled={!canRun || running} className="gap-2"><Play className="h-4 w-4"/>Start simulation</Button>
                <Button onClick={cancelRun} disabled={!running} variant="secondary">Cancel</Button>
              </div>
              <div className="text-xs text-slate-500">{!useOffline && !apiKey && "Tip: Add your OpenAI API key (top right) or enable Offline Simulator."}</div>
            </CardContent>
          </Card>
        </section>

        {rounds.length > 0 && (
          <section className="overflow-x-auto">
            <div className="min-w-[900px] grid" style={{ gridTemplateColumns: `repeat(${rounds.length}, minmax(220px, 1fr))`, gap: "1rem" }}>
              {rounds.map((round, rIdx) => (
                <div key={rIdx} className="space-y-3">
                  <h3 className="font-semibold text-slate-700">{rIdx === rounds.length - 1 ? "Final" : `Round ${rIdx + 1}`}</h3>
                  {round.matches.map((m, mIdx) => (
                    <MatchCard key={m.id} match={m} rounds={rounds} rIdx={rIdx} mIdx={mIdx} />
                  ))}
                </div>
              ))}
            </div>
          </section>
        )}
      </main>

      <footer className="max-w-6xl mx-auto px-4 py-8 text-xs text-slate-500">
        Built for fun. Keep it spooky, not gory. 👻
      </footer>
    </div>
  );
}

function MatchCard({ match, rounds, rIdx, mIdx }) {
  const A = resolveSlot(match.a, rounds);
  const B = resolveSlot(match.b, rounds);
  const winnerId = match?.winner?.id;
  const isFinal = rIdx === rounds.length - 1;

  return (
    <Card className={`relative ${winnerId ? "ring-1 ring-emerald-300" : ""}`}>
      <CardHeader className="pb-2">
        <CardTitle className="text-base flex items-center gap-2">
          {isFinal ? <Trophy className="h-4 w-4"/> : <Swords className="h-4 w-4"/>}
          <span>{A.name} vs {B.name}</span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="grid grid-cols-2 gap-2 text-sm">
          <div className={`rounded-lg px-2 py-1 ${winnerId === A.id ? "bg-emerald-50" : "bg-slate-50"}`}>{A.name}</div>
          <div className={`rounded-lg px-2 py-1 text-right ${winnerId === B.id ? "bg-emerald-50" : "bg-slate-50"}`}>{B.name}</div>
        </div>
        {match?.winner && (
          <div className="text-xs text-slate-600">
            <div className="flex justify-between"><span>Summary</span><span className="font-medium">{match.winner.summary}</span></div>
            <div className="mt-1 grid grid-cols-2 gap-2">
              <Highlight label={`${A.name} – Best`} text={match.details?.highlights?.aBest} />
              {isFinal && <Highlight label={`${B.name} – Best`} text={match.details?.highlights?.bBest} />}
              <Highlight label={`${A.name} – Tough moment`} text={match.details?.highlights?.aWorst} />
              {isFinal && <Highlight label={`${B.name} – Tough moment`} text={match.details?.highlights?.bWorst} />}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Highlight({ label, text }) {
  if (!text) return null;
  return (
    <div className="rounded-xl bg-slate-50 p-2">
      <div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className="text-xs text-slate-700 line-clamp-3">{text}</div>
    </div>
  );
}

function SettingsMenu({ apiKey, setApiKey, model, setModel, temperature, setTemperature, useOffline, setUseOffline }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant="secondary" size="sm" onClick={() => setOpen(true)} className="gap-2"><Settings2 className="h-4 w-4"/>Settings</Button>
      {open && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center p-4" onClick={() => setOpen(false)}>
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-lg p-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center gap-2 mb-2"><Settings2 className="h-5 w-5"/><h2 className="font-semibold">Simulation Settings</h2></div>
            <div className="space-y-3">
              <div>
                <Label className="text-sm flex items-center gap-2"><KeyRound className="h-4 w-4"/> OpenAI API key</Label>
                <Input type="password" placeholder="sk-..." value={apiKey} onChange={(e) => setApiKey(e.target.value)} className="mt-1"/>
                <div className="text-xs text-slate-500 mt-1">Stored locally in your browser. For demos only.
                </div>
              </div>
              <div className="grid sm:grid-cols-3 gap-3">
                <div>
                  <Label className="text-sm">Model</Label>
                  <Select value={model} onValueChange={setModel}>
                    <SelectTrigger className="mt-1"><SelectValue/></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="gpt-4o-mini">gpt-4o-mini</SelectItem>
                      <SelectItem value="gpt-4o">gpt-4o</SelectItem>
                      <SelectItem value="gpt-4.1-mini">gpt-4.1-mini</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="sm:col-span-2">
                  <Label className="text-sm">Creativity (temperature)</Label>
                  <Input type="number" step="0.1" min="0" max="2" value={temperature} onChange={(e) => setTemperature(parseFloat(e.target.value))} className="mt-1"/>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Switch id="offline2" checked={useOffline} onCheckedChange={setUseOffline}/>
                <Label htmlFor="offline2">Use Offline Simulator</Label>
              </div>
              <div className="pt-2 flex justify-end gap-2">
                <Button variant="secondary" onClick={() => setOpen(false)}>Close</Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function ImportList({ onImport }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  return (
    <>
      <Button variant="secondary" size="sm" onClick={() => setOpen(true)} className="gap-2"><Upload className="h-4 w-4"/>Paste list</Button>
      {open && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center p-4" onClick={() => setOpen(false)}>
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-lg p-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center gap-2 mb-2"><Upload className="h-5 w-5"/><h2 className="font-semibold">Paste one name per line</h2></div>
            <Textarea rows={10} placeholder={"e.g.\nMothman\nBigfoot\nWendigo"} value={text} onChange={(e) => setText(e.target.value)} />
            <div className="pt-3 flex justify-end gap-2">
              <Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
              <Button onClick={() => { onImport(text); setOpen(false); }}>Import</Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
