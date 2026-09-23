"""Limited Python socket guard; this is not an OS-level offline test."""
import importlib.metadata
import json
from pathlib import Path
import platform
import socket
import sys
import time
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path.cwd()))
checks = []
attempts = []
def blocked(*args, **kwargs):
    attempts.append('blocked Python socket call')
    raise RuntimeError('NETWORK_DISABLED_FOR_SUBMISSION_CHECK')

socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.socket.sendto = blocked
socket.create_connection = blocked
# Verify all four guards before discovering the suite, then reset counts.
for name in ('connect', 'connect_ex', 'sendto'):
    sample = socket.socket()
    try:
        try:
            if name == 'sendto':
                sample.sendto(b'synthetic', ('127.0.0.1', 9))
            else:
                getattr(sample, name)(('127.0.0.1', 9))
        except RuntimeError as exc:
            if str(exc) != 'NETWORK_DISABLED_FOR_SUBMISSION_CHECK':
                raise
            checks.append(name)
        else:
            raise AssertionError('Guard did not block: ' + name)
    finally:
        sample.close()
try:
    socket.create_connection(('127.0.0.1', 9))
except RuntimeError as exc:
    if str(exc) != 'NETWORK_DISABLED_FOR_SUBMISSION_CHECK':
        raise
    checks.append('create_connection')
else:
    raise AssertionError('create_connection guard did not block')
attempts.clear()
started = time.perf_counter()
suite = unittest.defaultTestLoader.discover('tests', pattern='test_*.py')
result = unittest.TextTestRunner(verbosity=2).run(suite)
report = {
    'scope': 'Python socket connect/connect_ex/create_connection/sendto only; not OS-level offline',
    'python': sys.version,
    'platform': platform.platform(),
    'guards_verified': checks,
    'blocked_attempts_during_discovery_and_tests': len(attempts),
    'tests': result.testsRun,
    'passed': result.testsRun - len(result.skipped) - len(result.failures) - len(result.errors),
    'skipped': [[test.id(), why] for test, why in result.skipped],
    'failures': len(result.failures),
    'errors': len(result.errors),
    'seconds_with_discovery': round(time.perf_counter() - started, 6),
    'versions': {name: importlib.metadata.version(name) for name in ('python-docx', 'lxml', 'typing_extensions', 'tzdata')},
}
print(json.dumps(report, ensure_ascii=True, indent=2))
Path(__file__).with_name('socket-guard-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
sys.exit(not result.wasSuccessful())
