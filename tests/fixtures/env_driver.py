"""Test-only equivalent of env NAME=value python script/-m module args."""

import os
import runpy
import sys

args = sys.argv[1:]
while args and "=" in args[0]:
    name, value = args.pop(0).split("=", 1)
    os.environ[name] = value
if not args or os.path.normcase(os.path.realpath(args.pop(0))) != os.path.normcase(os.path.realpath(sys.executable)):
    raise ValueError("This fixture launcher only runs the current Python interpreter")
if args[0] == "-m":
    module = args[1]
    sys.argv = [module, *args[2:]]
    runpy.run_module(module, run_name="__main__", alter_sys=True)
else:
    sys.argv = args
    runpy.run_path(args[0], run_name="__main__")
