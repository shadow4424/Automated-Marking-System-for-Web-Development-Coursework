from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence

from ams.core.models import (
    ArtefactInventory,
    ArtefactRelation,
    RoleMappedSubmission,
    SubmissionContext,
    SubmissionManifest,
    SubmissionManifestEntry,
)


_COMPONENT_BY_SUFFIX = {
    ".html": "html",
    ".css": "css",
    ".js": "js",
    ".php": "php",
    ".sql": "sql",
}

_BACKUP_NAME_RE = re.compile(
    r"(^~)|(~$)|(\.bak$)|(\.old$)|(\.orig$)|(\.copy$)|(\.backup$)|(^copy[_ -])|([_ -](copy|backup|old)$)",
    re.IGNORECASE,
)
_CSS_IMPORT_RE = re.compile(r"""@import\s+(?:url\()?['"]([^'"]+)['"]\)?""", re.IGNORECASE)
_JS_IMPORT_RE = re.compile(r"""import\s+(?:[^'"]+from\s+)?['"]([^'"]+)['"]""", re.IGNORECASE)
_JS_FETCH_RE = re.compile(r"""fetch\s*\(\s*['"`]([^'"`]+)['"`]""", re.IGNORECASE)
_PHP_INCLUDE_RE = re.compile(
    r"""\b(?:include|include_once|require|require_once)\s*(?:\(\s*)?['"]([^'"]+)['"]""",
    re.IGNORECASE,
)


@dataclass
class _HTMLDiscovery:
    css_links: List[str]
    js_links: List[str]
    links: List[str]
    form_actions: List[str]


class _HTMLDiscoveryParser(HTMLParser):
    def __init__(self) -> None:
        """Return the."""
        super().__init__()
        self.css_links: List[str] = []
        self.js_links: List[str] = []
        self.links: List[str] = []
        self.form_actions: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[tuple[str, str | None]]) -> None:
        """Handle the start tag."""
        attrs_dict = {key.lower(): (value or "").strip() for key, value in attrs}
        lowered_tag = tag.lower()
        if lowered_tag == "link" and attrs_dict.get("rel", "").lower() == "stylesheet":
            href = attrs_dict.get("href")
            if href:
                self.css_links.append(href)
        elif lowered_tag == "script":
            src = attrs_dict.get("src")
            if src:
                self.js_links.append(src)
        elif lowered_tag == "a":
            href = attrs_dict.get("href")
            if href:
                self.links.append(href)
        elif lowered_tag == "form":
            action = attrs_dict.get("action")
            if action:
                self.form_actions.append(action)


def build_submission_evidence(context: SubmissionContext) -> None:
    """Build submission evidence."""
    root = Path(
        context.metadata.get("resolved_root", context.workspace_path / "submission")
    ).resolve()
    manifest_entries: List[SubmissionManifestEntry] = []
    artefacts: Dict[str, List[str]] = defaultdict(list)
    relations: List[ArtefactRelation] = []
    duplicate_candidates: Dict[tuple[str, str], List[str]] = defaultdict(list)
    backup_files: List[str] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        component = _COMPONENT_BY_SUFFIX.get(path.suffix.lower(), "asset")
        is_backup = _is_backup_file(path)
        entry = SubmissionManifestEntry(
            path=relative,
            absolute_path=str(path),
            component=component,
            size_bytes=path.stat().st_size,
            backup=is_backup,
        )
        manifest_entries.append(entry)
        artefacts[component].append(relative)
        duplicate_candidates[(component, path.name.lower())].append(relative)
        if is_backup:
            backup_files.append(relative)

    duplicate_files = sorted(
        item
        for files in duplicate_candidates.values()
        if len(files) > 1
        for item in files
    )

    relation_map = _build_relation_map(root, artefacts)
    relations.extend(relation_map["relations"])
    candidate_execution_map = relation_map["candidate_execution_map"]

    reachable = _compute_reachable(
        artefacts=artefacts,
        relations=relations,
        seeds=relation_map["seed_paths"],
    )
    role_mapping = _map_roles(
        root=root,
        artefacts=artefacts,
        relations=relations,
        reachable=reachable,
        candidate_execution_map=candidate_execution_map,
    )

    reachable_paths = {
        item
        for values in role_mapping.relevant_files.values()
        for item in values
    }
    orphan_files = sorted(
        path
        for component, files in artefacts.items()
        if component in {"html", "css", "js", "php", "sql"}
        for path in files
        if path not in reachable_paths and path not in backup_files
    )

    manifest = SubmissionManifest(
        entries=_apply_manifest_flags(
            manifest_entries,
            reachable=reachable_paths,
            orphan_files=set(orphan_files),
            duplicate_files=set(duplicate_files),
        ),
        warnings=[],
        errors=[],
    )
    inventory = ArtefactInventory(
        artefacts={key: sorted(value) for key, value in artefacts.items()},
        relations=relations,
        orphan_files=orphan_files,
        duplicate_files=duplicate_files,
        backup_files=sorted(set(backup_files)),
        candidate_execution_map={
            key: sorted(value) for key, value in candidate_execution_map.items()
        },
    )

    context.manifest = manifest
    context.artefact_inventory = inventory
    context.role_mapping = role_mapping
    context.scoring_files = _materialize_scoring_files(root, role_mapping.relevant_files)
    context.metadata["manifest"] = manifest.to_dict()
    context.metadata["artefact_inventory"] = inventory.to_dict()
    context.metadata["role_mapping"] = role_mapping.to_dict()


