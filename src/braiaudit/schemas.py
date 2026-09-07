"""JSON Schema loading and validation for every skill's I/O contract.

The JSON Schemas under ``schemas/`` at the repository root are the single
source of truth for every contract documented in each skill's ``SKILL.md``.
This module is the only place that knows how to find and load them, and the
only place other modules call to validate a payload — so a schema and its
enforcement point never drift apart.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

import jsonschema

_SCHEMA_FILENAMES = {
    "audit-report": "audit-report.schema.json",
    "website-observer": "website-observer.output.schema.json",
    "crawl-render-audit": "crawl-render-audit.output.schema.json",
    "content-cleaner": "content-cleaner.output.schema.json",
    "query-guided-discovery": "query-guided-discovery.output.schema.json",
    "failure-diagnostics": "failure-diagnostics.output.schema.json",
    "ontology": "ontology.schema.json",
    "marketplace": "marketplace.schema.json",
}


class SchemaNotFoundError(RuntimeError):
    """Raised when the repository's schemas/ directory cannot be located.

    This happens when braiaudit is used from a non-editable wheel that did
    not bundle schemas/ (see the packaging note in pyproject.toml) — run
    from an editable checkout (``pip install -e .``) instead.
    """


@functools.lru_cache(maxsize=1)
def data_root() -> Path:
    """Directory holding the runtime data files (schemas/, ontology.yaml).

    A wheel carries them at braiaudit/_data (see pyproject's force-include);
    an editable install or a plain source checkout has only the canonical
    copies at the repository root. Packaged data wins so an installed
    braiaudit never depends on the checkout still being present.
    """
    packaged = Path(__file__).resolve().parent / "_data"
    if (packaged / "schemas").is_dir():
        return packaged
    return repo_root()


@functools.lru_cache(maxsize=1)
def repo_root() -> Path:
    """Walk up from this file until a directory containing schemas/ and
    marketplace.json is found (the repository root)."""
    here = Path(__file__).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "schemas").is_dir() and (candidate / "marketplace.json").is_file():
            return candidate
    raise SchemaNotFoundError(
        "Could not locate the brand-ai-readiness-audit repository root "
        "(a directory containing both schemas/ and marketplace.json) by "
        f"walking up from {here}. Install braiaudit with `pip install -e .` "
        "from within the repository."
    )


@functools.cache
def load_schema(name: str) -> dict[str, Any]:
    """Load a schema by short name (a key of _SCHEMA_FILENAMES)."""
    if name not in _SCHEMA_FILENAMES:
        raise KeyError(f"Unknown schema '{name}'. Known schemas: {sorted(_SCHEMA_FILENAMES)}")
    path = data_root() / "schemas" / _SCHEMA_FILENAMES[name]
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def validate(payload: Any, schema_name: str) -> None:
    """Validate `payload` against the named schema.

    Raises jsonschema.exceptions.ValidationError with a precise JSON path
    on failure. Callers should let this propagate rather than swallowing
    it — a schema violation means a skill's implementation has drifted
    from its documented contract, which is a defect worth surfacing loudly.
    """
    schema = load_schema(schema_name)
    jsonschema.validate(instance=payload, schema=schema)


def is_valid(payload: Any, schema_name: str) -> tuple[bool, str | None]:
    """Non-raising variant of validate(); returns (ok, error_message)."""
    try:
        validate(payload, schema_name)
    except jsonschema.exceptions.ValidationError as exc:
        return False, f"{'/'.join(str(p) for p in exc.absolute_path) or '<root>'}: {exc.message}"
    return True, None
