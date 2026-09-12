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
character appears only as a tiny detail at the base of the r in the Riff wordmark, with a
related app icon. See [branding](docs/branding.md) for exact tokens and provenance.

The large artwork begins with a deterministic shape from the recording's seed.
Live sound folds those same contours into a sculptural surface with depth, lighting,
and fine ridges. A signed, filtered PCM trace runs through the open contours,
connecting the sculpture to recognizable waveform lines. Seed palettes use mineral
hues: slate, violet, sage and muted clay. Surface and movement controls range from
open lines to solid material, and from stillness to expressive motion; an export
captures these choices at its start. Bass, middle and high frequencies shape its body; transient attacks
travel through the folds, while stereo balance and width shift its perspective.
The only mark inside the artwork is riff in the bottom-left corner.
An immersive view keeps transport controls outside the image. MP4 exports use the same renderer and audio clock, with the original audio
muxed separately. Animation rests when playback stops, the view is hidden, or
reduced motion is requested; deliberately exported videos still animate. Descriptive titles and ample
writing space carry more visual weight than engine statistics.

## Three depths of control

1. **A musical intention:** optional lyrics and free-form direction, idea writing,
   holds, undo, duration and quality choices.
2. **A composition:** an optional score workspace with tempo, meter, key, voices,
   transposition, note selection and tone audition. Voice maps reveal phrase density
   and register; voices can be isolated in the preview. Written duration can set the
   next take's duration. Plain-language score revision
   makes harmony and arrangement changes accessible without requiring ABC knowledge.
3. **A precise experiment:** source notation, planning/performance sampling,
   guidance, token budgets, seeds and custom solver steps. Expose real runtime
   choices and validate their actual domains rather than inventing creative caps.

A short study uses selected lines without replacing the full draft. A/B comparison
keeps the listening position and play state while changing takes; a passage can
loop across both performances. Input differences remain alongside the audition,
and finishing a new take does not replace a comparison in progress. Waiting takes
can move in the queue with keyboard controls, without altering the active take or
creation timestamps. A recording
retains its recipe, captured symbolic plan and native performance codes. Refine this
performance revisits acoustic rendering while retaining phrasing; a fresh variation
opens the composition again. Variations link back to their source;
producer notes link to moments in the audio. Each proposed AI score is previewed
before application, and editing has undo. Score generation is a real native YuE2
planning pass; notation is not reconstructed from a finished recording.

Producer review sends the prior generation controls, supplied and generated score,
and audio to the selected multimodal model. Its structured recommendation uses the
same generation contract as the studio. A recommended take can run directly without
overwriting the open draft; editing it in the studio preserves undo and ancestry.
Listening observations remain available beside the proposed performance.

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
