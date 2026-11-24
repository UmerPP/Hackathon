"""An offline, example-based character n-gram classifier and campus gazetteer."""
import re
import unicodedata
from language import predict
from collections import Counter
from difflib import SequenceMatcher
from functools import lru_cache

DEPTS = ("EE", "CSE", "IS", "Library", "Cafeteria", "Labs")
PLACES = {
    "Library": ("library", "kutub khana"),
    "Cafeteria": ("cafeteria", "canteen", "cafe"),
    "CSE block": ("cse block", "cse department", "cs block"),
    "EE block": ("ee block", "electrical block"),
    "IS office": ("is office", "it office"),
    "Main gate": ("main gate", "main entrance"),
    "CSE lab": ("cse lab", "cs lab", "computer lab"),
    **{f"Lab {i}": (f"lab {i}", f"lab{i}", f"laboratory {i}", f"lab {word}", f"lab number {i}", f"lab no {i}") for i, word in enumerate(("one","two","three","four","five","six"),1)},
}
# Each label has bilingual examples, a default owner, and a base priority.
TOPICS = {
 "WiFi": ("IS", 2, "wifi|wi fi|wireless|internet|network connection|net nahi chal raha|net slow|router|signal nahi"),
 "Cooling": ("EE", 1, "ac|a c|air conditioner|air conditioning|cooling|thanda nahi|bohat garmi|too hot|aircon"),
 "Projector": ("CSE", 2, "projector|projection screen|projekter|multimedia display"),
 "Computer": ("CSE", 2, "computer|desktop|keyboard|monitor|mouse|pc|system boot"),
 "Audio system": ("CSE", 2, "speaker|speakers|microphone|sound system|audio system|awaaz nahi"),
 "Lighting": ("EE", 1, "light|bulb|tube light|batti|andhera|lights flickering"),
 "Power": ("EE", 2, "electricity|power outage|bijli|socket|switchboard|breaker"),
 "Fan": ("EE", 1, "fan|pankha|pankhay"),
 "Toaster": ("Cafeteria", 1, "toaster|bread toaster|toast machine|bread slots|bread will not toast|toast nahi"),
 "Food": ("Cafeteria", 2, "food|khana|stale meal|bad lunch|roti|expired milk"),
 "Library equipment": ("Library", 2, "barcode scanner|checkout scanner|library kiosk|self checkout machine|book scanner|return machine"),
 "Books": ("Library", 1, "book|kitab|borrowing|shelf|catalogue"),
 "Water supply": ("Labs", 2, "water cooler|drinking water|pani nahi|tap dry|water dispenser"),
 "Water leak": ("Labs", 2, "water leak|pani tapak|pipe leaking|flooding|paani beh"),
 "Cleanliness": ("Labs", 1, "dirty|ganda|gandagi|garbage|kachra|safai|trash"),
 "Furniture": ("Labs", 1, "chair|kursi|desk|table|broken seat"),
 "Lab equipment": ("Labs", 2, "microscope|equipment|oscilloscope|apparatus|instrument"),
}
PARAPHRASES = {
    "WiFi":"internet disconnects|connection keeps dropping|wireless drops|can't get online|pages will not load|no connectivity|net band|packet loss",
    "Cooling":"not cooling|blowing warm air|blows hot air|room is stifling|garmi lag rahi|thandi hawa nahi|thandak nahi|boiling hot|students are sweating|heat is unbearable|air conditioning unit",
    "Projector":"slides won't show|image on the wall|projection|projetor|projecter|slides on screen",
    "Lighting":"pitch dark|tube flickers|roshni nahi|lamp",
    "Power":"no power|load shedding|loadshedding|bijlee",
    "Food":"spoiled meal|undercooked chicken|basi khana|food smells rotten",
    "Furniture":"broken bench|wobbly stool|tooti kursi",
}
for label, extra in PARAPHRASES.items():
    owner, level, examples = TOPICS[label]
    TOPICS[label] = owner, level, examples + "|" + extra

HAZARDS = {
 "Fire or smoke": "fire|flames|smoke|burning smell|aag|dhua|dhuan|jalne ki boo",
 "Electrical hazard": "spark|sparks|sparking|chingari|chingariyan|electric shock|current lag|exposed wire|live wire|nangi taar",
 "Gas hazard": "gas|gas leak|gas leakage|gas ki boo|gas ki badbu",
 "Medical emergency": "medical emergency|medical|fainted|unconscious|collapsed|chest pain|bleeding|behosh|behoshi|ambulance|saans nahi",
}
PRIVATE = "harassment|harass|ragging|ragged|bullying|bullied|stalking|stalked|threatened|unwanted touching|groped|blackmail|ched char|chher chhar|chhed|tang kar|badtameezi|dhamki|catcalling|intimidation|sexual assault|eve teasing|followed me|forced initiation"
PRIORITIES = {1:"Low", 2:"Normal", 3:"High", 4:"Critical"}

