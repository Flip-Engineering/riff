# Deferred cover analysis for Riff

This is a product design note, not an implementation claim. Riff does not add
ComfyUI support or a second local transcription model.

The useful pattern in the linked [YuE2 cover guide](https://docs.comfy.org/tutorials/audio/yue2/yue2)
and the [faithfulness-slider workflow discussion](https://www.reddit.com/r/comfyui/comments/1wjneky/due_to_heavy_request_yue2_covers_now_with_a_how/)
is a two-stage cover operation:

1. inspect a reference recording and turn it into an editable musical brief;
2. let the artist revise that brief before sending ordinary YuE2 inputs to the
   generator.

For Riff, the optional analysis action would send the user-selected audio
directly to `google/gemini-3.8-flash` and ask for a strict, editable result:

- style and production description;
- approximate duration, tempo, meter, key and section boundaries;
- lyric text when the user asks for lyric analysis;
- a melody/harmony/phrase scaffold in Riff's accepted ABC dialect;
- vocal character, instrumental roles and density changes as soft descriptions;
- confidence and uncertainty for every inferred field;
- a compact explanation of what the generated cover can preserve and what may
  change in YuE2's single-performance decoder.

The result should be a normal Riff draft. The user can edit or remove any field,
replace the words, open the score workspace, choose a new style, or switch to
instrumental. The original audio remains an opaque local attachment with a
hash and provenance; it is not silently stored in Git or sent to YuE2.

## Faithfulness as constraints

The external workflow's 0–100 faithfulness idea is valuable as a user-facing
starting point, but it must not pretend that YuE2 has a continuous cover knob.
Riff should translate the value into explicit, inspectable constraints:

- **0**: free reinterpretation; retain only selected character cues;
- **middle**: hold the main melody and broad section timing while allowing
  changes to harmony, arrangement, register or tempo according to the chosen
  controls;
- **100**: hold the supplied score, section order, key/tempo and lyric phrasing
  wherever the chosen representation can prove those invariants.

Advanced controls can release melody, hook, structure, harmony, tempo, key,
register and words independently. The faithfulness value must never silently
change the words. Any requested constraint that cannot be proven from the
returned score should be shown as an unresolved warning before generation.

## Validation and boundaries

Gemini's score is an approximation from audio, so Riff should validate its
syntax and compare duration, bar count, tempo and key fields before offering a
render. It should preserve the raw analysis response, edited score and final
recipe as separate lineage. A failed or incomplete response should leave the
draft untouched. The cover analyzer is the one planned exception to the
ordinary audio-review rule: it may return lyric text and notation when the user
explicitly asks for cover analysis. It uses no Whisper, ASR or hidden secondary
model.

This capability is deferred behind the existing reference-audio producer work
(issue [#16](https://github.com/Flip-Engineering/riff/issues/16)). It is not part
of the current implementation pass.
