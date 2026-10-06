"""无第三方依赖的测试入口：跑 unittest 用例 + 手写断言风格的 fefo 测例。

用法：python3 run_tests.py
原 test_fefo.py 不 import pytest，可直接发现并执行其中 test_* 函数，
与 pytest 跑出的结果一致（断言失败即非零退出）。
"""
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _collect_plain_assert_modules():
    """把 app/tests 下不带 TestCase 的 test_* 模块里的 test_* 函数包成用例。"""
    import app.tests.test_fefo as fefo  # noqa: F401
    suite = unittest.TestSuite()
    mod = importlib.import_module("app.tests.test_fefo")
    for name in sorted(dir(mod)):
        if name.startswith("test_") and callable(getattr(mod, name)):
            suite.addTest(unittest.FunctionTestCase(getattr(mod, name), description=name))
    return suite


def main():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromName("app.tests.test_projection"))
    suite.addTest(_collect_plain_assert_modules())
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
