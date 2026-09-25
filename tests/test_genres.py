import unittest

from genres import genre_tags


class GenreTagTests(unittest.TestCase):
    def test_tags_follow_the_description_in_order_and_add_broader_families(self):
        self.assertEqual(genre_tags("English, a 1958 doo-wop ballad sung on a street corner."), ["doo-wop", "ballad"])
        self.assertEqual(genre_tags("English, glitchy, maximal hyperpop about one person."), ["hyperpop", "pop", "electronic"])
        self.assertEqual(genre_tags("Spanish, a fiery rumba flamenca."), ["flamenco", "latin"])
        self.assertEqual(genre_tags("English, twinkly Midwest math rock in 5/4."), ["math rock", "rock"])

    def test_references_name_artists_not_genres(self):
        style = "English, a joyful parade. References: Rebirth Brass Band, Gipsy Kings, The Beach Boys."
        self.assertEqual(genre_tags(style), [])
        self.assertEqual(genre_tags("English, a New Orleans second-line parade. References: Rebirth Brass Band."),
                         ["new orleans"])

    def test_similar_words_do_not_cross_match(self):
        self.assertEqual(genre_tags("English, 1991 new jack swing."), ["new jack swing", "r&b"])
        self.assertEqual(genre_tags("Korean, a glossy K-pop song."), ["k-pop", "pop"])
        self.assertNotIn("soul", genre_tags("English, a soulful male voice over a folk guitar."))
        self.assertEqual(genre_tags("English, sugary bubblegum synth pop."), ["synth-pop", "pop", "electronic"])

    def test_empty_or_unknown_descriptions_have_no_tags(self):
        self.assertEqual(genre_tags(""), [])
        self.assertEqual(genre_tags(None), [])
        self.assertEqual(genre_tags("a warm, gentle sound"), [])


if __name__ == "__main__":
    unittest.main()
