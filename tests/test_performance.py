import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import run
from capabilities import describe
from review_recipe import recommended_generation, response_schema, FIELDS
from studio_core import Generator, validate_recipe
from test_studio import StudioFixture, fixture_audio, recipe, wait_until


class PerformanceTests(StudioFixture):
    def saved_performance(self):
        codes = self.audio.with_suffix(".codes.i32")
        codes.write_bytes(bytes([1, 0, 0, 0]) * 250)
        source = validate_recipe(recipe(cot="full", abc=""))
        source["symbolic_plan"] = {"abc": "X:1\nK:Dm\nD4|"}
        source["performance"] = {"frames": 250, "sha256": hashlib.sha256(codes.read_bytes()).hexdigest(), "truncated": False}
        return self.store.add_track(self.audio, source, {}), source, codes

    def test_reuse_resolves_owned_codes_and_actual_score_without_sampling_again(self):
        track_id, source, codes = self.saved_performance()
        value = self.store.prepare_performance(validate_recipe({**source, "performance_source": track_id,
                                                                "steps": 37, "style": "A muted reed", "max_seconds": 20}))
        self.assertEqual(value["abc"], source["symbolic_plan"]["abc"])
        self.assertEqual(value["max_seconds"], 10)
        self.assertEqual((value["steps"], value["style"]), (37, "A muted reed"))
        generator = Generator(self.store)
        try:
            command = generator.native_command(value, self.root / "result.wav")
            self.assertIn("semantic_codes_file=" + str(codes), command)
            self.assertIn("semantic_codes_out=" + str(self.root / "result.codes.i32"), command)
            self.assertIn("abc=" + source["symbolic_plan"]["abc"], command)
        finally:
            generator.close()

    def test_invalid_or_changed_artifacts_cannot_enter_the_queue(self):
        track_id, source, codes = self.saved_performance()
        generator = Generator(self.store)
        try:
            for value in ("../../secret", "a" * 32):
                with self.subTest(value=value), self.assertRaises((ValueError, KeyError)):
                    generator.submit({**source, "performance_source": value})
            codes.write_bytes(bytes([2, 0, 0, 0]) * 250)
            with self.assertRaisesRegex(ValueError, "changed"):
                generator.submit({**source, "performance_source": track_id})
            outside = self.root / "outside.i32"
            codes.rename(outside)
            codes.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "outside"):
                generator.submit({**source, "performance_source": track_id})
            self.assertEqual(self.store.snapshot()["jobs"], [])
        finally:
            generator.close()

    def test_worker_captures_codes_and_reuse_keeps_lineage(self):
        import sys
        def command(value, output):
            return [sys.executable, "-c",
                    "from pathlib import Path;import shutil,sys;shutil.copyfile(sys.argv[1],sys.argv[2]);p=Path(sys.argv[2]).with_suffix('.codes.i32');p.write_bytes(bytes([7,0,0,0])*10);p.with_suffix('.i32.json').write_text('{\"frames\":10,\"truncated\":false}')",
                    str(self.audio), str(output)]
        generator = Generator(self.store, command)
        try:
            job = generator.submit(recipe())
            wait_until(lambda: any(j["id"] == job["id"] and j["status"] == "done" for j in self.store.snapshot()["jobs"]))
            track = self.store.track(job["id"])
            self.assertEqual(track["recipe"]["performance"]["frames"], 10)
            self.assertTrue(self.store.snapshot()["tracks"][0]["performance_available"])
            self.assertTrue(self.store.performance_path(job["id"]).is_file())
            reused = generator.submit({**track["recipe"], "performance_source": job["id"], "parent_track_id": job["id"]})
            self.assertEqual(reused["recipe"]["max_seconds"], .4)
            self.assertEqual(reused["recipe"]["parent_track_id"], job["id"])
        finally:
            generator.close()

    def test_producer_can_reuse_supplied_performance_or_compose_through_same_contract(self):
        track_id, source, _ = self.saved_performance()
        source["performance_track_id"] = track_id
        take = {key: validate_recipe(source)[key] for key in FIELDS}
        take.update(performance_source=track_id, steps=48)
        result = recommended_generation(take, source, True)
        self.assertEqual(result["performance_source"], track_id)
        schema = response_schema(source, True)["properties"]["generation"]
        self.assertEqual(schema["properties"]["performance_source"]["enum"], ["", track_id])
        with self.assertRaisesRegex(ValueError, "not supplied"):
            recommended_generation({**take, "performance_source": "a" * 32}, source, True)
        with self.assertRaises(ValueError):
            recommended_generation({**take, "render_mode": "plan"}, source, True)
        plan = recommended_generation({**take, "performance_source": "", "render_mode": "plan"}, source, True)
        self.assertEqual(plan["render_mode"], "plan")
        self.assertEqual(describe()["operations"]["generate"]["path"], "/api/generations")
