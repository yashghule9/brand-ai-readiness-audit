"""braiaudit — reference implementation of the brand-ai-readiness-audit skill set.

Each module here mirrors one skill under ``skills/`` in the repository root:

============================  =============================
module                        skill
============================  =============================
:mod:`braiaudit.fetch`        website-observer
:mod:`braiaudit.render`       crawl-render-audit
:mod:`braiaudit.clean`        content-cleaner
:mod:`braiaudit.discovery`    query-guided-discovery
:mod:`braiaudit.ontology`     failure-diagnostics
:mod:`braiaudit.report`       freshness-corroboration
:mod:`braiaudit.pipeline`     SKILL.md (orchestrator)
============================  =============================

Every public function's input/output shape is validated at the boundary
against the JSON Schemas in ``schemas/`` (see :mod:`braiaudit.schemas`), so
the code and the SKILL.md contracts it implements can never silently drift
apart.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("braiaudit")
except PackageNotFoundError:  # pragma: no cover - editable/uninstalled checkout
    __version__ = "0.0.0+dev"
