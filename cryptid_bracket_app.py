import json, math, random, textwrap, time
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Literal
import requests
import streamlit as st

# --------------------------
# Types & constants
# --------------------------
Outcome = Literal["runs_away","runs_away_crying","defends","stays_put","walks_away"]
SCORE_MAP: Dict[Outcome, int] = {
    "runs_away": 1,
    "runs_away_crying": 2,
    "defends": -1,
    "stays_put": -2,
    "walks_away": 0,
}
OUTCOMES = list(SCORE_MAP.keys())

@dataclass
class Attempt:
    attempt: int
    outcome: Outcome
    notes: str

@dataclass
class SimJSON:
    attempts: List[Attempt]
    highlights: Dict[str, Optional[str]] = field(default_factory=dict)

@dataclass
class Entrant:
    id: str
    name: str
    blurb: str = ""
    seed: int = 0

@dataclass
class Slot:
    id: Optional[str] = None
    name: Optional[str] = None
    from_round: Optional[int] = None
    from_match: Optional[int] = None

@dataclass
class MatchDetails:
    aLog: SimJSON
    bLog: Optional[SimJSON]
    scoreA: int
    scoreB: int
    winnerId: str
    highlights: Dict[str, Optional[str]]

@dataclass
class Match:
    id: str
    a: Slot
    b: Slot
    winner: Optional[Slot] = None
    summary: str = ""
    details: Optional[MatchDetails] = None

@dataclass
class Round:
    matches: List[Match]

# --------------------------
# Helpers
# --------------------------
def uid(prefix="id"):
    return f"{prefix}_{random.randrange(1_000_000):06d}"

def shuffle(seq):
    s = list(seq)
    random.shuffle(s)
    return s

def compute_score(sim: SimJSON) -> int:
    return sum(SCORE_MAP[a.outcome] for a in sim.attempts)

def resolve_slot(slot: Slot, rounds: List[Round]) -> Slot:
    if slot.name and slot.id:
        return slot
    if slot.from_round is not None and slot.from_match is not None:
        m = rounds[slot.from_round].matches[slot.from_match]
        if m.winner:
            return Slot(id=m.winner.id, name=m.winner.name)
    return Slot(id="tbd", name="TBD")

SAMPLE_CRYPTIDS = [
    ("Mothman","A winged, red-eyed harbinger of doom."),
    ("Bigfoot","Massive, elusive primate in North American woods."),
    ("Loch Ness Monster","Shy lake-dweller with a serpentine silhouette."),
    ("Chupacabra","Spiny-backed livestock drainer from the dark."),
    ("Jersey Devil","Hooved, winged shrieker of Pine Barrens lore."),
    ("Wendigo","Gaunt, ravenous spirit of endless winter hunger."),
    ("Kraken","A titanic tentacled terror from the deep."),
    ("Yeti","Snowbound colossus with a thunderous roar."),
    ("Banshee","Wailing omen whose cry chills the marrow."),
    ("Mokele-mbembe","Swamp-bound sauropod of whispered sightings."),
    ("Skinwalker","Shapeshifting mimic with malevolent intent."),
    ("Thunderbird","Storm-calling avian giant with shadowed wings."),
    ("Shadow Person","A living absence lurking at the edge of sight."),
    ("Bunyip","Billabong beast with a dreadful bellow."),
    ("Mongolian Death Worm","Arid burrower rumored to spit lightning."),
    ("Spring-Heeled Jack","Grinning leaper cloaked in coal-smoke."),
]

# --------------------------
# Bracket construction
# --------------------------
def build_rounds(entrants: List[Entrant]) -> List[Round]:
    n = len(entrants)
    total = int(math.log2(n))
    rounds: List[Round] = []

    # Round 0 pairings (sequential)
    r0_matches = []
    for i in range(0, n, 2):
        a, b = entrants[i], entrants[i+1]
        r0_matches.append(Match(
            id=uid("m"),
            a=Slot(id=a.id, name=a.name),
            b=Slot(id=b.id, name=b.name),
        ))
    rounds.append(Round(matches=r0_matches))

    # Follow-on rounds reference previous winners
    for r in range(1, total):
        prev = rounds[r-1]
        matches = []
        for i in range(0, len(prev.matches), 2):
            matches.append(Match(
                id=uid("m"),
                a=Slot(from_round=r-1, from_match=i),
                b=Slot(from_round=r-1, from_match=i+1),
            ))
        rounds.append(Round(matches=matches))
    return rounds

