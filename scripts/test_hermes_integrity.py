"""Offline operational regressions; never import the runtime or contact services."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from typing import Any

REPO = Path(__file__).resolve().parents[1]


def load(path) -> Any:
    if not path.exists():
        raise unittest.SkipTest(f'Local operational script not installed: {path}')
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class IntegrityTests(unittest.TestCase):
    def test_live_upstream_is_accepted(self):
        doctor = load(Path('/root/.hermes/scripts/hermes_doctor.py'))
        self.assertEqual(doctor.check_integrity(), (True, []))

    def test_hourly_health_accepts_upstream(self):
        health = load(Path('/root/.hermes/scripts/hermes_health_check.py'))
        self.assertEqual(health.check_fixes(), (True, []))

    def test_structural_regressions_are_detected_without_writes(self):
        from hermes_integrity import check_integrity
        original = (REPO / 'agent/codex_runtime.py').read_text()
        mutations = [
            ('stream_kwargs["stream"] = True', 'stream_kwargs["stream"] = False'),
            ('self.output_items.append(done_item)', 'pass'),
            ('return assembler.result()', 'return None'),
            ('self.saw_terminal = True', 'self.saw_terminal = True; bad = resp_obj.output'),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'agent/transports').mkdir(parents=True)
            transport = root / 'agent/transports/codex.py'
            transport.write_text((REPO / 'agent/transports/codex.py').read_text())
            runtime = root / 'agent/codex_runtime.py'
            for old, new in mutations:
                with self.subTest(mutation=old):
                    self.assertIn(old, original)
                    changed = original.replace(old, new)
                    runtime.write_text(changed)
                    self.assertTrue(check_integrity(root))
                    self.assertEqual(runtime.read_text(), changed)
            runtime.write_text(original)
            transport.write_text('kwargs["tools"] = None')
            self.assertTrue(check_integrity(root))
            runtime.write_text('invalid python !')
            self.assertTrue(check_integrity(root))
            runtime.unlink()
            self.assertTrue(check_integrity(root))

    def test_watchdog_accepts_upstream(self):
        self.assertEqual(load(REPO / 'scripts/hermes_watchdog.py').check_fixes(), [])

    def test_repair_cannot_restore_even_with_golden_files(self):
        doctor = load(Path('/root/.hermes/scripts/hermes_doctor.py'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            golden = root / 'golden'
            golden.mkdir()
            target = root / 'runtime.py'
            target.write_text('new runtime')
            (golden / 'runtime.py').write_text('incompatible old runtime')
            doctor.GOLDEN = golden
            doctor.BACKUP_DIR = root / 'backups'
            doctor.FIX_FILES = {'runtime.py': target}
            doctor.REPO = root
            calls = []
            doctor.run = lambda *a, **kw: (calls.append(a) or (0, ''))
            ok, _ = doctor.repair_fixes()
            self.assertFalse(ok)
            self.assertEqual(target.read_text(), 'new runtime')
            self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