def _apply_manifest_flags(
    entries: Sequence[SubmissionManifestEntry],
    *,
    reachable: set[str],
    orphan_files: set[str],
    duplicate_files: set[str],
) -> List[SubmissionManifestEntry]:
    """Apply the manifest flags."""
    updated: List[SubmissionManifestEntry] = []
    for entry in entries:
        updated.append(
            SubmissionManifestEntry(
                path=entry.path,
                absolute_path=entry.absolute_path,
                component=entry.component,
                size_bytes=entry.size_bytes,
                reachable=entry.path in reachable,
                orphan=entry.path in orphan_files,
                duplicate=entry.path in duplicate_files,
                backup=entry.backup,
            )
        )
    return updated


def _materialize_scoring_files(root: Path, relevant_files: Mapping[str, Sequence[str]]) -> Dict[str, List[Path]]:
    """Return scoring files."""
    result: Dict[str, List[Path]] = {}
    for component, files in relevant_files.items():
        materialized: List[Path] = []
        for relative in files:
            candidate = (root / relative).resolve()
            if candidate.exists():
                materialized.append(candidate)
        result[component] = materialized
    return result


def _build_relation_map(root: Path, artefacts: Mapping[str, Sequence[str]]) -> Dict[str, object]:
    """Build the relation map."""
    relations: List[ArtefactRelation] = []
    candidate_execution_map: Dict[str, List[str]] = defaultdict(list)
    seed_paths: List[str] = []

    html_files = sorted(artefacts.get("html", []))
    php_files = sorted(artefacts.get("php", []))
    sql_files = sorted(artefacts.get("sql", []))

    for relative in html_files:
        path = root / relative
        discovery = _discover_html_links(path)
        for href in discovery.css_links:
            resolved = _resolve_internal_reference(root, path.parent, href)
            if resolved:
                relations.append(ArtefactRelation(source=relative, target=resolved, relation="html_links_css"))
        for src in discovery.js_links:
            resolved = _resolve_internal_reference(root, path.parent, src)
            if resolved:
                relations.append(ArtefactRelation(source=relative, target=resolved, relation="html_links_js"))
        for href in discovery.links:
            resolved = _resolve_internal_reference(root, path.parent, href)
            if resolved:
                relations.append(ArtefactRelation(source=relative, target=resolved, relation="html_links_page"))
        for action in discovery.form_actions:
            resolved = _resolve_internal_reference(root, path.parent, action)
            if resolved:
                relations.append(ArtefactRelation(source=relative, target=resolved, relation="html_form_action"))
                candidate_execution_map["backend_entrypoint"].append(resolved)

        if path.name.lower() == "index.html":
            seed_paths.append(relative)
            candidate_execution_map["primary_page"].append(relative)

    for relative in artefacts.get("css", []):
        path = root / relative
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in _CSS_IMPORT_RE.findall(content):
            resolved = _resolve_internal_reference(root, path.parent, match)
            if resolved:
                relations.append(ArtefactRelation(source=relative, target=resolved, relation="css_import"))

    for relative in artefacts.get("js", []):
        path = root / relative
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in _JS_IMPORT_RE.findall(content):
            resolved = _resolve_internal_reference(root, path.parent, match)
            if resolved:
                relations.append(ArtefactRelation(source=relative, target=resolved, relation="js_import"))
        api_hints = [value for value in _JS_FETCH_RE.findall(content) if _looks_like_api_reference(value)]
        if api_hints:
            candidate_execution_map["api_client_code"].append(relative)

    for relative in php_files:
        path = root / relative
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in _PHP_INCLUDE_RE.findall(content):
            resolved = _resolve_internal_reference(root, path.parent, match)
            if resolved:
                relations.append(ArtefactRelation(source=relative, target=resolved, relation="php_include"))
        if path.name.lower() in {"index.php", "process.php", "submit.php", "handler.php", "form.php"}:
            candidate_execution_map["backend_entrypoint"].append(relative)
        if _looks_like_php_api_endpoint(content, path.name):
            candidate_execution_map["api_backend"].append(relative)
            candidate_execution_map["backend_entrypoint"].append(relative)

    for relative in sql_files:
        if Path(relative).name.lower() in {"database.sql", "schema.sql", "db.sql", "seed.sql"}:
            candidate_execution_map["database_schema"].append(relative)

    if not seed_paths:
        if html_files:
            seed_paths.append(html_files[0])
            candidate_execution_map["primary_page"].append(html_files[0])
        elif php_files:
            seed_paths.append(php_files[0])
            candidate_execution_map["backend_entrypoint"].append(php_files[0])

    if not candidate_execution_map["database_schema"] and sql_files:
        candidate_execution_map["database_schema"].append(sql_files[0])

    return {
        "relations": relations,
        "candidate_execution_map": {
            key: list(dict.fromkeys(values)) for key, values in candidate_execution_map.items()
        },
        "seed_paths": list(dict.fromkeys(seed_paths)),
    }


