import json
import shutil
import unittest

from test_studio import StudioFixture, recipe


class LineageTests(StudioFixture):
    def take(self, name, **changes):
        target = self.store.outputs / (name + ".wav")
        shutil.copyfile(self.audio, target)
        return self.store.add_track(target, recipe(title=name, **changes), {})

    def test_connected_branches_include_archived_and_exclude_similar_unrelated_takes(self):
        root = self.take("Origin")
        a = self.take("Longer", parent_track_id=root, max_seconds=60)
        b = self.take("Alternate", parent_track_id=root, seed="44")
        c = self.take("Finish", parent_track_id=a, performance_source=a, decoder={"halo_frames": 0})
        unrelated = self.take("Same seed")
        self.store.update_track(b, {"archived": True})
        with self.store.db() as db:
            before = [tuple(row) for row in db.execute("SELECT id,recipe FROM tracks ORDER BY id")]
        graph = self.store.lineage(c)
        self.assertEqual({node["id"] for node in graph["nodes"]}, {root, a, b, c})
        self.assertNotIn(unrelated, graph["roots"])
        self.assertEqual(graph["roots"], [root])
        self.assertFalse(graph["has_cycle"])
        self.assertTrue(next(node for node in graph["nodes"] if node["id"] == b)["archived"])
        edge = next(edge for edge in graph["edges"] if edge["child"] == a)
        self.assertEqual(edge["changes"], [{"field": "max_seconds", "label": "Length", "before": 10,
                                           "after": 60, "before_recorded": True, "after_recorded": True}])
        change = next(edge for edge in graph["edges"] if edge["child"] == c)["changes"]
        decoder = next(item for item in change if item["field"] == "decoder")
        self.assertFalse(decoder["before_recorded"])
        self.assertEqual(decoder["after"], {"halo_frames": 0})
        with self.store.db() as db:
            self.assertEqual(before, [tuple(row) for row in db.execute("SELECT id,recipe FROM tracks ORDER BY id")])

    def test_missing_source_connects_siblings_without_inventing_recipe(self):
        missing = "f" * 32
        a = self.take("A", parent_track_id=missing)
        b = self.take("B", parent_track_id=missing)
        graph = self.store.lineage(a)
        self.assertEqual({node["id"] for node in graph["nodes"]}, {a, b, missing})
        self.assertTrue(next(node for node in graph["nodes"] if node["id"] == missing)["missing"])
        self.assertTrue(all(edge["changes"] is None for edge in graph["edges"]))

    def test_cycles_are_bounded_and_reported_without_repair(self):
        a = self.take("A")
        b = self.take("B", parent_track_id=a)
        saved = self.store.track(a)["recipe"]
        saved["parent_track_id"] = b
        with self.store.db() as db:
            db.execute("UPDATE tracks SET recipe=? WHERE id=?", (json.dumps(saved), a))
        graph = self.store.lineage(a)
        self.assertTrue(graph["has_cycle"])
        self.assertEqual(len(graph["nodes"]), 2)
        self.assertEqual(self.store.track(a)["recipe"], saved)

    def test_unknown_track_and_unlinked_take(self):
        with self.assertRaises(KeyError): self.store.lineage("0" * 32)
        a = self.take("A")
        self.assertEqual(self.store.lineage(a)["edges"], [])


if __name__ == "__main__": unittest.main()
