"""Small, offline musical idea generator. Original phrase banks, no extra model."""
import math
import random
import secrets


THEMES = {
    "water": {
        "name": "Open water", "titles": ["Tidepool", "A smaller ocean", "Salt on the windows", "The other shore"],
        "moods": ["the first light over a quiet harbor", "a long walk beside an unfamiliar sea", "sunlight moving underwater"],
        "verses": [
            ("Salt on the window, a map on the floor.", "I stopped asking the tide what it's waiting for."),
            ("The boats leave their names in the blue of the bay.", "I loosen my hands and the knots float away."),
            ("Your coat on the seawall, my shoes in the sand.", "A whole little ocean fits into my hand."),
            ("The ferry is drawing a line through the rain.", "I watch it dissolve, then begin it again."),
        ],
        "hooks": [
            ("Let the water find the way.", "We don't have to name today."),
            ("A little less weight, a little more blue.", "There's room in the current for something new."),
            ("Where the river opens wide,", "Leave a little room inside."),
        ],
    },
    "city": {
        "name": "After hours", "titles": ["Last tram home", "Windows still awake", "Neon receipt", "The city exhales"],
        "moods": ["a sleeping city seen from the last tram", "rain on neon outside an empty diner", "a rooftop just before sunrise"],
        "verses": [
            ("The last tram is humming a tune through the street.", "A paper cup rolls to the time of my feet."),
            ("There's light in a window where nobody stays.", "I count all the floors like unfinished days."),
            ("Your name on a receipt in the back of my coat.", "The rain turns the ink to a half-written note."),
            ("The shutters come down but the avenue glows.", "I take the long way where the night-blooming grows."),
        ],
        "hooks": [
            ("Stay a little past the blue.", "There's a quieter way through."),
            ("All these windows, all this light.", "Somewhere we belong tonight."),
            ("Let the city breathe us in.", "We can find our feet again."),
        ],
    },
    "elsewhere": {
        "name": "Elsewhere", "titles": ["Postcard with no address", "A turn not taken", "Passenger light", "Maps made of air"],
        "moods": ["a hopeful departure with no fixed destination", "a train crossing wide summer fields", "finding a street you have never seen"],
        "verses": [
            ("A ticket folded small in the palm of my hand.", "The platform is a shore and the rails leave the land."),
            ("We packed only questions and two pairs of shoes.", "The morning had nothing it wanted to lose."),
            ("The fields turn to gold as the afternoon leans.", "I live for a while in the space in between."),
            ("A road with no number, a sky with no seams.", "The world is much larger than yesterday's dreams."),
        ],
        "hooks": [
            ("Take a turn we haven't tried.", "Let the unknown sit beside."),
            ("We can go where the light goes.", "We can learn what the night knows."),
            ("No address and nothing due.", "Just a little farther into something new."),
        ],
    },
    "wonder": {
        "name": "Small wonders", "titles": ["A roomful of sun", "Paper satellites", "The ordinary shines", "Pocket-sized sky"],
        "moods": ["the delight of finding something small and beautiful", "a kitchen dancing in afternoon light", "a paper kite above a quiet neighborhood"],
        "verses": [
            ("The spoon in your coffee is ringing a bell.", "There's something in nothing that words never tell."),
            ("A kite made of paper is pulling the sky.", "We look at each other and don't wonder why."),
            ("The plant on the windowsill turned toward the day.", "I think there is something in growing that way."),
            ("A dust-mote parade in a roomful of sun.", "I thought I was late, but the day just begun."),
        ],
        "hooks": [
            ("Hold this little piece of bright.", "Let it be enough tonight."),
            ("Nothing grand and nothing planned.", "Just the whole world in your hand."),
            ("Small things, wide sky.", "Let the ordinary shine."),
        ],
    },
    "weather": {
        "name": "Weather systems", "titles": ["Pressure falling", "After the rain", "Cloud atlas at home", "Thunder in the distance"],
        "moods": ["relief after a summer storm", "low clouds breaking into shafts of light", "the charged stillness before rain"],
        "verses": [
            ("The curtains lift gently; the pressure comes down.", "A rumor of thunder walks into the town."),
            ("The rain on the roof has a language its own.", "It says you can rest without being alone."),
            ("The pavement is holding a piece of the sky.", "I step through a cloud as the traffic goes by."),
            ("We left all the windows a little apart.", "The weather came in and rearranged the heart."),
        ],
        "hooks": [
            ("Let it come and let it clear.", "There's a different season here."),
            ("Every cloud can change its mind.", "Leave the heavy air behind."),
            ("Wait until the rain lets go.", "There are things that only new leaves know."),
        ],
    },
    "return": {
        "name": "Slow return", "titles": ["The porch light", "Room at the table", "A familiar key", "Home in a different way"],
        "moods": ["coming home after a long absence", "a quiet reunion at a kitchen table", "learning to feel at home again"],
        "verses": [
            ("The key takes a moment to turn in the door.", "The light falls the way that it always did before."),
            ("Your chair by the table, a book on its face.", "I brought back a silence that needed a place."),
            ("The road in the mirror grows smaller and small.", "I don't need an answer to answer your call."),
            ("There's soup on the stove and a coat on a chair.", "I set down the years that I carried out there."),
        ],
        "hooks": [
            ("Leave a little light for me.", "I am where I meant to be."),
            ("There's a place at the table still.", "Let the quiet have its fill."),
            ("All the roads can fall away.", "Let me be at home today."),
        ],
    },
}