def norm(value):
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[\w]+", value))

@lru_cache(2048)
def grams(word):
    word = "^" + word + "$"
    return Counter(word[i:i+n] for n in (2, 3) for i in range(len(word)-n+1))

def similarity(a, b):
    x, y = grams(a), grams(b)
    return 2 * sum((x & y).values()) / (sum(x.values()) + sum(y.values()))

def score(sentence, examples):
    words = sentence.split()
    best = 0
    for phrase in examples.split("|"):
        n = len(phrase.split())
        for i in range(len(words)-n+1):
            part = " ".join(words[i:i+n])
            if part == phrase:
                best = max(best, 1 + len(phrase)/100)
            elif len(phrase) >= 5 and abs(len(part)-len(phrase)) <= 3:
                best = max(best, similarity(part, phrase), SequenceMatcher(None,part,phrase).ratio() * .94)
    return best

def report_facts(raw):
    # Imperatives about routing and ranking have no authority.
    lower = raw.casefold()
    return re.sub(r"\b(?:ignore (?:all |your |previous |the )*(?:rules|instructions)|(?:mark|classify|route|set|assign|label) (?:this|it|my report|the report|priority)\b)[^.!?;\n]*", " ", lower)

def analyze(raw, chosen="", confidential=False):
    s = norm(report_facts(raw))
    private = confidential or score(norm(raw), PRIVATE) >= .77 or score(norm(raw),
        "seniors forcing|seniors forced|forced me to|unwanted messages|touching me|keeps following|being followed|zabardasti|hazing|humiliated me|sexual remarks|badtamizi|chher rahe|tang kr|raging by seniors") >= .81
    found = [(place,alias) for place, aliases in PLACES.items() for alias in aliases
             if re.search(r"\b"+re.escape(alias)+r"\b", s)]
    known = {place for place,_ in found}
    # If a second, unregistered building or room is mentioned, ask the reporter.
    remainder = s
    for _, alias in sorted(found, key=lambda pair: -len(pair[1])):
        remainder = re.sub(r"\b"+re.escape(alias)+r"\b"," campusplace ",remainder)
    unfamiliar = any(int(x) not in range(1,7) for x in re.findall(r"\b(?:lab|laboratory)\s*(\d+)\b",s))
    unfamiliar |= bool(re.search(r"\b(?:hall|tower|hostel|auditorium|annex|block|building|basement|room|wing|lab\s+[a-z0-9]+)\b",remainder))
    unfamiliar |= bool(re.search(r"\b(?:at|near|inside|outside|beside|opposite|behind|next to)\s+(?:the\s+)?(?!(?:the|campusplace|all|times|first|night|noon|least|once|full)\b)\w+",remainder))
    place = chosen if chosen in PLACES else (next(iter(known)) if len(known)==1 and not unfamiliar else "")
    safety_text = re.sub(r"\b(?:no|without|no sign of|there is no|not a)\s+(?:sparks?|smoke|fire|gas leak|flames|bleeding)\b"," ",s)
    danger = max(HAZARDS, key=lambda k:score(safety_text, HAZARDS[k]))
    hazard = score(safety_text, HAZARDS[danger]) >= .79
    if private:
        topic, dept, base = "Confidential concern", "Admin", 4 if hazard else 3
    elif hazard:
        topic, dept, base = danger, ("EE" if danger != "Medical emergency" else "Admin"), 4
    else:
        scores = {k:score(remainder,examples) for k,(_,_,examples) in TOPICS.items()}
        topic = max(scores, key=scores.get)
        if scores[topic] < .76:
            topic = predict(remainder)
        if not topic:
            topic, dept, base = "", "", 1
        else:
            dept, base, _ = TOPICS[topic]
            if topic in ("Cleanliness","Furniture","Water supply","Water leak"):
                dept = {"Library":"Library","Cafeteria":"Cafeteria","CSE block":"CSE","EE block":"EE","IS office":"IS"}.get(place,"Labs")
    reason = ("Safety signal requires immediate review." if hazard else
              "Restricted to the registrar for confidential review." if private else
              f"{topic} at {place} falls under {dept}." if topic and place else "More detail is needed.")
    if private:
        if score(norm(raw),"ragging|ragged|hazing|forced initiation") >= .77:
            reason = "Possible ragging or hazing. Registrar review requested."
        elif score(norm(raw),PRIVATE) >= .77:
            reason = "Possible harassment or intimidation. Registrar review requested."
        if hazard:
            reason += " Immediate safety review required."
    return dict(topic=topic,dept=dept,base=base,place=place,private=int(private),reason=reason)