# --------------------------
# Prompts & OpenAI call
# --------------------------
def prompt_man(attacker_name: str, attacker_blurb: str) -> str:
    return textwrap.dedent(f"""
    You are simulating a horror-scare attempt sequence. Return STRICT JSON only per the provided schema.

    Attacker: {attacker_name}. Description: {attacker_blurb or "(no extra lore)"}.
    Target: A lone, rage-prone macho man sitting on a park bench at night with his dog.
    Tone: PG-13. Absolutely no gore. Keep notes brief and cinematic.

    Rules: 10 independent attempts. For each attempt choose exactly one outcome from this list (lowercase, exact match):
    - runs_away
    - runs_away_crying
    - defends
    - stays_put
    - walks_away

    Scoring (FYI): runs_away=+1, runs_away_crying=+2, defends=-1, stays_put=-2, walks_away=0.

    JSON schema:
    {{
      "attempts": [
        {{ "attempt": 1, "outcome": "runs_away" | "runs_away_crying" | "defends" | "stays_put" | "walks_away", "notes": "short vivid note" }}
      ],
      "highlights": {{
        "most_successful": "",
        "least_successful": ""
      }}
    }}

    Respond with JSON only.
    """).strip()

def prompt_vs(attacker: str, a_blurb: str, defender: str, d_blurb: str) -> str:
    return textwrap.dedent(f"""
    You are simulating a cryptid scaring another cryptid. Return STRICT JSON only.

    Scenario: A night-time park bench. The defender ({defender}) is seated alone on the bench. The attacker ({attacker}) tries to scare them away.
    Attacker description: {a_blurb or "(no extra lore)"}.
    Defender description: {d_blurb or "(no extra lore)"}.
    Tone: PG-13. Absolutely no gore. Keep notes brief and cinematic.

    Rules: 10 independent attempts. For each attempt choose exactly one outcome (exact lowercase):
    - runs_away
    - runs_away_crying
    - defends
    - stays_put
    - walks_away

    Scoring (FYI): runs_away=+1, runs_away_crying=+2, defends=-1, stays_put=-2, walks_away=0.

    JSON schema:
    {{
      "attempts": [
        {{ "attempt": 1, "outcome": "runs_away" | "runs_away_crying" | "defends" | "stays_put" | "walks_away", "notes": "short vivid note" }}
      ],
      "highlights": {{
        "most_successful": "",
        "least_successful": ""
      }}
    }}

    Respond with JSON only.
    """).strip()

def openai_chat(api_key: str, prompt: str, model: str = "gpt-4o-mini", temperature: float = 0.7) -> SimJSON:
    url = "https://api.openai.com/v1/chat/completions"
    body = {
        "model": model,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "You are an impartial simulation engine. Always return STRICT JSON that matches the schema with allowed outcome values only."},
            {"role": "user", "content": prompt}
        ],
    }
    r = requests.post(url, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }, json=body, timeout=60)
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    # Parse & validate
    raw = json.loads(content)
    attempts = []
    for i, a in enumerate(raw.get("attempts", []), start=1):
        oc = a["outcome"]
        if oc not in OUTCOMES:
            raise ValueError(f"Bad outcome at attempt {i}: {oc}")
        attempts.append(Attempt(attempt=a.get("attempt", i), outcome=oc, notes=a.get("notes","")))
    highlights = raw.get("highlights", {})
    return SimJSON(attempts=attempts, highlights=highlights)

def offline_sim(name: str) -> SimJSON:
    atts = []
    for i in range(10):
        r = random.random()
        if r < 0.15:
            oc = "runs_away_crying"
            note = f"{name} whispers a name from the void; the target bolts sobbing"
        elif r < 0.45:
            oc = "runs_away"
            note = f"{name} looms from fog; the target sprints"
        elif r < 0.7:
            oc = "walks_away"
            note = f"{name} rattles a sign; the target just stands and leaves"
        elif r < 0.9:
            oc = "defends"
            note = f"{name} gets pelted with the man's thermos"
        else:
            oc = "stays_put"
            note = f"{name} flickers; the target stays stubbornly seated"
        atts.append(Attempt(attempt=i+1, outcome=oc, notes=note))
    # simple highlights
    most = next((a.notes for a in atts if a.outcome == "runs_away_crying"), None) \
        or next((a.notes for a in atts if a.outcome == "runs_away"), atts[0].notes)
    least = next((a.notes for a in reversed(atts) if a.outcome in ("stays_put","defends")), atts[-1].notes)
    return SimJSON(attempts=atts, highlights={"most_successful": most, "least_successful": least})

