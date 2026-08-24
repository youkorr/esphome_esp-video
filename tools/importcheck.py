"""Execute a component's __init__.py with a stand-in for esphome.

esphome is not installed here, so `esphome config` cannot run, and a whole
class of mistake reaches the board's build instead: a schema that references a
validator defined further down the file raises NameError at import time, long
before any YAML is looked at. Nothing about that needs esphome to be real --
only that the module be executed in order -- so a permissive stand-in for every
esphome import is enough to catch it.

It proves nothing about whether the schema is *correct*. It proves the file
imports, which is the step that was failing.

    python3 importcheck.py <path to __init__.py> [...]
"""
import importlib.abc
import importlib.machinery
import pathlib
import sys
import types
from unittest.mock import MagicMock


class StubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Anything under `esphome` becomes a mock that accepts everything."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "esphome" or fullname.startswith("esphome."):
            return importlib.machinery.ModuleSpec(fullname, self)
        return None

    def create_module(self, spec):
        module = MagicMock(name=spec.name)
        module.__name__ = spec.name
        module.__path__ = []
        module.__spec__ = spec
        # esphome.const is read for plain string constants; a mock attribute
        # would work but a real string keeps dictionary keys legible.
        if spec.name == "esphome.const":
            real = types.ModuleType(spec.name)
            real.__getattr__ = lambda name: name.replace("CONF_", "").lower()
            return real
        return module

    def exec_module(self, module):
        return None


def check(path):
    sys.meta_path.insert(0, StubFinder())
    source = pathlib.Path(path).read_text()
    namespace = {"__name__": "component_under_test", "__file__": str(path)}
    try:
        exec(compile(source, str(path), "exec"), namespace)
    except NameError as err:
        print(f"  ECHEC  {path}\n         NameError: {err}")
        return 1
    except Exception as err:  # noqa: BLE001 - anything else is worth seeing too
        print(f"  ?      {path}\n         {type(err).__name__}: {err}")
        return 0
    finally:
        sys.meta_path.pop(0)
    have = [n for n in ("CONFIG_SCHEMA", "to_code") if n in namespace]
    print(f"  ok     {path}  (a defini {', '.join(have) or 'rien'})")
    return 0


if __name__ == "__main__":
    sys.exit(sum(check(p) for p in sys.argv[1:]))
