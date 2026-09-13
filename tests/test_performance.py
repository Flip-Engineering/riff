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
    def test_solver_selection_reaches_native_and_producer_recipes(self):
        source = validate_recipe(recipe(solver="ab2", steps=51))
        value = {key: source[key] for key in FIELDS}
        self.assertEqual(recommended_generation(value, source, False)["solver"], "ab2")
        legacy = {key: val for key, val in value.items() if key != "solver"}
        self.assertEqual(recommended_generation(legacy, {}, False)["solver"], "midpoint")
        command = run.build_command(lyrics="", style="", max_seconds=8, steps=51, cot="off",
                                    seed=1, threads=2, output=self.root / "new.wav", solver="ab2")
        self.assertIn("ode_method=ab2", command)
        self.assertIn("num_inference_steps=51", command)
        with self.assertRaises(ValueError):
            validate_recipe(recipe(solver="unknown"))
        self.assertEqual(describe()["recipe_schema"]["properties"]["solver"]["enum"], ["midpoint", "ab2"])

    def test_older_engine_cannot_silently_ignore_multistep(self):
        import subprocess
        with patch("run.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "num_inference_steps", "")) as probe:
            run.require_solver("midpoint", "engine")
            probe.assert_not_called()
            with self.assertRaisesRegex(ValueError, "Update the music engine"):
                run.require_solver("ab2", "engine")
            probe.return_value = subprocess.CompletedProcess([], 0, "ode_method <midpoint|ab2>", "")
            run.require_solver("ab2", "engine")

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

    def test_performance_only_job_can_be_finished_without_a_recording(self):
        import sys
        def command(value, output):
            code = "from pathlib import Path;import json,sys,shutil;p=Path(sys.argv[1]);c=p.with_suffix('.codes.i32');c.write_bytes(bytes([7,0,0,0])*250);c.with_suffix('.i32.json').write_text(json.dumps(dict(frames=250,truncated=True)))"
            if value['render_mode'] == 'music':
                code += ";shutil.copyfile(sys.argv[2],sys.argv[1])"
            return [sys.executable, '-c', code, str(output), str(self.audio)]
        generator = Generator(self.store, command)
        try:
            job = generator.submit(recipe(render_mode='performance'))
            wait_until(lambda: self.store.job(job['id'])['status'] == 'performed')
            self.assertEqual(self.store.snapshot()['tracks'], [])
            self.assertTrue(self.store.snapshot()['jobs'][0]['performance_available'])
            before = self.store.performance_path(job['id']).read_bytes()
            value = self.store.job(job['id'])['recipe']
            self.assertIn('semantic_only=true', generator.native_command(value, self.root/'out.wav'))
            rendered = generator.submit({**value, 'render_mode': 'music', 'performance_source': job['id'], 'steps': 51})
            wait_until(lambda: self.store.job(rendered['id'])['status'] == 'done')
            self.assertEqual(self.store.performance_path(rendered['id']).read_bytes(), before)
            self.assertEqual(self.store.track(rendered['id'])['recipe']['performance_source'], job['id'])
            self.assertTrue(self.store.track(rendered['id'])['recipe']['performance']['truncated'])
            self.assertEqual(self.store.job(job['id'])['status'], 'performed')
        finally:
            generator.close()

    def test_restart_recovers_complete_stage_and_preserves_partial_files(self):
        import time
        values = validate_recipe(recipe(cot='full', abc='X:1\nK:Dm\nD4|'))
        directory = self.store.outputs/'riff'; directory.mkdir()
        for index, shape in enumerate(('complete', 'partial', 'outside', 'missing-metadata')):
            job_id = f'{index:032x}'
            codes = directory/(job_id+'.codes.i32')
            codes.write_bytes(bytes([1,0,0,0])*(249 if shape=='partial' else 250))
            if shape != 'missing-metadata':
                codes.with_suffix('.i32.json').write_text(json.dumps({'frames':250,'truncated':False}))
            if shape == 'outside':
                target=self.root/'outside.i32'; codes.rename(target); codes.symlink_to(target)
            with self.store.db() as db:
                db.execute('INSERT INTO jobs(id,title,created,status,recipe) VALUES(?,?,?,?,?)',
                           (job_id,shape,time.time(),'running',json.dumps(values)))
        self.store.recover_jobs()
        jobs = self.store.snapshot()['jobs']
        self.assertEqual([job['status'] for job in jobs], ['interrupted']*4)
        self.assertEqual([job['performance_available'] for job in jobs], [True,False,False,False])
        result = self.store.prepare_performance(validate_recipe({**values,'performance_source':jobs[0]['id']}))
        self.assertEqual(result['max_seconds'],10)
        self.assertEqual(result['abc'],values['abc'])
        self.assertTrue((directory/(jobs[1]['id']+'.codes.i32')).exists())
        (directory/(jobs[0]['id']+'.codes.i32')).write_bytes(bytes([2,0,0,0])*250)
        with self.assertRaisesRegex(ValueError,'changed'):
            self.store.prepare_performance(result)
        self.assertEqual(self.store.snapshot()['tracks'],[])