# --------------------------
# Simulation driver
# --------------------------
def simulate_tournament(entrants: List[Entrant], use_offline: bool, api_key: str, model: str, temperature: float):
    rounds = build_rounds(entrants)
    # map id->blurb
    blurbs = {e.id: e.blurb for e in entrants}

    for r_idx, rnd in enumerate(rounds):
        is_final = (r_idx == len(rounds)-1)
        for m_idx, match in enumerate(rnd.matches):
            A = resolve_slot(match.a, rounds)
            B = resolve_slot(match.b, rounds)
            # Run sim(s)
            if not is_final:
                if use_offline:
                    aLog = offline_sim(A.name)
                else:
                    aLog = openai_chat(api_key, prompt_man(A.name, blurbs.get(A.id,"")), model, temperature)
                scoreA = compute_score(aLog)
                scoreB = 0
                # Winner logic (A vs bench)
                if scoreA > 0:
                    winner = A
                elif scoreA == 0:
                    winner = A if random.random() < 0.5 else B
                else:
                    winner = B
                match.details = MatchDetails(
                    aLog=aLog, bLog=None, scoreA=scoreA, scoreB=scoreB,
                    winnerId=winner.id,
                    highlights={"aBest": aLog.highlights.get("most_successful"),
                                "aWorst": aLog.highlights.get("least_successful"),
                                "bBest": None, "bWorst": None}
                )
                match.winner = Slot(id=winner.id, name=winner.name)
                match.summary = f"{A.name} vs bench: {scoreA} pts."
            else:
                # Final: A scares B and B scares A
                if use_offline:
                    aLog = offline_sim(A.name)
                    bLog = offline_sim(B.name)
                else:
                    aLog = openai_chat(api_key, prompt_vs(A.name, blurbs.get(A.id,""), B.name, blurbs.get(B.id,"")), model, temperature)
                    bLog = openai_chat(api_key, prompt_vs(B.name, blurbs.get(B.id,""), A.name, blurbs.get(A.id,"")), model, temperature)
                scoreA = compute_score(aLog)
                scoreB = compute_score(bLog)
                if scoreA == scoreB:
                    aCry = sum(1 for a in aLog.attempts if a.outcome == "runs_away_crying")
                    bCry = sum(1 for a in bLog.attempts if a.outcome == "runs_away_crying")
                    if aCry != bCry:
                        winner = A if aCry > bCry else B
                    else:
                        winner = A if random.random() < 0.5 else B
                else:
                    winner = A if scoreA > scoreB else B
                match.details = MatchDetails(
                    aLog=aLog, bLog=bLog, scoreA=scoreA, scoreB=scoreB,
                    winnerId=winner.id,
                    highlights={"aBest": aLog.highlights.get("most_successful"),
                                "aWorst": aLog.highlights.get("least_successful"),
                                "bBest": bLog.highlights.get("most_successful"),
                                "bWorst": bLog.highlights.get("least_successful")}
                )
                match.winner = Slot(id=winner.id, name=winner.name)
                match.summary = f"{A.name}: {scoreA} • {B.name}: {scoreB}"
            # tiny delay so the UI can show progress
            yield r_idx, m_idx, rounds

# --------------------------
# UI
# --------------------------
st.set_page_config(page_title="Cryptid Scare Bracket (Streamlit)", page_icon="👻", layout="wide")
st.title("👻 Cryptid Scare Bracket")

with st.sidebar:
    st.header("Simulation Settings")
    use_offline = st.toggle("Use Offline Simulator", value=True, help="Test without calling OpenAI.")
    api_key = st.text_input("OpenAI API key", type="password", disabled=use_offline)
    model = st.selectbox("Model", ["gpt-4o-mini","gpt-4o","gpt-4.1-mini"], index=0, disabled=use_offline)
    temperature = st.slider("Creativity (temperature)", 0.0, 2.0, 0.7, 0.1)

col_setup, col_run = st.columns([2,1])
with col_setup:
    st.subheader("Setup")
    size = st.selectbox("Bracket size", [8,16], index=0)
    st.caption("Enter exactly this many cryptids.")

    if "cryptids" not in st.session_state:
        st.session_state.cryptids = []

    def reset_for_size():
        st.session_state.cryptids = []

    if st.button("Reset entries", on_click=reset_for_size, type="secondary"):
        pass

    # Entry helpers
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("Add empty slot"):
            if len(st.session_state.cryptids) < size:
                st.session_state.cryptids.append({"id": uid("c"), "name": "", "blurb": ""})
    with c2:
        if st.button("Quick-fill samples"):
            picks = shuffle(SAMPLE_CRYPTIDS)[:size]
            st.session_state.cryptids = [{"id": uid("c"), "name": n, "blurb": b} for n,b in picks]
    with c3:
        paste = st.text_area("Paste one name per line", height=100, placeholder="Mothman\nBigfoot\nWendigo")
        if st.button("Import pasted list"):
            lines = [ln.strip() for ln in paste.splitlines() if ln.strip()]
            if lines:
                st.session_state.cryptids = [{"id": uid("c"), "name": ln, "blurb": ""} for ln in lines[:size]]

    # Editable grid
    for i in range(len(st.session_state.cryptids)):
        with st.expander(f"#{i+1} – {st.session_state.cryptids[i]['name'] or 'Unnamed'}", expanded=True):
            st.session_state.cryptids[i]["name"] = st.text_input("Name", key=f"name_{i}", value=st.session_state.cryptids[i]["name"])
            st.session_state.cryptids[i]["blurb"] = st.text_area("Optional blurb/lore", key=f"blurb_{i}", value=st.session_state.cryptids[i]["blurb"])
            if st.button("Remove", key=f"rm_{i}"):
                st.session_state.cryptids.pop(i)
                st.experimental_rerun()

    st.caption(f"Slots used: {len(st.session_state.cryptids)}/{size}")

