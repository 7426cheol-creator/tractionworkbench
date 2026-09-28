"""Content identity of engine inputs (independent review R2, D-R2-02 / handoff A3).

A result must name the physics data it was computed from.  ``semantic`` walks an engine model - dataclasses,
numpy arrays, enums, tuples, dicts - into a canonical JSON-like structure of ALL its fields (every table value,
axis, mask, coefficient and flag), so a content hash cannot be fooled by a descriptive ``describe()`` that omits
data.  Equal semantics give equal hashes (dict order, 1 vs 1.0 and -0.0 are canonicalised; NaN / infinities have
explicit tokens); any changed active input changes the hash.

The implementation identity (software version, source revision, dirty tree) is a separate statement from the
data identity: the same data run by another implementation is the same input, not the same evidence.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
import math
import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np


def _num(v: float):
    if math.isnan(v):
        return "NaN"
    if math.isinf(v):
        return "+Inf" if v > 0 else "-Inf"
    return 0.0 if v == 0.0 else v


def semantic(x):
    """Canonical, JSON-serialisable content of an engine object (all fields, recursively)."""
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        return {"__type__": type(x).__name__,
                **{f.name: semantic(getattr(x, f.name)) for f in dataclasses.fields(x)}}
    if isinstance(x, enum.Enum):
        return semantic(x.value)
    if isinstance(x, np.ndarray):
        return {"__shape__": list(x.shape), "data": semantic(x.tolist())}
    if isinstance(x, dict):
        return {str(k): semantic(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [semantic(v) for v in x]
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, float, np.integer, np.floating)):
        return _num(float(x))
    if x is None or isinstance(x, str):
        return x
    if isinstance(x, Path):
        return str(x)
    # an object whose content cannot be read is named, never silently dropped (it makes the identity weaker, and
    # the marker says so)
    return {"__opaque__": f"{type(x).__module__}.{type(x).__qualname__}"}


def canonical_json(x) -> str:
    return json.dumps(semantic(x), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def content_sha256(x) -> str:
    return hashlib.sha256(canonical_json(x).encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def implementation() -> dict:
    """Software version and source revision of THIS implementation (build stamp, else the git checkout)."""
    from . import __version__
    out = {"software_version": __version__, "source_revision": None, "dirty": None, "basis": "unknown"}
    stamp = Path(__file__).with_name("_build_info.json")
    if stamp.is_file():
        try:
            d = json.loads(stamp.read_text(encoding="utf-8"))
            out.update(source_revision=d.get("source_revision"), dirty=d.get("dirty"), basis="build stamp")
            return out
        except (OSError, ValueError):
            pass
    root = Path(__file__).resolve().parents[2]
    try:
        rev = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
                             timeout=5, check=True).stdout.strip()
        st = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                            capture_output=True, text=True, timeout=5, check=True).stdout.strip()
        out.update(source_revision=rev or None, dirty=bool(st), basis="git checkout")
    except (OSError, subprocess.SubprocessError):
        pass
    return out
