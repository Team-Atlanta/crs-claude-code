"""Stub libCRS so patcher can be imported outside Docker."""
import sys
from types import ModuleType, SimpleNamespace

# Create minimal libCRS stub before patcher is imported.
libCRS = ModuleType("libCRS")
libCRS.base = ModuleType("libCRS.base")
libCRS.base.DataType = SimpleNamespace(PATCH="patch", POV="pov", DIFF="diff", SEED="seed", BUG_CANDIDATE="bug_candidate")
libCRS.base.SourceType = SimpleNamespace(REPO="repo", TARGET_SOURCE="target_source")
libCRS.cli = ModuleType("libCRS.cli")
libCRS.cli.main = ModuleType("libCRS.cli.main")
libCRS.cli.main.init_crs_utils = lambda: None

sys.modules["libCRS"] = libCRS
sys.modules["libCRS.base"] = libCRS.base
sys.modules["libCRS.cli"] = libCRS.cli
sys.modules["libCRS.cli.main"] = libCRS.cli.main
