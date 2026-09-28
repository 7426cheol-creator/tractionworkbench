"""Architecture rules of the package, checked on its static import graph (system review, section 2).

The graph includes imports inside functions: a lazy import hides a dependency, it does not remove it.

* layering - a module imports only from its own layer or a lower one:
      base < models < kernel < engines < services < presentation   (``LAYERS`` below)
  e.g. the datasheet module loss model is evaluated by the kernel, so it lives in ``models`` (review R1);
* no private name (``_x``) of another package is used, by import or by attribute access;
* no import cycle among module-level imports.

A new module must be placed in ``LAYERS``; an exception to a rule is added to its allow-list with the reason.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
PKG = "traction_workbench"
ROOT = SRC / PKG

# top-level name (first component under the package) -> (rank, layer)
LAYERS = {
    **{m: (0, "base") for m in ("", "errors", "status", "settings", "units", "validation", "modulation", "i18n",
                                "identity")},
    **{m: (1, "models") for m in ("models", "scenario", "requirement", "spec_fixtures")},
    **{m: (2, "kernel") for m in ("physics", "solvers")},
    **{m: (3, "engines") for m in ("analysis", "extensions")},
    **{m: (4, "services") for m in ("io", "parsers", "examples", "project", "exchange", "service", "decision", "report",
                                    "api", "datasheet", "mathworks", "requirement_set")},
    **{m: (5, "presentation") for m in ("plots", "viz", "report_pdf", "desktop", "cli", "__main__")},
}
UPWARD_ALLOWED: dict[tuple[str, str], str] = {}       # (importer, imported) -> reason
PRIVATE_ALLOWED: dict[tuple[str, str, str], str] = {}  # (importer, source, name) -> reason


def _modname(path: Path) -> str:
    parts = list(path.relative_to(SRC).with_suffix("").parts)
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _is_module(name: str) -> bool:
    p = SRC / name.replace(".", "/")
    return p.with_suffix(".py").exists() or (p / "__init__.py").exists()


def _top(mod: str) -> str:
    parts = mod.split(".")
    return parts[1] if len(parts) > 1 else ""


def _resolve(mod: str, is_pkg: bool, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    base = mod.split(".") if is_pkg else mod.split(".")[:-1]
    base = base[: len(base) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def _type_checking_nodes(tree: ast.AST) -> set:
    skip = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.If) and "TYPE_CHECKING" in ast.unparse(n.test):
            skip.update(id(x) for x in ast.walk(n))
    return skip


class _Graph:
    def __init__(self):
        self.edges: dict[str, set] = {}          # every import (incl. lazy)
        self.module_level: dict[str, set] = {}   # imports executed at module import time
        self.private: list[tuple] = []           # (importer, source module, private name)
        for path in sorted(ROOT.rglob("*.py")):
            mod = _modname(path)
            is_pkg = path.name == "__init__.py"
            tree = ast.parse(path.read_text(encoding="utf-8"))
            skip = _type_checking_nodes(tree)
            top_level = {id(n) for n in tree.body}
            every, eager, aliases = set(), set(), {}
            for node in ast.walk(tree):
                targets = []
                if isinstance(node, ast.ImportFrom):
                    src = _resolve(mod, is_pkg, node)
                    if not src or not (src == PKG or src.startswith(PKG + ".")):
                        continue
                    for a in node.names:
                        sub = f"{src}.{a.name}"
                        if _is_module(sub):
                            targets.append(sub)
                            aliases[a.asname or a.name] = sub
                        else:
                            targets.append(src)
                            if a.name.startswith("_") and not a.name.startswith("__"):
                                self.private.append((mod, src, a.name))
                elif isinstance(node, ast.Import):
                    for a in node.names:
                        if a.name == PKG or a.name.startswith(PKG + "."):
                            targets.append(a.name)
                            if a.asname:
                                aliases[a.asname] = a.name
                else:
                    continue
                every.update(targets)
                if id(node) in top_level and id(node) not in skip:
                    eager.update(targets)
            for node in ast.walk(tree):          # module._private through an imported module alias
                if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                        and node.value.id in aliases and node.attr.startswith("_")
                        and not node.attr.startswith("__")):
                    self.private.append((mod, aliases[node.value.id], node.attr))
            self.edges[mod] = every - {mod}
            self.module_level[mod] = eager - {mod}


@pytest.fixture(scope="module")
def graph():
    return _Graph()


def test_every_module_has_a_layer(graph):
    missing = sorted({_top(m) for m in graph.edges} - set(LAYERS))
    assert not missing, f"place these top-level modules/packages in LAYERS: {missing}"


def test_no_upward_imports(graph):
    """E.g. the kernel evaluates the datasheet module model, so that model is part of the model layer; a lazy
    import from the kernel into ``extensions`` would make an optional analysis a hidden core dependency."""
    bad = []
    for mod, targets in graph.edges.items():
        rank = LAYERS[_top(mod)][0]
        for t in targets:
            if LAYERS[_top(t)][0] > rank and (mod, t) not in UPWARD_ALLOWED:
                bad.append(f"{mod} ({LAYERS[_top(mod)][1]}) -> {t} ({LAYERS[_top(t)][1]})")
    assert not bad, "upward imports:\n" + "\n".join(sorted(bad))


def test_no_private_names_across_packages(graph):
    bad = sorted({f"{m} uses {s}.{n}" for m, s, n in graph.private
                  if _top(m) != _top(s) and (m, s, n) not in PRIVATE_ALLOWED})
    assert not bad, "private names used across packages (make them public):\n" + "\n".join(bad)


def test_no_module_level_import_cycles(graph):
    """Tarjan's strongly connected components on the imports executed at import time."""
    index, low, stack, on, sccs, counter = {}, {}, [], set(), [], [0]

    def visit(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in graph.module_level.get(v, ()):
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                sccs.append(sorted(comp))

    for v in sorted(graph.module_level):
        if v not in index:
            visit(v)
    assert not sccs, f"module-level import cycles: {sccs}"


def test_module_loss_model_is_part_of_the_drive_model():
    """R1: the model the kernel evaluates is typed on the inverter model; the former path re-exports it."""
    from traction_workbench.extensions import module_loss as former
    from traction_workbench.models import components, module_loss
    assert former.ModuleLossModel is module_loss.ModuleLossModel
    assert former.inverter_losses is module_loss.inverter_losses
    assert components.InverterModel.__dataclass_fields__["module_loss"].type == "ModuleLossModel | None"
