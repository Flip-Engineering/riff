"""Warm lease lifecycle through the real bridge and fake engine, without models."""
import json
from pathlib import Path
import sys
import threading
from unittest.mock import patch

import platform_support
import run
from model_admission import ModelAdmission
from studio_core import Generator, Store, validate_recipe
from fixture_waits import until
from test_resident_admission import PORT, Observation
from test_warm_engine import WarmFixture


class ResidentWarmTests(WarmFixture):
    def setUp(self):
        super().setUp()
        # The fake also accepts a cold CLI invocation, so fallback tests use a
        # distinct real child and produce the same WAV without job-mode markers.
        cold = '''if "--jobs" not in sys.argv:
    (state / ("cold-%d" % os.getpid())).touch()
    out=sys.argv[sys.argv.index("--out")+1]
    with wave.open(out,"wb") as audio:
        audio.setparams((2,2,48000,0,"NONE","not compressed"))
        audio.writeframes(array.array("h",[1000,-1000]*4800).tobytes())
    sys.exit(0)
'''
        self.binary.write_text(self.binary.read_text().replace('print("fake engine startup", flush=True)', cold+'print("fake engine startup", flush=True)'))
        self.settings = {"warm_engine": True, "warm_engine_release_when_empty": False,
                         "backend": "cpu", "device": 0, "threads": 1, "binary": str(self.binary),
                         "model_root": str(self.models), "model_file": "m.gguf", "vae_file": "v.gguf"}
        config = patch('platform_support.settings', side_effect=lambda: dict(self.settings))
        config.start()
        self.addCleanup(config.stop)
        capabilities = patch.object(run, 'engine_capabilities', return_value={
            'warm_engine': True, 'exact_score_replay': False, 'acoustic_checkpoint': False, 'lora': False})
        capabilities.start()
        self.addCleanup(capabilities.stop)
        inputs = patch('model_admission.resource_inputs', return_value={})
        inputs.start()
        self.addCleanup(inputs.stop)
        self.store = Store(self.root/'data', self.root/'outputs')
        self.control = ModelAdmission(self.store.data_root, observer=Observation(), command=[sys.executable,'-u','-c',PORT])
        self.generator = Generator(self.store, lambda recipe,output:self.command(recipe['style'],output), admission=self.control)
        self.addCleanup(self.generator.close)

    def status(self, identity):
        with self.store.db() as db:
            return db.execute('SELECT status FROM jobs WHERE id=?',(identity,)).fetchone()[0]

    def submit(self, style='ok'):
        return self.generator.submit({'title':'Lease','lyrics':'line','style':style,'max_seconds':10,
                                     'steps':8,'seed':'42','cot':'off','abc':''})['id']

    def done(self, identity, status='done'):
        until(lambda:self.status(identity)==status, self.generator.thread)

    def metrics(self, identity):
        return json.loads((self.store.outputs/'riff'/f'{identity}.metrics.json').read_text())

    def test_default_stays_resident_and_the_second_take_names_the_measured_lease(self):
        first=self.submit();self.done(first)
        lease=self.generator.warm_lease
        self.assertIsNotNone(lease)
        self.assertGreater(lease['usage']['host'],0)
        second=self.submit();self.done(second)
        self.assertEqual(len(self.starts()),1)
        self.assertFalse(self.metrics(first)['engine_reused'])
        self.assertTrue(self.metrics(second)['engine_reused'])
        messages=self.control._exchange({'op':'inspect'})['messages']
        warm_requests=[m for m in messages if m['op']=='request' and m['id']=='native:'+second]
        self.assertEqual(warm_requests[-1]['resident'],lease['id'])
        self.assertTrue(self.generator.warm.running())

    def test_disabled_warm_mode_does_not_preview_the_command_before_artifact_setup(self):
        self.settings['warm_engine']=False
        builder=self.generator.command_builder
        calls=[]
        def command(recipe,output):
            calls.append(recipe)
            return builder(recipe,output)
        self.generator.command_builder=command
        first=self.submit();self.done(first)
        self.assertEqual(len(calls),1)
        self.assertEqual(self.metrics(first)['engine'],'process')

    def test_sound_only_command_has_its_output_path_before_the_launch_receipt_exists(self):
        recipe=validate_recipe({'title':'Sound','lyrics':'line','style':'ok','max_seconds':10,
                                'steps':8,'seed':'42','cot':'off','abc':'','render_mode':'sound'})
        output=self.root/'sound.wav'
        with patch.object(run,'engine_capabilities',return_value={'acoustic_checkpoint':True}):
            command=self.generator.native_command(recipe,output)
        options=[command[i+1] for i,arg in enumerate(command) if arg=='--request-option']
        self.assertIn('acoustic_latents_out='+str(output.with_suffix('.yac')),options)
        self.assertIn('acoustic_only=true',options)
        self.assertNotIn('acoustic_launch_sha256',recipe)

    def test_fit_failure_stops_the_old_lease_and_runs_a_cold_take_with_compared_figures(self):
        first=self.submit();self.done(first)
        figures={'host_available':100,'reserve_bytes':20,'largest_waiting_host':200,
                 'host_pressure':'normal','device_available':{}}
        (self.state/'fit.json').write_text(json.dumps({'state':'resident_fit','keep':False,
            'reason':'Resident engine does not fit alongside waiting work','figures':figures}))
        second=self.submit();self.done(second)
        metrics=self.metrics(second)
        self.assertEqual(metrics['engine'],'process')
        self.assertEqual(metrics['warm_fit'],figures)
        self.assertIn('does not fit',metrics['warm_reason'])
        self.assertFalse(self.generator.warm.running())
        self.assertFalse(self.control.residents)
        self.assertEqual(len(list(self.state.glob('cold-*'))),1)
        self.assertEqual((self.store.outputs/'riff'/f'{first}.wav').read_bytes(),
                         (self.store.outputs/'riff'/f'{second}.wav').read_bytes())

    def test_cancel_of_a_running_take_ends_the_lease_and_next_take_starts_cold(self):
        first=self.submit();self.done(first)
        active=self.submit('wait')
        until(lambda:(self.state/'waiting').exists(),self.generator.thread)
        self.assertEqual(self.generator.cancel(active),'cancelling')
        self.done(active,'cancelled')
        self.assertFalse(self.control.residents)
        following=self.submit();self.done(following)
        self.assertFalse(self.metrics(following)['engine_reused'])
        self.assertEqual(len(self.starts()),2)

    def test_cancelling_a_queued_take_keeps_the_idle_lease_for_the_following_take(self):
        first=self.submit();self.done(first)
        lease=self.generator.warm_lease['id']
        with self.generator.lock:
            queued=self.submit()
            self.assertEqual(self.generator.cancel(queued),'cancelled')
            self.assertTrue(self.generator.warm.running())
            self.assertIn(lease,self.control.residents)
        following=self.submit();self.done(following)
        self.assertTrue(self.metrics(following)['engine_reused'])
        self.assertEqual(len(self.starts()),1)

    def test_queue_empty_release_is_opt_in_and_waits_for_other_model_work(self):
        first=self.submit();self.done(first)
        self.control.acquire('writer',{'host_peak':100},threading.Event())
        self.settings['warm_engine_release_when_empty']=True
        self.generator.wake.set()
        # Call the same idle hook under the admission boundary to prove that a
        # writer reservation outside the generator queue prevents retirement.
        self.generator._release_idle_warm()
        self.assertTrue(self.generator.warm.running())
        self.control.release('writer')
        until(lambda:not self.generator.warm.running(),self.generator.thread)
        self.assertFalse(self.control.residents)
        following=self.submit();self.done(following)
        until(lambda:not self.generator.warm.running(),self.generator.thread)
        self.assertEqual(len(self.starts()),2)

    def test_scheduler_busy_reply_prevents_release_even_with_no_local_entries(self):
        (self.state/'scheduler-busy').touch()
        self.settings['warm_engine_release_when_empty']=True
        first=self.submit();self.done(first)
        self.generator._release_idle_warm()
        self.assertTrue(self.generator.warm.running())
        (self.state/'scheduler-busy').unlink()
        self.generator.wake.set()
        until(lambda:not self.generator.warm.running(),self.generator.thread)

    def test_missing_process_measurements_keep_legacy_reuse_without_credit(self):
        self.control.monitor.read=lambda pid:(0,0)
        first=self.submit();self.done(first)
        second=self.submit();self.done(second)
        self.assertEqual(len(self.starts()),1)
        self.assertFalse(self.control.residents)
        self.assertIsNone(self.generator.warm_lease)
        self.assertIn('unavailable',self.metrics(second)['warm_reason'])
        requests=[m for m in self.control._exchange({'op':'inspect'})['messages'] if m['op']=='request']
        self.assertTrue(all('resident' not in m for m in requests))

    def test_older_scheduler_keeps_manual_reuse_and_receives_no_resident_operations(self):
        self.control.command=[sys.executable,'-u','-c',PORT.replace(",'resident':'riff.resident.v1'",'')]
        first=self.submit();self.done(first)
        second=self.submit();self.done(second)
        self.assertEqual(len(self.starts()),1)
        self.assertIn('legacy',self.metrics(second)['warm_reason'])
        messages=self.control._exchange({'op':'inspect'})['messages']
        self.assertFalse(any(m['op']=='resident' or 'resident' in m for m in messages))

    def test_mixed_session_keys_keep_queue_order_and_record_the_lost_reuse(self):
        first=self.submit();self.done(first)
        self.generator.command_builder=lambda recipe,output:self.command(recipe['style'],output,threads='2')
        second=self.submit();self.done(second)
        self.assertEqual(len(self.starts()),2)
        self.assertFalse(self.metrics(second)['engine_reused'])
        self.assertIn('session changed',self.metrics(second)['warm_reason'])

    def test_measurement_loss_drops_credit_without_forcing_a_new_manual_engine(self):
        first=self.submit();self.done(first)
        self.assertTrue(self.control.residents)
        self.control.monitor.read=lambda pid:(0,0)
        following=self.submit();self.done(following)
        self.assertEqual(len(self.starts()),1)
        self.assertFalse(self.control.residents)
        self.assertIn('unavailable',self.metrics(following)['warm_reason'])
        requests=[m for m in self.control._exchange({'op':'inspect'})['messages'] if m['op']=='request' and m['id']=='native:'+following]
        self.assertNotIn('resident',requests[-1])

    def test_a_session_change_after_admission_discards_the_lease_credit_before_launch(self):
        first=self.submit();self.done(first)
        acquire=self.control.acquire
        def changed(*args,**kwargs):
            result=acquire(*args,**kwargs)
            if kwargs.get('resident'):
                self.generator.command_builder=lambda recipe,output:self.command(recipe['style'],output,threads='2')
            return result
        with patch.object(self.control,'acquire',side_effect=changed):
            following=self.submit();self.done(following)
        metrics=self.metrics(following)
        self.assertEqual(metrics['engine'],'process')
        self.assertIn('changed while waiting',metrics['warm_reason'])
        requests=[m for m in self.control._exchange({'op':'inspect'})['messages'] if m['op']=='request' and m['id']=='native:'+following]
        self.assertNotIn('resident',requests[-1])
        self.assertFalse(self.control.residents)

    def test_settings_change_releases_an_idle_engine(self):
        first=self.submit();self.done(first)
        self.settings['warm_engine']=False
        self.generator.engine_settings_changed()
        until(lambda:not self.generator.warm.running() and not self.control.residents,self.generator.thread)
        self.assertFalse(self.control.residents)

    def test_engine_loss_at_prepare_replaces_borrowed_credit_with_a_full_reservation(self):
        first=self.submit();self.done(first)
        prepare=self.generator.warm.prepare
        def engine_lost(session, reuse_only=False):
            if reuse_only:
                self.generator.warm.stop()
            return prepare(session, reuse_only=reuse_only)
        with patch.object(self.generator.warm,'prepare',side_effect=engine_lost):
            following=self.submit();self.done(following)
        metrics=self.metrics(following)
        self.assertEqual(metrics['engine'],'process')
        self.assertIn('changed before submission',metrics['warm_reason'])
        requests=[m for m in self.control._exchange({'op':'inspect'})['messages'] if m['op']=='request' and m['id']=='native:'+following]
        self.assertIn('resident',requests[-2])
        self.assertNotIn('resident',requests[-1])
        self.assertFalse(self.control.residents)
        self.assertEqual(len(self.starts()),2)  # One warm child, then one cold child.
        starts=[json.loads(path.read_text()) for path in self.starts()]
        self.assertEqual(sum('--jobs' in argv for argv in starts),1)
        self.assertEqual(len(list(self.state.glob('cold-*'))),1)
