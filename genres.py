"""Genre tags derived from a recording's sound description.

The library searches and filters by these tags. They are computed from the style text each time the state is read,
so they need no storage and follow the vocabulary as it grows. Only the description itself is read: the
"References:" list names artists, and an artist's name ("Rebirth Brass Band", "Gipsy Kings") is not a genre.
"""
import re

# (tag, pattern) in the order they are tried; a tag's position in the result follows where it first appears in the text.
VOCABULARY = [
    ("a cappella", r"a\s?cappella"),
    ("barbershop", r"barbershop"),
    ("doo-wop", r"doo[- ]?wop"),
    ("synthwave", r"synthwave"),
    ("city pop", r"city pop"),
    ("bossa nova", r"bossa nova"),
    ("bolero", r"\bbolero\b"),
    ("fado", r"\bfado\b"),
    ("dub", r"\bdub\b"),
    ("reggaeton", r"reggaeton"),
    ("reggae", r"\breggae\b"),
    ("ska", r"\bska\b"),
    ("pop-punk", r"pop[- ]punk"),
    ("punk", r"\bpunk\b"),
    ("grunge", r"\bgrunge\b"),
    ("power metal", r"power metal"),
    ("metal", r"\bmetal\b"),
    ("industrial", r"\bindustrial\b"),
    ("hip hop", r"hip[- ]?hop|\brap\b|\brapper\b|boom[- ]bap"),
    ("new jack swing", r"new jack swing"),
    ("funk", r"\bfunk\b|\bfunky\b"),
    ("italo disco", r"italo disco"),
    ("disco", r"\bdisco\b"),
    ("r&b", r"r&b|rhythm and blues"),
    ("motown", r"\bmotown\b"),
    ("soul", r"\bsoul\b"),
    ("gospel", r"\bgospel\b"),
    ("blues", r"\bblues\b"),
    ("bebop", r"\bbebop\b|\bvocalese\b"),
    ("big band", r"big band"),
    ("swing", r"(?<!jack )\bswing\b"),
    ("jazz", r"\bjazz\b|\bjazzy\b"),
    ("exotica", r"\bexotica\b"),
    ("lounge", r"\blounge\b"),
    ("bluegrass", r"bluegrass"),
    ("country", r"\bcountry\b|countrypolitan"),
    ("sea shanty", r"\bshanty\b"),
    ("celtic", r"\bceltic\b|\birish\b"),
    ("folk", r"\bfolk\b"),
    ("klezmer", r"\bklezmer\b"),
    ("cumbia", r"\bcumbia\b"),
    ("flamenco", r"\bflamenc[oa]\b"),
    ("tango", r"\btango\b"),
    ("afrobeats", r"\bafrobeats?\b|\bhighlife\b"),
    ("calypso", r"\bcalypso\b"),
    ("corrido", r"\bcorrido\b|norte[ñn]o"),
    ("mariachi", r"\bmariachi\b"),
    ("cabaret", r"\bcabaret\b"),
    ("chanson", r"\bchanson\b|\bmusette\b"),
    ("waltz", r"\bwaltz\b"),
    ("chamber pop", r"chamber pop"),
    ("chamber", r"\bchamber\b(?! pop)|string quartet"),
    ("classical", r"\bclassical\b|\borchestral\b"),
    ("choral", r"\bgregorian\b|\bchoral\b|\bchant\b"),
    ("ambient", r"\bambient\b"),
    ("dream pop", r"dream pop"),
    ("shoegaze", r"\bshoegaze\b"),
    ("trip-hop", r"trip[- ]hop"),
    ("hyperpop", r"\bhyperpop\b"),
    ("synth-pop", r"synth[- ]?pop"),
    ("electronic", r"\belectro\b|\belectronic\b|\belectroclash\b"),
    ("k-pop", r"\bk-?pop\b"),
    ("j-pop", r"\bj-?pop\b|idol pop"),
    ("mandopop", r"\bmandopop\b|\bc-pop\b"),
    ("yacht rock", r"yacht rock"),
    ("britpop", r"\bbritpop\b"),
    ("math rock", r"math rock"),
    ("art rock", r"art rock|art[- ]pop"),
    ("garage rock", r"garage rock"),
    ("college rock", r"college rock"),
    ("surf rock", r"\bsurf\b"),
    ("rockabilly", r"\brockabilly\b"),
    ("new orleans", r"new orleans|second[- ]line|brass band"),
    ("showtune", r"\bbroadway\b|\bshowstopper\b|show ?tune|musical theat"),
    ("children's", r"children's|\bkids'|\bnursery\b"),
    ("lullaby", r"\blullaby\b"),
    ("ballad", r"\bballad\b"),
    ("indie", r"\bindie\b"),
    ("rock", r"\brock\b"),
    ("pop", r"(?<![\w-])pop(?![\w-])"),
]
_COMPILED = [(tag, re.compile(pattern)) for tag, pattern in VOCABULARY]

# A specific tag also files the recording under its broader family.
IMPLIES = {
    "power metal": ["metal"], "grunge": ["rock"], "pop-punk": ["punk", "pop"], "bebop": ["jazz"], "big band": ["jazz"], "swing": ["jazz"],
    "italo disco": ["disco"], "new jack swing": ["r&b"], "motown": ["soul"], "dub": ["reggae"], "exotica": ["lounge"],
    "barbershop": ["a cappella"], "synthwave": ["electronic"], "synth-pop": ["pop", "electronic"],
    "hyperpop": ["pop", "electronic"], "trip-hop": ["electronic"], "city pop": ["pop"], "chamber pop": ["pop"],
    "dream pop": ["pop"], "k-pop": ["pop"], "j-pop": ["pop"], "mandopop": ["pop"], "britpop": ["rock", "pop"],
    "math rock": ["rock"], "art rock": ["rock"], "garage rock": ["rock"], "college rock": ["rock"], "surf rock": ["rock"],
    "yacht rock": ["rock"], "rockabilly": ["rock"], "bolero": ["latin"], "bossa nova": ["latin"], "cumbia": ["latin"],
    "tango": ["latin"], "reggaeton": ["latin"], "flamenco": ["latin"], "corrido": ["latin"], "mariachi": ["latin"],
}

_REFERENCES = re.compile(r"\breferences?\s*:", re.IGNORECASE)


def genre_tags(style):
    """Return the genre tags found in a sound description, in the order they first appear, then implied families."""
    text = _REFERENCES.split(style or "", maxsplit=1)[0].lower()
    found = sorted(((match.start(), tag) for tag, pattern in _COMPILED if (match := pattern.search(text))))
    tags = list(dict.fromkeys(tag for _, tag in found))
    for tag in list(tags):
        tags.extend(broader for broader in IMPLIES.get(tag, []) if broader not in tags)
    return tags
