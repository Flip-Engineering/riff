"""Versioned resident bridge and ownership, without model execution."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from model_admission import ModelAdmission, RESIDENT_PROTOCOL, ResidentUnavailable
from fixture_waits import signalled

PORT = r'''
import json,sys,os
from pathlib import Path
state=Path(os.environ["FAKE_ENGINE_STATE"]) if "FAKE_ENGINE_STATE" in os.environ else None
messages=[]
for line in sys.stdin:
    m=json.loads(line); messages.append(m); op=m['op']
    if op=='capabilities': r={'state':'capabilities','resident':'riff.resident.v1'}
    elif op=='estimate': r={'state':'estimated','requirement':{'host_peak':1000,'device_peak':0,'basis':{'weights':600}}}
    elif op=='request': r={'state':'admitted','requirement':m['requirement']}
    elif op=='resident':
        action=m['action']
        if action=='fit':
            r=json.loads((state/'fit.json').read_text()) if state and (state/'fit.json').exists() else {'state':'resident_fit','keep':True,'figures':m['snapshot']}
        elif action=='release_if_idle' and state and (state/'scheduler-busy').exists(): r={'state':'busy'}
        else: r={'state':'released' if action.startswith('release') else 'resident'}
        r['protocol']='riff.resident.v1'
    elif op=='status': r={'state':'ready','active':[],'waiting':[]}
    elif op=='inspect': r={'state':'inspected','messages':messages[:-1]}
    else: r={'state':'released'}
    print(json.dumps(r),flush=True)
'''


class Observation:
    def __init__(self):
        self.value = {"host": {"available": 2_000, "reserve": 100, "pressure": "normal"},
                      "devices": {}, "device_processes": {}}
        self.fresh = 0

    def snapshot(self):
        return self.value

    def fresh_snapshot(self):
        self.fresh += 1
        return self.snapshot()


class Process:
    pid = 123

    def poll(self):
        return None


class ResidentBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.observer = Observation()
        self.control = ModelAdmission(Path(self.temp.name), observer=self.observer,
                                      command=[sys.executable, "-u", "-c", PORT])
        self.addCleanup(self.control.close)
        self.control.monitor.read = lambda pid: (600, 600)
        self.requirement = {"host_peak": 1_000, "device_peak": 0, "basis": {"weights": 600}}
        self.cancel = threading.Event()
        self.engine = Process()

    def keep(self, requirement=None):
        self.control.acquire("first", requirement or self.requirement, self.cancel)
        self.control.attach("first", self.engine)
        info, reason = self.control.retain_resident("first", self.engine)
        self.assertFalse(reason)
        self.control.release("first")
        return info["id"]

    def messages(self):
        return self.control._exchange({"op": "inspect"})["messages"]

    def test_versioned_requests_share_one_measured_baseline_without_duplicate_usage_credit(self):
        lease = self.keep()
        self.control.acquire("warm", self.requirement, self.cancel, resident=lease)
        self.control.attach("warm", self.engine)
        self.control.monitor.read = lambda pid: (900, 900)
        self.control.acquire("writer", {"host_peak": 100}, self.cancel)
        request = [m for m in self.messages() if m['op'] == 'request' and m['id'] == 'writer'][-1]
        self.assertEqual(request['usage'][lease], {"host": 600, "device": 0})
        self.assertEqual(request['usage']['warm'], {"host": 900, "device": 0})
        warm = [m for m in self.messages() if m['op'] == 'request' and m['id'] == 'warm'][-1]
        self.assertEqual((warm['resident'], warm['resident_protocol']), (lease, RESIDENT_PROTOCOL))
        self.control.release("writer")
        self.control.release("warm")
        self.control.release_resident(lease)
        self.assertFalse(self.control.residents)

    def test_fresh_fit_sends_the_registered_lease_and_all_compared_observations(self):
        lease = self.keep()
        with patch('model_admission.resource_inputs', return_value={}):
            requirement, decision = self.control.resident_plan("next", {}, "native", self.cancel, resident=lease)
        self.assertEqual(requirement, self.requirement)
        self.assertTrue(decision['keep'])
        self.assertGreater(self.observer.fresh, 0)
        fit = [m for m in self.messages() if m['op'] == 'resident' and m['action'] == 'fit'][-1]
        self.assertEqual(fit['resident'], lease)
        self.assertEqual(fit['snapshot'], self.observer.value)
        self.assertEqual(fit['usage'][lease], {"host": 600, "device": 0})

    def test_idle_residents_are_restored_after_a_policy_process_restart(self):
        lease = self.keep()
        before = self.control.process
        before.terminate()
        before.wait()
        self.control.acquire("warm", self.requirement, self.cancel, resident=lease)
        self.assertNotEqual(before.pid, self.control.process.pid)
        messages = self.messages()
        update = next(i for i,m in enumerate(messages) if m['op'] == 'resident' and m['action'] == 'update')
        request = next(i for i,m in enumerate(messages) if m['op'] == 'request')
        self.assertLess(update, request)
        self.control.release("warm")

    def test_unmeasurable_host_or_cuda_usage_never_creates_guessed_credit(self):
        for host, requirement in ((0, self.requirement), (600, {**self.requirement, 'device_peak': 1_000, 'device': 'gpu'})):
            with self.subTest(host=host, requirement=requirement):
                self.control.monitor.read = lambda pid: (host, host)
                self.control.acquire("first", requirement, self.cancel)
                info, reason = self.control.retain_resident("first", self.engine)
                self.assertIsNone(info)
                self.assertIn('unavailable', reason)
                self.assertFalse(self.control.residents)
                self.control.release("first")

    def test_old_scheduler_gets_no_new_protocol_messages(self):
        legacy = PORT.replace(",'resident':'riff.resident.v1'", "")
        self.control.command = [sys.executable, '-u', '-c', legacy]
        self.assertFalse(self.control.supports_residents())
        self.control.acquire('first', self.requirement, self.cancel)
        info, reason = self.control.retain_resident('first', self.engine)
        self.assertIsNone(info)
        self.assertIn('does not support', reason)
        self.control.release('first')
        self.assertFalse(any(m['op'] == 'resident' or 'resident' in m for m in self.messages()))

    def test_mismatched_capability_version_fails_clearly(self):
        self.control.command = [sys.executable, '-u', '-c', PORT.replace('riff.resident.v1', 'riff.resident.v2')]
        with self.assertRaisesRegex(ValueError, 'Unsupported resident lease protocol'):
            self.control.supports_residents()

    def test_a_malformed_fit_decision_is_not_treated_as_permission_to_reuse(self):
        self.control.command=[sys.executable,'-u','-c',PORT.replace("'keep':True", "'keep':'yes'")]
        with patch('model_admission.resource_inputs',return_value={}):
            with self.assertRaisesRegex(ValueError,'invalid resident fit decision'):
                self.control.resident_plan('next',{},'native',self.cancel)

    def test_measurement_loss_releases_a_waiting_job_before_its_lease(self):
        waiting_port = PORT.replace(
            "elif op=='request': r={'state':'admitted','requirement':m['requirement']}",
            "elif op=='request': r={'state':'waiting' if m['requirement'].get('wait') else 'admitted','requirement':m['requirement'],'reason':'Waiting for memory'}")
        self.control.command = [sys.executable, '-u', '-c', waiting_port]
        lease = self.keep()
        waiting, failures = threading.Event(), []
        def acquire():
            try:
                self.control.acquire('next', {**self.requirement, 'wait': True}, self.cancel,
                                     resident=lease, on_wait=lambda reason: waiting.set())
            except ResidentUnavailable as error:
                failures.append(str(error))
        worker = threading.Thread(target=acquire)
        worker.start()
        try:
            self.assertTrue(signalled(waiting, worker))
            self.control.monitor.read = lambda pid: (0, 0)
        finally:
            # The regular admission wake polls the changed observation.
            worker.join()
        self.assertEqual(len(failures), 1)
        self.assertIn('unavailable', failures[0])
        self.assertNotIn('next', self.control.entries)
        self.control.release_resident(lease)
        messages = self.messages()
        job_release = next(i for i,m in enumerate(messages) if m['op']=='release' and m['id']=='next')
        lease_release = next(i for i,m in enumerate(messages) if m['op']=='resident' and m['action']=='release' and m['id']==lease)
        self.assertLess(job_release, lease_release)

    def test_idle_release_waits_for_writer_ownership_then_serializes_against_new_admission(self):
        lease = self.keep()
        self.control.acquire('writer', {'host_peak': 100}, self.cancel)
        stopped = threading.Event()
        self.assertFalse(self.control.release_resident_if_idle(lease, stopped.set))
        self.assertFalse(stopped.is_set())
        self.control.release('writer')
        closing, finish, admitted = threading.Event(), threading.Event(), threading.Event()
        def stop():
            closing.set()
            finish.wait()
            stopped.set()
        release = threading.Thread(target=lambda: self.control.release_resident_if_idle(lease, stop))
        release.start()
        self.assertTrue(signalled(closing, release))
        def reserve():
            self.control.acquire('next', {'host_peak': 100}, self.cancel)
            admitted.set()
        next_job = threading.Thread(target=reserve)
        next_job.start()
        try:
            self.assertFalse(admitted.is_set())
        finally:
            finish.set()
            release.join()
            next_job.join()
            self.control.release('next')
        self.assertTrue(stopped.is_set())
        self.assertTrue(admitted.is_set())
        self.assertFalse(self.control.residents)
