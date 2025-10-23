# streamlit_app.py
import json, math, random, textwrap, time
from collections import Counter
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
    Tone: PG-13. Absolutely no gore. Keep notes brief and cinematic with occasional dry, dark humor.

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
    Tone: PG-13. Absolutely no gore. Keep notes brief and cinematic with occasional dry, dark humor.

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
    most = next((a.notes for a in atts if a.outcome == "runs_away_crying"), None) \
        or next((a.notes for a in atts if a.outcome == "runs_away"), atts[0].notes)
    least = next((a.notes for a in reversed(atts) if a.outcome in ("stays_put","defends")), atts[-1].notes)
    return SimJSON(attempts=atts, highlights={"most_successful": most, "least_successful": least})

# --------------------------
# Narrative builders (spooky + clear scoring)
# --------------------------
OUTCOME_SENTENCES = {
    "runs_away": [
        "The Macho Man bolts, sprinting like cardio finally found religion.",
        "The Macho Man breaks into a panicked 5K, no medal ceremony required.",
        "The Macho Man abandons the bench with the dignity of a fast-moving shopping cart."
    ],
    "runs_away_crying": [
        "The Macho Man flees in tears—salt water meets sweat in an unholy sports drink.",
        "The Macho Man sprints away bawling; even the dog averts its eyes.",
        "The Macho Man escapes, sobbing into the night like a siren with gym membership."
    ],
    "defends": [
        "The Macho Man hurls a thermos with MLB aspirations.",
        "The Macho Man squares up—thermos first, feelings second.",
        "The Macho Man counterattacks with commuter-grade artillery."
    ],
    "stays_put": [
        "The Macho Man sits immovable, patron saint of Stubborn Benches.",
        "The Macho Man refuses to budge; gravity files an alliance.",
        "The Macho Man remains seated, an oak with opinions."
    ],
    "walks_away": [
        "The Macho Man stands and leaves like he’s quitting a bad movie.",
        "The Macho Man wanders off, the world’s pettiest protest march of one.",
        "The Macho Man drifts away, offended in slow motion."
    ],
}

OUTCOME_SCORE_NOTE = "Scoring: runs_away=+1, runs_away_crying=+2, defends=-1, stays_put=-2, walks_away=0."

def unique_sentence(outcome: Outcome, i: int) -> str:
    bank = OUTCOME_SENTENCES[outcome]
    return bank[i % len(bank)]

def tallies_to_string(t: Counter) -> str:
    order = ["runs_away_crying","runs_away","walks_away","defends","stays_put"]
    labels = {
        "runs_away": "runs away",
        "runs_away_crying": "runs away crying",
        "defends": "defends himself",
        "stays_put": "stays put",
        "walks_away": "walks away",
    }
    parts = [f"{t[k]}× {labels[k]}" for k in order if t[k]]
    return ", ".join(parts) if parts else "no notable reactions"

def attempt_lines(attempts: List[Attempt], attacker: str, defender_label: str) -> str:
    # longform, one unique sentence per attempt + the model’s short note
    lines = []
    for idx, a in enumerate(attempts, start=1):
        sentence = unique_sentence(a.outcome, idx)
        # put the attacker up front; always refer to the target as The Macho Man (or defender_label in finals)
        if defender_label == "The Macho Man":
            perspective = f"**Attempt {idx}** — {attacker} advances; {sentence}"
        else:
            perspective = f"**Attempt {idx}** — {attacker} advances on {defender_label}; {sentence}"
        # include the sim’s concise note for flavor
        lines.append(f"- {perspective} _({a.notes})._")
    return "\n".join(lines)

