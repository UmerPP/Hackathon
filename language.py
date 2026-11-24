"""Small supervised nearest-example model for indirect descriptions.

Fit TF-IDF weights from local labeled utterances. A weak or ambiguous match
abstains so the intake form can ask the student to select a service.
"""
import math
import re
from collections import Counter
from functools import lru_cache

EXAMPLES = {
    "WiFi": [
        "My phone connects but nothing on the internet loads",
        "Every student keeps getting kicked off the wireless network",
        "The connection drops whenever we try to go online",
        "Signal aata hai lekin websites khulti hi nahi",
        "Mobile pe net connect hota hai phir disconnect ho jata hai",
    ],
    "Cooling": [
        "The vents are pushing hot air and the room keeps getting warmer",
        "We are sweating even with the thermostat turned all the way down",
        "Machine chalti hai magar kamra thanda nahi hota",
        "Hawa garam aa rahi hai paseena aa raha hai",
    ],
    "Projector": [
        "The lecturer cannot put her slides up on the wall",
        "The beam on the screen disappears in the middle of presentations",
        "Laptop connected but the lecture slides are invisible",
        "Sir ki presentation parday pe nazar nahi aa rahi",
    ],
    "Lighting": [
        "Half the room is dark and we cannot read our notes",
        "The overhead lamps flicker throughout class",
        "Roshni itni kam hai kuch nazar nahi ata",
    ],
    "Power": [
        "None of the outlets charge our laptops",
        "Everything went off when the circuit tripped",
        "Kisi plug mein bijli nahi aa rahi",
    ],
    "Water supply": [
        "The dispenser is empty and there is nothing to drink",
        "No water comes out when we turn the tap",
        "Peenay ke liye paani nahi mil raha",
    ],
    "Water leak": [
        "A pipe is dripping and there is a puddle on the floor",
        "The ceiling drips onto the desks when it rains",
        "Chhat se pani tapak raha hai farsh geela hai",
    ],
    "Food": [
        "The meal served at lunch smells sour and tastes spoiled",
        "They served us raw chicken again today",
        "Aaj ka khana basi aur badbudar hai",
    ],
    "Furniture": [
        "A leg came off the seat and it tips over",
        "The desk wobbles when I try to write",
        "Baithne wali kursi ki tang toot gayi",
    ],
    "Computer": [
        "The workstation freezes before we can log in",
        "None of the keys respond when I type",
        "Screen on karte hi system hang ho jata hai",
    ],
    "Cleanliness": [
        "Overflowing bins have attracted flies",
        "Someone needs to clear the litter off the floor",
        "Kachray ke dher se badbu aa rahi hai",
    ],
    "Fan": [
        "The ceiling blades have stopped spinning",
        "Pankhay ki pattiyan ghoom nahi rahi",
    ],
    "Books": [
        "I cannot renew a borrowed textbook",
        "The catalogue says this title is on the shelf but it is missing",
    ],
    "Lab equipment": [
        "The oscilloscope trace disappears during experiments",
        "Microscope lens is cracked and we cannot focus it",
    ],
}
STOP = set("a an the and at on it is are we to of for my our in when with from there this that hai hain raha rahi rahe ko ka ki ke pe mein se hi".split())

def features(text):
    words=[w for w in re.findall(r"[a-z0-9]+",text.lower()) if w not in STOP]
    counts=Counter()
    for word in words:
        counts["w:"+word]+=3
        padded="^"+word+"$"
        counts.update("c:"+padded[i:i+3] for i in range(len(padded)-2))
    return counts

def vector(counts,idf):
    weighted={k:math.log1p(n)*idf[k] for k,n in counts.items() if k in idf}
    length=math.sqrt(sum(n*n for n in weighted.values())) or 1
    return {k:v/length for k,v in weighted.items()}

@lru_cache(1)
def fit():
    docs=[(label,features(text)) for label,texts in EXAMPLES.items() for text in texts]
    freq=Counter()
    for _,counts in docs:
        freq.update(counts.keys())
    idf={k:1+math.log((1+len(docs))/(1+n)) for k,n in freq.items()}
    return idf,[(label,vector(counts,idf)) for label,counts in docs]

def predict(text):
    idf,docs=fit()
    query=vector(features(text),idf)
    ranks={}
    for label,doc in docs:
        similarity=sum(v*doc.get(k,0) for k,v in query.items())
        ranks[label]=max(ranks.get(label,0),similarity)
    first,second=sorted(ranks,key=ranks.get,reverse=True)[:2]
    return first if ranks[first]>=.49 and ranks[first]-ranks[second]>=.12 else ""
