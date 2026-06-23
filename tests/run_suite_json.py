from __future__ import annotations

import io
import json
import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


suite = unittest.defaultTestLoader.discover("tests")
stream = io.StringIO()
result = unittest.TextTestRunner(stream=stream, verbosity=1).run(suite)
payload = {
    "successful": result.wasSuccessful(),
    "tests_run": result.testsRun,
    "failures": len(result.failures),
    "errors": len(result.errors),
    "skipped": len(result.skipped),
    "details": stream.getvalue()[-4000:],
}
encoded = json.dumps(payload, ensure_ascii=False)
result_path = os.environ.get("NEXETRA_TEST_RESULT_JSON")
if result_path:
    target = Path(result_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(encoded, encoding="utf-8")
print(encoded)
raise SystemExit(0 if result.wasSuccessful() else 1)
