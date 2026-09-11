# Riff and Flip-Default

Riff uses the Flip-Default root palette from Flip Engineering's server design
tokens and world palettes, revision `e2b988a1380797c3449b894131e31134c107de64`.
The implementation is in `web/suite.css`, with a persisted light/dark choice.

| Role | Dark | Light |
|---|---|---|
| Background | #1C1216 | #F5EEE6 |
| Surface | #281A1F | #FDF9F3 |
| Raised surface | #382630 | #ECE0D1 |
| Primary accent | #EE7766 | #1C5798 |
| Secondary accent | #5B8FC9 | #EE7766 |
| Primary text | #F4F1EC | #2B1A1D |
| Secondary text | #D5C3C8 | #5E4A4E |
| Muted text | #9A8088 | #8A767B |

`web/flip-face.svg` uses the canonical Flip avatar part geometry with the Flip
seed (`phash2("Flip") = 122206397`), colored for this palette. It appears at
10–12 pixels as the dot of the i in Riff. The app icon includes a small related
face detail. Flip Engineering attribution stays in the footer and About dialog.
There are no mascot panels competing with the music.