def _discover_html_links(path: Path) -> _HTMLDiscovery:
    """Discover the html links."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return _HTMLDiscovery(css_links=[], js_links=[], links=[], form_actions=[])
    parser = _HTMLDiscoveryParser()
    try:
        parser.feed(content)
    except Exception:
        return _HTMLDiscovery(css_links=[], js_links=[], links=[], form_actions=[])
    return _HTMLDiscovery(
        css_links=list(parser.css_links),
        js_links=list(parser.js_links),
        links=list(parser.links),
        form_actions=list(parser.form_actions),
    )


def _resolve_internal_reference(root: Path, base_dir: Path, reference: str) -> str | None:
    """Resolve the internal reference."""
    raw = (reference or "").strip()
    if not raw:
        return None
    if raw.startswith(("http://", "https://", "mailto:", "tel:", "javascript:", "#")):
        return None
    target = raw.split("#", 1)[0].split("?", 1)[0].strip()
    if not target:
        return None
    candidate = (base_dir / target).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.exists() or not candidate.is_file():
        return None
    return candidate.relative_to(root).as_posix()


def _compute_reachable(
    *,
    artefacts: Mapping[str, Sequence[str]],
    relations: Sequence[ArtefactRelation],
    seeds: Sequence[str],
) -> set[str]:
    """Compute the reachable."""
    adjacency: Dict[str, List[str]] = defaultdict(list)
    for relation in relations:
        adjacency[relation.source].append(relation.target)
        if relation.relation in {"html_links_page", "html_form_action", "php_include", "js_import", "css_import"}:
            adjacency[relation.target].append(relation.source)

    fallback = [
        path
        for component in ("html", "php")
        for path in artefacts.get(component, [])
    ]
    queue = deque(path for path in list(seeds) + fallback[:1] if path)
    visited: set[str] = set()

    while queue:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        for neighbour in adjacency.get(current, []):
            if neighbour not in visited:
                queue.append(neighbour)
    return visited


def _map_roles(
    *,
    root: Path,
    artefacts: Mapping[str, Sequence[str]],
    relations: Sequence[ArtefactRelation],
    reachable: set[str],
    candidate_execution_map: Mapping[str, Sequence[str]],
) -> RoleMappedSubmission:
    """Return roles."""
    relation_lookup: Dict[str, List[ArtefactRelation]] = defaultdict(list)
    for relation in relations:
        relation_lookup[relation.source].append(relation)

    roles: Dict[str, List[str]] = defaultdict(list)
    relevant_files: Dict[str, List[str]] = defaultdict(list)
    trace: List[Dict[str, object]] = []

    primary_page = _select_best_path(
        artefacts.get("html", []),
        reachable=reachable,
        preferred_names=("index.html", "home.html"),
    )
    if primary_page:
        roles["primary_page"].append(primary_page)
        relevant_files["html"].append(primary_page)
        trace.append(
            {
                "role": "primary_page",
                "path": primary_page,
                "reason": "preferred entrypoint and reachable",
            }
        )

    secondary_pages = [
        path
        for path in sorted(artefacts.get("html", []))
        if path != primary_page and path in reachable and not _is_backup_file(Path(path))
    ]
    for path in secondary_pages:
        roles["secondary_page"].append(path)
        relevant_files["html"].append(path)
        trace.append(
            {
                "role": "secondary_page",
                "path": path,
                "reason": "reachable linked page",
            }
        )

    linked_css = _targets_for_sources(
        sources=roles["primary_page"] + roles["secondary_page"],
        relation_lookup=relation_lookup,
        relation_name="html_links_css",
    )
    linked_js = _targets_for_sources(
        sources=roles["primary_page"] + roles["secondary_page"],
        relation_lookup=relation_lookup,
        relation_name="html_links_js",
    )

    for path in _select_relevant_group(
        artefacts.get("css", []),
        linked=linked_css,
        reachable=reachable,
        preferred_names=("style.css", "main.css"),
    ):
        roles["stylesheet_set"].append(path)
        relevant_files["css"].append(path)
        trace.append(
            {
                "role": "stylesheet_set",
                "path": path,
                "reason": "linked stylesheet selected for scoring",
            }
        )

    for path in _select_relevant_group(
        artefacts.get("js", []),
        linked=linked_js,
        reachable=reachable,
        preferred_names=("app.js", "main.js", "script.js"),
    ):
        roles["script_set"].append(path)
        relevant_files["js"].append(path)
        trace.append(
            {
                "role": "script_set",
                "path": path,
                "reason": "linked script selected for scoring",
            }
        )

    backend_entrypoint = _select_best_path(
        candidate_execution_map.get("backend_entrypoint", []),
        reachable=reachable,
        preferred_names=("index.php", "process.php", "submit.php", "handler.php", "form.php"),
    ) or _select_best_path(
        artefacts.get("php", []),
        reachable=reachable,
        preferred_names=("index.php", "process.php", "submit.php", "handler.php", "form.php"),
    )
    if backend_entrypoint:
        roles["backend_entrypoint"].append(backend_entrypoint)
        relevant_files["php"].append(backend_entrypoint)
        trace.append(
            {
                "role": "backend_entrypoint",
                "path": backend_entrypoint,
                "reason": "preferred reachable PHP entrypoint",
            }
        )
        for include_target in _expand_connected_targets(
            backend_entrypoint,
            relation_lookup=relation_lookup,
            relation_names={"php_include"},
        ):
            if include_target not in relevant_files["php"]:
                relevant_files["php"].append(include_target)
                trace.append(
                    {
                        "role": "backend_supporting_php",
                        "path": include_target,
                        "reason": "included by selected backend entrypoint",
                    }
                )

    database_schema = _select_best_path(
        candidate_execution_map.get("database_schema", []),
        reachable=reachable,
        preferred_names=("database.sql", "schema.sql", "db.sql", "seed.sql"),
    ) or _select_best_path(
        artefacts.get("sql", []),
        reachable=reachable,
        preferred_names=("database.sql", "schema.sql", "db.sql", "seed.sql"),
    )
    if database_schema:
        roles["database_schema_file"].append(database_schema)
        relevant_files["sql"].append(database_schema)
        trace.append(
            {
                "role": "database_schema_file",
                "path": database_schema,
                "reason": "preferred database/schema artefact",
            }
        )

    api_candidates = sorted(
        set(candidate_execution_map.get("api_client_code", []))
        | set(candidate_execution_map.get("api_backend", []))
    )
    for api_candidate in api_candidates:
        roles["api_client_code"].append(api_candidate)
        relevant_files["api"].append(api_candidate)
        trace.append(
            {
                "role": "api_client_code",
                "path": api_candidate,
                "reason": "API usage hint detected",
            }
        )

    for component in ("html", "css", "js", "php", "sql"):
        if not relevant_files.get(component):
            relevant_files[component] = []
        else:
            relevant_files[component] = list(dict.fromkeys(sorted(relevant_files[component])))

    return RoleMappedSubmission(
        roles={key: list(value) for key, value in roles.items()},
        relevant_files={key: list(value) for key, value in relevant_files.items()},
        selection_trace=trace,
    )


def _targets_for_sources(
    *,
    sources: Sequence[str],
    relation_lookup: Mapping[str, Sequence[ArtefactRelation]],
    relation_name: str,
) -> List[str]:
    """Return for sources."""
    targets: List[str] = []
    for source in sources:
        for relation in relation_lookup.get(source, []):
            if relation.relation == relation_name:
                targets.append(relation.target)
    return list(dict.fromkeys(targets))


def _expand_connected_targets(
    source: str,
    *,
    relation_lookup: Mapping[str, Sequence[ArtefactRelation]],
    relation_names: set[str],
) -> List[str]:
    """Return connected targets."""
    queue = deque([source])
    visited: set[str] = set()
    discovered: List[str] = []
    while queue:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        for relation in relation_lookup.get(current, []):
            if relation.relation not in relation_names:
                continue
            if relation.target not in visited:
                discovered.append(relation.target)
                queue.append(relation.target)
    return list(dict.fromkeys(discovered))


def _select_best_path(
    paths: Iterable[str],
    *,
    reachable: set[str],
    preferred_names: Sequence[str],
) -> str | None:
    """Select the best path."""
    candidates = [path for path in paths if path and not _is_backup_file(Path(path))]
    if not candidates:
        return None
    for preferred in preferred_names:
        for path in candidates:
            if Path(path).name.lower() == preferred.lower() and (not reachable or path in reachable):
                return path
    for path in candidates:
        if not reachable or path in reachable:
            return path
    return sorted(candidates)[0]


def _select_relevant_group(
    paths: Iterable[str],
    *,
    linked: Sequence[str],
    reachable: set[str],
    preferred_names: Sequence[str],
) -> List[str]:
    """Select the relevant group."""
    preferred_group = [
        path
        for path in linked
        if path and not _is_backup_file(Path(path))
    ]
    if preferred_group:
        return list(dict.fromkeys(sorted(preferred_group)))

    reachable_group = [
        path
        for path in paths
        if path in reachable and not _is_backup_file(Path(path))
    ]
    if reachable_group:
        return list(dict.fromkeys(sorted(reachable_group)))

    best = _select_best_path(paths, reachable=set(), preferred_names=preferred_names)
    return [best] if best else []


def _is_backup_file(path: Path) -> bool:
    """Return backup file."""
    return bool(_BACKUP_NAME_RE.search(path.stem) or _BACKUP_NAME_RE.search(path.name))


def _looks_like_api_reference(value: str) -> bool:
    """Return like api reference."""
    lowered = (value or "").lower()
    return lowered.startswith("/api") or "/api/" in lowered or lowered.startswith("http")


def _looks_like_php_api_endpoint(content: str, filename: str) -> bool:
    """Return like php api endpoint."""
    lowered = content.lower()
    return (
        "json_encode(" in lowered
        or "application/json" in lowered
        or filename.lower() in {"api.php", "endpoint.php", "service.php", "data.php"}
    )


__all__ = ["build_submission_evidence"]