def round_match_narrative(A, B, details: MatchDetails, is_final: bool, round_number: Optional[int] = None) -> str:
    if not is_final:
        t = Counter([a.outcome for a in details.aLog.attempts])
        score = details.scoreA
        attempts_text = attempt_lines(details.aLog.attempts, A.name, "The Macho Man")
        tally = tallies_to_string(t)

        # Summary block FIRST, then story
        header = f"### Round {round_number}: **{A.name} vs The Macho Man**"
        summary = (
            f"**Overview (10 attempts):** {tally} → **Score {score}**  \n"
            f"{OUTCOME_SCORE_NOTE}\n\n"
            f"**Best moment:** {details.highlights.get('aBest') or 'A rumor crawls under the skin; sprinting ensues.'}  \n"
            f"**Tough moment:** {details.highlights.get('aWorst') or 'A thermos achieves escape velocity.'}"
        )
        prose_intro = (
            f"Night swallows the park. **{A.name}** steps from the margins of folklore, aiming all dread at "
            f"**The Macho Man**, who clutches his thermos like it’s certified ghost-proof. The dog provides moral support and unhelpful commentary."
        )
        result_line = f"**Result:** {'**'+A.name+'** advances.' if details.winnerId == A.id else '**'+B.name+'** advances.'}"

        return textwrap.dedent(f"""
        {header}

        {summary}

        {prose_intro}

        {attempts_text}

        {result_line}
        """).strip()
    else:
        # Final: both attack each other
        tA = Counter([a.outcome for a in details.aLog.attempts])
        tB = Counter([a.outcome for a in (details.bLog.attempts if details.bLog else [])])

        a_lines = attempt_lines(details.aLog.attempts, A.name, B.name)
        b_lines = attempt_lines(details.bLog.attempts if details.bLog else [], B.name, A.name) if details.bLog else ""

        header = f"### Final: **{A.name} vs {B.name}**"
        overview = (
            f"**Tallies (10 attempts each):**  \n"
            f"- {A.name}: {tallies_to_string(tA)} → **Score {details.scoreA}**  \n"
            f"- {B.name}: {tallies_to_string(tB)} → **Score {details.scoreB}**  \n"
            f"{OUTCOME_SCORE_NOTE}"
        )
        flavor = (
            f"Under trees that remember older fears, the bench hosts an ego summit. "
            f"**{A.name}** and **{B.name}** trade hauntings like collectors swapping cursed stamps."
        )
        best = (
            f"**Best moments:**  \n"
            f"- {A.name}: {details.highlights.get('aBest') or 'A shriek like cold iron on glass.'}  \n"
            f"- {B.name}: {details.highlights.get('bBest') or 'A shadow that forgets to be two-dimensional.'}"
        )
        tough = (
            f"**Tough moments:**  \n"
            f"- {A.name}: {details.highlights.get('aWorst') or 'Confidence meets counter-roar.'}  \n"
            f"- {B.name}: {details.highlights.get('bWorst') or 'A brave face with trembly edges.'}"
        )
        winner_name = A.name if details.winnerId == A.id else B.name
        result_line = f"**Result:** **{winner_name}** claims the trophy and immediately misplaces it in a haunted lost-and-found."

        return textwrap.dedent(f"""
        {header}

        {overview}

        {flavor}

        **{A.name} attacks {B.name}:**  
        {a_lines}

        **{B.name} attacks {A.name}:**  
        {b_lines}

        {best}

        {tough}

        {result_line}
        """).strip()


# --------------------------
# Round-by-round simulation
# --------------------------
def simulate_match(A, B, is_final, use_offline, api_key, model, temperature, blurbs) -> MatchDetails:
    if not is_final:
        aLog = offline_sim(A.name) if use_offline else openai_chat(api_key, prompt_man(A.name, blurbs.get(A.id,"")), model, temperature)
        scoreA = compute_score(aLog)
        # Winner logic: if A fails (<=0), the bench wins on behalf of B
        if scoreA > 0:
            winner = A
        elif scoreA == 0:
            winner = A if random.random() < 0.5 else B
        else:
            winner = B
        return MatchDetails(
            aLog=aLog, bLog=None, scoreA=scoreA, scoreB=0, winnerId=winner.id,
            highlights={"aBest": aLog.highlights.get("most_successful"),
                        "aWorst": aLog.highlights.get("least_successful"),
                        "bBest": None, "bWorst": None}
        )
    else:
        aLog = offline_sim(A.name) if use_offline else openai_chat(api_key, prompt_vs(A.name, blurbs.get(A.id,""), B.name, blurbs.get(B.id,"")), model, temperature)
        bLog = offline_sim(B.name) if use_offline else openai_chat(api_key, prompt_vs(B.name, blurbs.get(B.id,""), A.name, blurbs.get(A.id,"")), model, temperature)
        scoreA, scoreB = compute_score(aLog), compute_score(bLog)
        if scoreA == scoreB:
            aCry = sum(1 for a in aLog.attempts if a.outcome == "runs_away_crying")
            bCry = sum(1 for a in bLog.attempts if a.outcome == "runs_away_crying")
            winner = A if aCry > bCry else (B if aCry < bCry else (A if random.random()<0.5 else B))
        else:
            winner = A if scoreA > scoreB else B
        return MatchDetails(
            aLog=aLog, bLog=bLog, scoreA=scoreA, scoreB=scoreB, winnerId=winner.id,
            highlights={"aBest": aLog.highlights.get("most_successful"),
                        "aWorst": aLog.highlights.get("least_successful"),
                        "bBest": bLog.highlights.get("most_successful"),
                        "bWorst": bLog.highlights.get("least_successful")}
        )

