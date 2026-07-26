"""Marathon corpus — one grandma, one session, hours of talking.

Why this exists (and why night_stress/corpus.py wasn't enough):

``scripts/night_stress/run.py:231`` assigns every prompt its own
``session_id``. That harness is therefore 201 independent FIRST TURNS.
It has never crossed an episode-eviction boundary, never exercised a
turnover letter, and its 20 "memory probe" prompts probe a session with
no prior turns. The single property the whole product is built on —
Principle #7, "grandma can never tell when it refreshed context" — has
never been measured end to end.

This corpus is the opposite shape: ONE session, hundreds of turns, with
facts planted at known indices and re-asked at measured distances.

**The cliff we are aiming at.** ``prompt.py:680`` reads the
``context_window`` slider (default 5) and keeps ``5 + 5*5 = 30``
episodes. ``loop.py:609-611`` writes TWO episodes per turn (user +
assistant). So verbatim history is **~15 turns**. Past that, a fact
survives only if ``search_episodes_hybrid`` retrieves it — and the
keyword half of that is ``prompt.py::_extract_keywords``, which
lowercases, splits on spaces, drops a 60-word stopword list, and keeps
the FIRST FIVE tokens.

Grandma crosses that cliff in ~15 minutes and then again all afternoon.
So probes are placed at distances that straddle it: inside the verbatim
window, just past it, and far past it.

**Voice.** The night_stress corpus is engineer English ("Explain the
difference between TCP and UDP"). Real input from a ballroom audience is
pronoun-heavy, run-on, and mangled by voice transcription. Retrieval
degrades very differently on "what was that thing about my medicare"
than on a well-formed query, and that difference is the entire point —
so the filler and the probes here are written the way people actually
talk.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Fact:
    """A thing grandma tells her agent once, then expects it to know."""

    key: str
    # What she says when she establishes it.
    seed: str
    # How she asks for it later — deliberately vague and pronoun-heavy,
    # because that is how the ask actually arrives.
    probes: list[str]
    # Lowercased substrings; ANY match counts as recall.
    expect: list[str]
    # Turn index where `seed` is spoken. Filled in by build_script().
    seed_turn: int = -1


FACTS: list[Fact] = [
    Fact(
        key="dog_name",
        seed="oh i should tell you about my dog, her name is Biscuit, she's a "
             "little beagle and she's twelve years old now bless her heart",
        probes=[
            "what was my dog's name again hon",
            "remind me what i named her, the beagle",
            "you remember my little girl right, whats her name",
        ],
        expect=["biscuit"],
    ),
    Fact(
        key="grandson",
        seed="my grandson Tyler just started playing the trombone in the "
             "school band, he's eleven",
        probes=[
            "whats that boy of mine that plays the horn called",
            "my grandson, the musical one, what was his name",
            "which instrument did tyler pick up again",
        ],
        expect=["tyler", "trombone"],
    ),
    Fact(
        key="doctor",
        seed="i have my appointment with Dr. Reyes on Thursday at 2 in the "
             "afternoon, i keep forgetting it",
        probes=[
            "when is that appointment i told you about",
            "who am i seeing thursday",
            "what time do i have to be at the doctor",
        ],
        expect=["reyes", "thursday", "2"],
    ),
    Fact(
        key="allergy",
        seed="one thing thats important, im allergic to penicillin, it makes "
             "me break out something awful",
        probes=[
            "is there a medicine im not supposed to take",
            "what am i allergic to again",
            "the doctor asked about allergies, what do i tell him",
        ],
        expect=["penicillin"],
    ),
    Fact(
        key="husband",
        seed="my husband Harold passed in 2019, our anniversary was june 12th, "
             "we were married 51 years",
        probes=[
            "when was our anniversary, me and my husband",
            "whats the date i always get sad about in june",
            "how long were harold and i married",
        ],
        expect=["june 12", "june 12th", "51"],
    ),
    Fact(
        key="pharmacy",
        seed="i get my prescriptions at the pharmacy on Elm Street, im on a "
             "Part D plan",
        probes=[
            "where do i pick up my pills",
            "what street is my drugstore on",
            "what kind of medicare plan did i say i had",
        ],
        expect=["elm", "part d"],
    ),
    Fact(
        key="car",
        seed="my car is a 2011 Buick and Sal is the fella who works on it for me",
        probes=[
            "whos my mechanic",
            "what kind of car do i drive again",
            "who should i call about the car",
        ],
        expect=["sal", "buick"],
    ),
    Fact(
        key="neighbor",
        seed="Doris next door checks on me on sundays, shes been my neighbor "
             "for thirty years",
        probes=[
            "whats my neighbors name",
            "who comes by on sundays",
            "the lady next door, what do i call her",
        ],
        expect=["doris"],
    ),
    Fact(
        key="bank",
        seed="i bank at Adirondack Trust, been there since Harold and i got married",
        probes=[
            "where do i keep my money",
            "whats my bank called",
            "who do i call about my checking account",
        ],
        expect=["adirondack"],
    ),
    Fact(
        key="church",
        seed="my favorite hymn is How Great Thou Art, we sing it at the "
             "methodist church on sundays",
        probes=[
            "whats that song i like at church",
            "which church do i go to",
            "what hymn did i tell you was my favorite",
        ],
        expect=["how great thou art", "methodist"],
    ),
]


# Realistic filler. Grandma is not running a benchmark — she is chatting,
# asking small favors, and repeating herself. None of these establish a
# probed fact; they exist to push turns (and therefore episodes) past the
# eviction cliff the way a real afternoon would.
FILLER: list[str] = [
    "its raining again here, third day straight",
    "how do i make the text bigger on this thing",
    "whats the weather looking like tomorrow",
    "can you help me write a note to my sister",
    "i cant find my glasses again, isnt that always the way",
    "whats a good recipe for pot roast",
    "my knee has been bothering me something fierce",
    "do you think i should get one of those smart tvs",
    "tell me something interesting i didnt know",
    "how do you spell restaurant",
    "what year did the war end, the second one",
    "is it supposed to snow this week",
    "i watched that show last night, the one with the detective",
    "how much is a stamp these days",
    "can you remind me to take my pills at 8",
    "whats the capital of vermont",
    "my sister keeps sending me those chain emails",
    "how do i turn the volume up",
    "what day of the week is the fourth going to be",
    "do you know any jokes",
    "i need to remember to water the plants",
    "whats a good gift for an eleven year old boy",
    "how long do you boil an egg",
    "the news is so depressing lately",
    "can you look up when the pharmacy closes",
    "i think my computer is running slow",
    "whats that word for when you cant sleep",
    "how do i print something",
    "my hands get so cold in the winter",
    "what should i make for supper",
    "is coffee bad for you or good for you, they keep changing it",
    "i miss having a garden",
    "how do you get a stain out of a tablecloth",
    "whats the difference between medicare and medicaid",
    "can you help me find a phone number",
    "i dont understand these new phones at all",
    "what time does the sun go down tonight",
    "do i need an umbrella today",
    "how do i save a picture",
    "tell me about the weather up in the mountains",
]


@dataclass
class Turn:
    """One thing grandma says, plus what we expect to happen."""

    index: int
    text: str
    kind: str  # "seed" | "probe" | "filler"
    fact_key: str | None = None
    # For probes: how many turns since the fact was seeded.
    distance: int = 0
    expect: list[str] = field(default_factory=list)


def build_script(total_turns: int = 500) -> list[Turn]:
    """Build the marathon script.

    Facts are seeded early and staggered, then re-probed at distances
    that straddle the ~15-turn verbatim window: some probes land while
    the seed is still in literal history (control — these MUST pass, and
    a failure there means something much worse than eviction), and the
    rest land well past it, where retrieval is the only thing keeping
    the memory alive.
    """
    turns: list[Turn] = []
    filler_i = 0

    def add_filler() -> None:
        nonlocal filler_i
        turns.append(Turn(index=len(turns), text=FILLER[filler_i % len(FILLER)],
                          kind="filler"))
        filler_i += 1

    # Stagger the seeds across the opening so probes at a given distance
    # don't all collide on the same stretch of conversation.
    for i, fact in enumerate(FACTS):
        for _ in range(2):
            add_filler()
        fact.seed_turn = len(turns)
        turns.append(Turn(index=len(turns), text=fact.seed, kind="seed",
                          fact_key=fact.key))

    # Probe distances chosen around the cliff:
    #   5  — inside the verbatim window (control)
    #   12 — right at the edge
    #   25, 60, 120, 240 — retrieval-only territory
    distances = [5, 12, 25, 60, 120, 240]
    scheduled: dict[int, tuple[Fact, int]] = {}
    for fact in FACTS:
        for d in distances:
            at = fact.seed_turn + d
            while at in scheduled:
                at += 1
            scheduled[at] = (fact, d)

    while len(turns) < total_turns:
        idx = len(turns)
        if idx in scheduled:
            fact, dist = scheduled[idx]
            probe = fact.probes[(dist // 12) % len(fact.probes)]
            turns.append(Turn(index=idx, text=probe, kind="probe",
                              fact_key=fact.key, distance=dist,
                              expect=fact.expect))
        else:
            add_filler()

    return turns[:total_turns]


def fact_by_key(key: str) -> Fact:
    for f in FACTS:
        if f.key == key:
            return f
    raise KeyError(key)