def seed_number(value=None):
    if value in (None, ""):
        return secrets.randbits(32)
    if type(value) not in (int, str):
        raise ValueError("Seed must be a whole number.")
    try:
        value = int(value)
    except ValueError:
        raise ValueError("Seed must be a whole number.")
    if not 0 <= value < 2 ** 63:
        raise ValueError("Seed must be from 0 through 9223372036854775807.")
    return value


def position(value, fallback):
    if value is None:
        return fallback
    if isinstance(value, bool):
        raise ValueError("Compass values must be numbers between 0 and 1.")
    try:
        number = float(value)
    except (ValueError, TypeError):
        raise ValueError("Compass values must be numbers between 0 and 1.")
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise ValueError("Compass values must be numbers between 0 and 1.")
    return number


def inspire(payload):
    if not isinstance(payload, dict):
        raise ValueError("Send an idea object.")
    seed = seed_number(payload.get("seed"))
    rng = random.Random(seed)
    mode = payload.get("mode", "surprise")
    if mode not in ("free", "instrumental", "lyrics", "surprise"):
        raise ValueError("Choose free play, instrumental, my lyrics, or surprise song.")
    theme = payload.get("theme", "anywhere")
    if not isinstance(theme, str) or theme not in (*THEMES, "anywhere"):
        raise ValueError("Choose a story from the idea menu.")
    theme = rng.choice(list(THEMES)) if theme == "anywhere" else theme
    world = THEMES[theme]
    energy = position(payload.get("energy"), rng.random())
    texture = position(payload.get("texture"), rng.random())
    title = rng.choice(world["titles"])
    verse = [line for pair in rng.sample(world["verses"], 2) for line in pair]
    refrain = [line for pair in rng.sample(world["hooks"], 2) for line in pair]
    # Stanza breaks are enough. Users never need to write section markers.
    lyrics = "\n".join(verse) + "\n\n" + "\n".join(refrain)
    if texture < .34:
        genre = rng.choice(["chamber folk", "acoustic jazz", "intimate indie folk"])
        instruments = rng.choice(["felt piano and upright bass", "fingerpicked guitar and mellow clarinet", "nylon-string guitar and a small string ensemble"])
    elif texture < .67:
        genre = rng.choice(["chamber soul", "jazz funk", "dream pop"])
        instruments = rng.choice(["Rhodes piano and muted trumpet", "shimmering guitar and round electric bass", "electric piano and warm analog strings"])
    else:
        genre = rng.choice(["ambient electronica", "luminous synth pop", "melodic downtempo"])
        instruments = rng.choice(["soft analog arpeggios and rounded sub bass", "glassy synthesizers and warm tape textures", "pulsing synth chords and distant electric piano"])
    rhythm = "spacious unhurried phrasing, sparse brushed percussion" if energy < .34 else (
        "a relaxed steady groove, light syncopation" if energy < .67 else "a propulsive rhythm, lively syncopated drums")
    tempo = round(65 + energy * 80)
    style = f"English, {genre}, {instruments}, {rhythm}, around {tempo} BPM, {rng.choice(world['moods'])}"
    style = f"Instrumental, {style}, no vocals" if mode == "instrumental" else style + ", clear expressive lead vocal"
    return {"title": title, "lyrics": "" if mode in ("free", "instrumental") else lyrics,
            "style": style, "mode": mode, "seed": str(seed), "theme": theme,
            "theme_name": world["name"], "energy": round(energy, 3), "texture": round(texture, 3),
            "lyrics_source": "none" if mode in ("free", "instrumental") else "generated"}