def simulate_round(round_index: int, entrants: List[Entrant], rounds: List[Round], use_offline: bool, api_key: str, model: str, temperature: float):
    blurbs = {e.id: e.blurb for e in entrants}
    rnd = rounds[round_index]
    is_final = (round_index == len(rounds)-1)
    narratives = []
    for m_idx, match in enumerate(rnd.matches):
        A = resolve_slot(match.a, rounds)
        B = resolve_slot(match.b, rounds)
        details = simulate_match(A, B, is_final, use_offline, api_key, model, temperature, blurbs)
        match.details = details
        winner = A if details.winnerId == A.id else B
        match.winner = Slot(id=winner.id, name=winner.name)
        match.summary = (f"{A.name}: {details.scoreA} • {B.name}: {details.scoreB}" if is_final
                         else f"{A.name} vs The Macho Man: {details.scoreA} pts.")
        # narrative (with round number for clarity)
        narratives.append(round_match_narrative(A, B, details, is_final, round_number=(round_index + 1 if not is_final else None)))
        time.sleep(0.05)
    return rounds, narratives

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

# --------------------------
# Top controls: Setup (left) and Actions (right)
# --------------------------
col_setup, col_run = st.columns([2,1], vertical_alignment="top")
with col_setup:
    st.subheader("Setup")
    size = st.selectbox("Bracket size", [8,16], index=0)
    st.caption("Enter exactly this many cryptids.")

    # session bootstrap
    if "cryptids" not in st.session_state:
        st.session_state.cryptids: List[Dict[str,str]] = []
    if "entrants" not in st.session_state:
        st.session_state.entrants: List[Entrant] = []
    if "rounds" not in st.session_state:
        st.session_state.rounds: List[Round] = []
    if "curr_round_idx" not in st.session_state:
        st.session_state.curr_round_idx: Optional[int] = None
    if "narratives_log" not in st.session_state:
        st.session_state.narratives_log: List[str] = []

    def reset_entries():
        st.session_state.cryptids = []
    def hard_reset_tournament():
        st.session_state.entrants = []
        st.session_state.rounds = []
        st.session_state.curr_round_idx = None
        st.session_state.narratives_log = []

    # entry helpers
    c1, c2, c3 = st.columns(3)
    with c1:
        st.button("Add empty slot", on_click=lambda: (
            st.session_state.cryptids.append({"id": uid("c"), "name": "", "blurb": ""})
            if len(st.session_state.cryptids) < size else None
        ))
    with c2:
        if st.button("Quick-fill samples"):
            picks = shuffle(SAMPLE_CRYPTIDS)[:size]
            st.session_state.cryptids = [{"id": uid("c"), "name": n, "blurb": b} for n, b in picks]
    with c3:
        st.button("Reset entries", type="secondary", on_click=reset_entries)

    paste = st.text_area("Paste one name per line (optional)", height=100, placeholder="Mothman\nBigfoot\nWendigo")
    if st.button("Import pasted list"):
        lines = [ln.strip() for ln in paste.splitlines() if ln.strip()]
        if lines:
            st.session_state.cryptids = [{"id": uid("c"), "name": ln, "blurb": ""} for ln in lines[:size]]

    # editable rows
    for i in range(len(st.session_state.cryptids)):
        with st.expander(f"#{i+1} – {st.session_state.cryptids[i]['name'] or 'Unnamed'}", expanded=True):
            st.session_state.cryptids[i]["name"] = st.text_input("Name", key=f"name_{i}", value=st.session_state.cryptids[i]["name"])
            st.session_state.cryptids[i]["blurb"] = st.text_area("Optional blurb / lore", key=f"blurb_{i}", value=st.session_state.cryptids[i]["blurb"])
            if st.button("Remove", key=f"rm_{i}"):
                st.session_state.cryptids.pop(i)
                st.experimental_rerun()

    st.caption(f"Slots used: {len(st.session_state.cryptids)}/{size}")

with col_run:
    st.subheader("Actions")
    can_generate = len([c for c in st.session_state.cryptids if c.get("name","").strip()]) == size
    if st.button("Generate bracket", disabled=not can_generate):
        cleaned = st.session_state.cryptids[:size]
        shuffled = shuffle(cleaned)
        entrants = [Entrant(id=c["id"], name=c["name"].strip(), blurb=c.get("blurb","").strip(), seed=i+1) for i,c in enumerate(shuffled)]
        st.session_state.entrants = entrants
        st.session_state.rounds = build_rounds(entrants)
        st.session_state.curr_round_idx = 0
        st.session_state.narratives_log = []
        st.success("Bracket generated. Use **Next round** below.")

    # Next round sits near bracket & stories, so we just show reminders here
    st.button("Reset tournament", type="secondary", on_click=lambda: (
        st.session_state.update({"entrants": [], "rounds": [], "curr_round_idx": None, "narratives_log": []})
    ))