with col_run:
    st.subheader("Run")
    can_generate = len([c for c in st.session_state.cryptids if c.get("name","").strip()]) == size
    if st.button("Generate bracket", disabled=not can_generate):
        cleaned = st.session_state.cryptids[:size]
        shuffled = shuffle(cleaned)
        entrants = [Entrant(id=c["id"], name=c["name"].strip(), blurb=c.get("blurb","").strip(), seed=i+1) for i,c in enumerate(shuffled)]
        st.session_state.entrants = entrants
        st.session_state.rounds = build_rounds(entrants)
        st.session_state.results_ready = False

    if "entrants" in st.session_state and st.session_state.get("rounds"):
        start = st.button("Start simulation", disabled=(not use_offline and not api_key))
        if start:
            with st.status("Running tournament…", expanded=True) as status:
                for r_idx, m_idx, rounds in simulate_tournament(
                    st.session_state.entrants, use_offline, api_key, model, temperature
                ):
                    status.update(label=f"Round {r_idx+1}, Match {m_idx+1} complete")
                    st.session_state.rounds = rounds
                    time.sleep(0.05)
                status.update(label="Tournament complete! 🏆")
                st.session_state.results_ready = True

# --------------------------
# Bracket rendering
# --------------------------
def highlight(text):
    st.markdown(f"<div style='background:#f6f7fb;border-radius:10px;padding:8px;font-size:12px;color:#334'>"+text+"</div>", unsafe_allow_html=True)

def render_match(m: Match, rounds: List[Round], is_final: bool):
    A = resolve_slot(m.a, rounds)
    B = resolve_slot(m.b, rounds)
    win = m.winner.id if m.winner else None
    st.markdown(f"**{A.name}** vs **{B.name}**")
    if m.winner:
        st.markdown(f"*Summary:* {m.summary}")
        # highlights
        d = m.details
        if d:
            c1, c2 = st.columns(2)
            with c1:
                if d.highlights.get("aBest"):
                    highlight(f"**{A.name} – Best:** {d.highlights['aBest']}")
                if d.highlights.get("aWorst"):
                    highlight(f"**{A.name} – Tough moment:** {d.highlights['aWorst']}")
            with c2:
                if is_final:
                    if d.highlights.get("bBest"):
                        highlight(f"**{B.name} – Best:** {d.highlights['bBest']}")
                    if d.highlights.get("bWorst"):
                        highlight(f"**{B.name} – Tough moment:** {d.highlights['bWorst']}")
        st.success(f"Winner: {m.winner.name}")

if st.session_state.get("rounds"):
    st.subheader("Bracket")
    rounds = st.session_state.rounds
    cols = st.columns(len(rounds))
    for i, rnd in enumerate(rounds):
        with cols[i]:
            st.markdown(f"### {'Final' if i == len(rounds)-1 else f'Round {i+1}'}")
            for m in rnd.matches:
                with st.container(border=True):
                    render_match(m, rounds, is_final=(i==len(rounds)-1))

# Export
if st.session_state.get("rounds") and st.session_state.get("entrants"):
    data = {
        "entrants": [e.__dict__ for e in st.session_state.entrants],
        "rounds": [
            {
                "matches": [
                    {
                        "id": m.id,
                        "a": m.a.__dict__,
                        "b": m.b.__dict__,
                        "winner": m.winner.__dict__ if m.winner else None,
                        "summary": m.summary,
                        "details": None,  # keep export small; could include if desired
                    }
                    for m in rnd.matches
                ]
            } for rnd in st.session_state.rounds
        ]
    }
    st.download_button("Export bracket JSON", data=json.dumps(data, indent=2), file_name="cryptid_bracket.json", mime="application/json")
