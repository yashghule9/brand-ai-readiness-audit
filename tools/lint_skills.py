#!/usr/bin/env python
"""Lint every SKILL.md in the repository against the Claude Agent Skill
frontmatter contract, and cross-check marketplace.json against what's
actually on disk.

Checks:
  - every skills/<dir>/SKILL.md has YAML frontmatter with `name` and
    `description`
  - `name` is lowercase kebab-case, <= 64 chars, and matches its directory
    name (or, for the root SKILL.md, matches marketplace.json's
    `orchestrator`)
  - `description` is non-empty and reasonably substantial (a real trigger
    phrase, not a placeholder)
  - every skill listed in marketplace.json has a SKILL.md that exists, and
    every SKILL.md on disk is listed in marketplace.json
  - marketplace.json validates against schemas/marketplace.schema.json
  - skills/failure-diagnostics/references/ontology.yaml validates against
    schemas/ontology.schema.json

Exit code is non-zero if any check fails — wired into CI (see
.github/workflows/ci.yml) so a broken skill definition fails the build
the same way a failing test would.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from braiaudit.schemas import validate  # noqa: E402

_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _parse_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError(f"{path}: missing YAML frontmatter block (--- ... ---)")
    return yaml.safe_load(match.group(1)) or {}


def lint_skill_md(path: Path, expected_name: str | None, errors: list[str]) -> None:
    try:
        fm = _parse_frontmatter(path)
    except ValueError as exc:
        errors.append(str(exc))
        return

    name = fm.get("name")
    description = fm.get("description")

    if not name:
        errors.append(f"{path}: frontmatter missing `name`")
    elif not _NAME_RE.match(name):
        errors.append(f"{path}: name {name!r} is not lowercase kebab-case")
    elif len(name) > 64:
        errors.append(f"{path}: name {name!r} exceeds 64 characters")
    elif expected_name and name != expected_name:
        errors.append(f"{path}: name {name!r} does not match expected {expected_name!r}")

    if not description:
        errors.append(f"{path}: frontmatter missing `description`")
    elif len(description) < 40:
        errors.append(
            f"{path}: description is only {len(description)} chars — too short to be a "
            "real trigger phrase"
        )


def main() -> int:
    errors: list[str] = []

    marketplace_path = REPO_ROOT / "marketplace.json"
    marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
    validate(marketplace, "marketplace")

    declared_paths = {Path(REPO_ROOT / s["path"]).resolve() for s in marketplace["skills"]}

    root_skill = REPO_ROOT / "SKILL.md"
    lint_skill_md(root_skill, marketplace.get("orchestrator"), errors)
    if root_skill.resolve() not in declared_paths:
        errors.append(f"{root_skill}: not listed in marketplace.json's skills[]")

    on_disk_skill_mds = {root_skill.resolve()}
    for skill_dir in sorted((REPO_ROOT / "skills").iterdir()):
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            errors.append(f"{skill_dir}: missing SKILL.md")
            continue
        on_disk_skill_mds.add(skill_md.resolve())
        lint_skill_md(skill_md, skill_dir.name, errors)
        if skill_md.resolve() not in declared_paths:
            errors.append(f"{skill_md}: not listed in marketplace.json's skills[]")

    for declared in declared_paths:
        if declared not in on_disk_skill_mds:
            errors.append(f"marketplace.json references missing file: {declared}")

    ontology_path = (
        REPO_ROOT / "skills" / "failure-diagnostics" / "references" / "ontology.yaml"
    )
    ontology_raw = yaml.safe_load(ontology_path.read_text(encoding="utf-8"))
    validate(ontology_raw, "ontology")
    unknown_categories = {
        fm["category"] for fm in ontology_raw["failure_modes"].values()
    } - set(ontology_raw["categories"])
    if unknown_categories:
        errors.append(f"ontology.yaml: undeclared categories referenced: {unknown_categories}")

    if errors:
        print(f"lint_skills.py: {len(errors)} problem(s) found:\n")
        for err in errors:
            print(f"  - {err}")
        return 1

    print(f"lint_skills.py: OK — {len(on_disk_skill_mds)} SKILL.md file(s), marketplace.json, "
          "and ontology.yaml all check out.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