# --------------------------
# Bracket rendering helpers
# --------------------------
def highlight(text: str):
    st.markdown(
        "<div style='background:#10151d;border-radius:10px;padding:8px;font-size:12px;color:#d7e0ea;'>"
        + text +
        "</div>",
        unsafe_allow_html=True,
    )

def render_match(m: Match, rounds: List[Round], is_final: bool):
    A = resolve_slot(m.a, rounds)
    B = resolve_slot(m.b, rounds)

    st.markdown(f"**{A.name}** vs **{B.name}**")
    if m.winner:
        # show short summary line that matches our new copy
        st.caption(
            m.summary if is_final
            else f"{A.name} vs The Macho Man: {m.details.scoreA if m.details else 0} pts."
        )

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

# --------------------------
# Results + Bracket + Next button in one band (top)
# --------------------------
st.subheader("Results & Bracket")

# Controls + Bracket + Stories side-by-side
left, right = st.columns([1.4, 1.0], vertical_alignment="top")

with left:
    # NEXT ROUND button lives here beside the bracket
    next_disabled = not (st.session_state.get("rounds") and st.session_state.curr_round_idx is not None)
    if st.button("▶️ Next round", disabled=next_disabled or (st.session_state.curr_round_idx is not None and st.session_state.curr_round_idx >= len(st.session_state.rounds)) or (not use_offline and not api_key)):
        ridx = st.session_state.curr_round_idx
        rounds = st.session_state.rounds
        entrants = st.session_state.entrants
        updated_rounds, narratives = simulate_round(ridx, entrants, rounds, use_offline, api_key, model, temperature)
        st.session_state.rounds = updated_rounds
        st.session_state.narratives_log.extend(narratives)
        st.session_state.curr_round_idx = ridx + 1
        if st.session_state.curr_round_idx >= len(st.session_state.rounds):
            st.success("Tournament complete! 🏆")

    # Bracket at top
    if st.session_state.get("rounds"):
        rounds = st.session_state.rounds
        cols = st.columns(len(rounds))
        for i, rnd in enumerate(rounds):
            with cols[i]:
                st.markdown(f"### {'Final' if i == len(rounds)-1 else f'Round {i+1}'}")
                for m in rnd.matches:
                    with st.container(border=True):
                        render_match(m, rounds, is_final=(i == len(rounds) - 1))

with right:
    # Stories on the right, newest on top
    if st.session_state.get("narratives_log"):
        st.markdown("### 📖 Round Recaps")
        for idx, text in enumerate(reversed(st.session_state.narratives_log), 1):
            with st.expander(f"Story {idx}", expanded=(idx == 1)):
                st.markdown(text)


# --------------------------
# Bracket rendering
# --------------------------
def highlight(text: str):
    st.markdown(
        "<div style='background:#10151d;border-radius:10px;padding:8px;font-size:12px;color:#d7e0ea;'>"
        + text +
        "</div>", unsafe_allow_html=True
    )

def render_match(m: Match, rounds: List[Round], is_final: bool):
    A = resolve_slot(m.a, rounds)
    B = resolve_slot(m.b, rounds)
    st.markdown(f"**{A.name}** vs **{B.name}**")
    if m.winner:
        st.caption(m.summary)
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
                    render_match(m, rounds, is_final=(i == len(rounds) - 1))

# --------------------------
# Narratives (long-form, spooky)
# --------------------------
if st.session_state.get("narratives_log"):
    st.subheader("📖 Round Recaps")
    # show most recent round's narratives on top
    for idx, text in enumerate(reversed(st.session_state.narratives_log), 1):
        with st.expander(f"Story {idx}", expanded=(idx == 1)):
            st.markdown(text)

# --------------------------
# Export buttons
# --------------------------
if st.session_state.get("rounds") and st.session_state.get("entrants"):
    # Export current state (including winners & summaries)
    export_data = {
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
                        "details": {
                            "scoreA": m.details.scoreA if m.details else None,
                            "scoreB": m.details.scoreB if m.details else None,
                            "highlights": m.details.highlights if m.details else None,
                        } if m.details else None,
                    }
                    for m in rnd.matches
                ]
            }
            for rnd in st.session_state.rounds
        ],
        "stories": st.session_state.narratives_log,
    }
    st.download_button(
        "⬇️ Export tournament JSON",
        data=json.dumps(export_data, indent=2),
        file_name="cryptid_bracket_state.json",
        mime="application/json",
    )

    transcript = "\n\n".join(st.session_state.narratives_log)
    st.download_button(
        "⬇️ Export stories (markdown)",
        data=transcript,
        file_name="cryptid_bracket_stories.md",
        mime="text/markdown",
    )
