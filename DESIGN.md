# Riff studio design

Riff treats writing, composition, and listening as parts of one working session.
The main desk presents words and direction beside the selected take. Most sessions
can begin with Free play and Generate. Additional controls open where they become
useful, without turning a blank field into a required decision.

The original exploration used the actual randomized fragments saved in
`design-seed.txt`. Chorus, relay, reed, and riff suggested repetition with variation;
glass and foil suggested spare linework. Those associations survive in the seeded
cover drawings, while the name and palette now belong to Flip Engineering.

## Visual language

Use Flip-Default's dark wine surfaces, warm type, coral primary accent and blue
secondary accent, with its corresponding warm light theme. Georgia supplies the
musical titles; system sans-serif type keeps controls quiet and familiar. The Flip
character appears only as a tiny detail over the i in the Riff wordmark, with a
related app icon. See [branding](docs/branding.md) for exact tokens and provenance.

The large artwork is deliberately generative decoration. Waveforms and spectrum
views use the recording's actual audio data. Animation rests when playback stops,
the view is hidden, or reduced motion is requested. Descriptive titles and ample
writing space carry more visual weight than engine statistics.

## Three depths of control

1. **A musical intention:** optional lyrics and free-form direction, idea writing,
   holds, undo, duration and quality choices.
2. **A composition:** an optional score workspace with tempo, meter, key, voices,
   transposition, note selection and tone audition. Plain-language score revision
   makes harmony and arrangement changes accessible without requiring ABC knowledge.
3. **A precise experiment:** source notation, planning/performance sampling,
   guidance, token budgets, seeds and custom solver steps. Expose real runtime
   choices and validate their actual domains rather than inventing creative caps.

A short study uses selected lines without replacing the full draft. A recording
retains its recipe and captured symbolic plan. Variations link back to their source;
producer notes link to moments in the audio. Each proposed AI score is previewed
before application, and editing has undo. Score generation is a real native YuE2
planning pass; notation is not reconstructed from a finished recording.

## Interaction and copy

Use musical nouns and concrete verbs: words, sound, take, score, listen, transpose,
apply, keep. Explain network sharing beside an action that sends material, and
credential storage beside the key field. Put installation paths and hardware
settings in Studio settings. Avoid location slogans and implementation narration
in the creative flow.

The interface adapts to narrow windows, gives dialogs explicit accessible names,
keeps keyboard focus visible, and preserves drafts during generation and setup.
Asynchronous refreshes must not overwrite a control someone has just changed.
Notation hit targets include space around small glyphs so notes are practical to
select, rather than only visually rendered.
