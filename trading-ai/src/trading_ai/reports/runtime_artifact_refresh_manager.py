"""
Orchestrates conditional refresh of runtime truth artifacts — dependency fingerprints, no blind polling.

Does not place orders, mutate GATE_B_LIVE_EXECUTION_ENABLED, or clear operating_mode_state files.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from trading_ai.reports.runtime_artifact_registry import REGISTRY, ArtifactSpec, registry_dependency_graph
from trading_ai.runtime_paths import ezras_runtime_root

STATE_FILENAME = "runtime_artifact_refresh_state.json"
REFRESH_TRUTH = "runtime_artifact_refresh_truth.json"


def _fingerprint_file(path: Path, *, max_sha_bytes: int = 400_000) -> Dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "exists": False}
    try:
        st = path.stat()
        base: Dict[str, Any] = {
            "path": str(path),
            "exists": True,
            "size": st.st_size,
            "mtime_ns": st.st_mtime_ns,
        }
        if st.st_size <= max_sha_bytes:
            data = path.read_bytes()
            base["sha256_16"] = hashlib.sha256(data).hexdigest()[:16]
        else:
            with path.open("rb") as f:
                chunk = f.read(65536)
            base["sha256_16_prefix64k"] = hashlib.sha256(chunk).hexdigest()[:16]
            base["large_file"] = True
        if path.suffix == ".json":
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    base["semantic_switch"] = {
                        "gate_b_can_be_switched_live_now": raw.get("gate_b_can_be_switched_live_now"),
                        "mode": raw.get("mode") or raw.get("persisted_mode"),
                        "FINAL_EXECUTION_PROVEN": raw.get("FINAL_EXECUTION_PROVEN"),
                        "tick_ok": raw.get("tick_ok"),
                    }
            except (OSError, json.JSONDecodeError):
                base["semantic_error"] = True
        return base
    except OSError as e:
        return {"path": str(path), "exists": False, "error": str(e)}


def fingerprint_dependency_set(paths: Sequence[Path]) -> Dict[str, Any]:
    """Composite fingerprint for staleness — order-stable."""
    fps = []
    for p in sorted(set(paths), key=lambda x: str(x)):
        fps.append(_fingerprint_file(p))
    return {"paths": fps, "combined_sha16": hashlib.sha256(json.dumps(fps, sort_keys=True).encode()).hexdigest()[:16]}


def _load_state(ctrl: Path) -> Dict[str, Any]:
    p = ctrl / STATE_FILENAME
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(ctrl: Path, state: Dict[str, Any]) -> None:
    ctrl.mkdir(parents=True, exist_ok=True)
    (ctrl / STATE_FILENAME).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _resolve_writer(spec: ArtifactSpec) -> Callable[..., Any]:
    mod_name, _, fn_name = spec.writer.partition(":")
    mod = importlib.import_module(mod_name)
    return getattr(mod, fn_name)


def _normalize_writer_result(spec: ArtifactSpec, raw: Any, root: Path) -> Dict[str, Any]:
    if isinstance(raw, dict) and raw.get("artifact_name"):
        out = dict(raw)
        if not out.get("path_json") and spec.primary_output_json:
            out["path_json"] = str(root / spec.primary_output_json)
        return out
    path_json = ""
    if isinstance(raw, dict):
        path_json = str(raw.get("path") or raw.get("path_json") or "")
    if not path_json and spec.primary_output_json:
        path_json = str(root / spec.primary_output_json)
    return {
        "artifact_name": spec.id,
        "path_json": path_json,
        "path_txt": "",
        "written": bool(path_json),
        "skipped_as_fresh": False,
        "freshness_basis": {},
        "dependencies_checked": [],
        "inputs_missing": [],
        "truth_level": spec.truth_level,
        "notes": [spec.notes] if spec.notes else [],
    }


def _artifact_stale(
    spec: ArtifactSpec,
    root: Path,
    state: Dict[str, Any],
    *,
    force: bool,
) -> Tuple[bool, Dict[str, Any], List[Path]]:
    paths = [p for p in spec.dependency_paths(root) if p is not None]
    fp = fingerprint_dependency_set(paths)
    prev = (state.get("artifacts") or {}).get(spec.id) or {}
    prev_fp = prev.get("dep_fingerprint_combined")
    stale = force or prev_fp != fp.get("combined_sha16") or not prev_fp
    return stale, fp, paths


def run_refresh_runtime_artifacts(
    *,
    runtime_root: Optional[Path] = None,
    force: bool = False,
    show_stale_only: bool = False,
    include_advisory: bool = True,
    print_final_switch_truth: bool = False,
) -> Dict[str, Any]:
    root = Path(runtime_root or ezras_runtime_root()).resolve()
    ctrl = root / "data" / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    state = _load_state(ctrl)

    refreshed: List[str] = []
    skipped: List[str] = []
    failures: List[Dict[str, Any]] = []
    writer_results: List[Dict[str, Any]] = []
    stale_detected: List[str] = []

    if "artifacts" not in state:
        state["artifacts"] = {}

    if show_stale_only:
        for spec in REGISTRY:
            stale, fp, _ = _artifact_stale(spec, root, state, force=False)
            if stale:
                stale_detected.append(spec.id)
            writer_results.append(
                {
                    "artifact_name": spec.id,
                    "stale": stale,
                    "dependency_fingerprint": fp.get("combined_sha16"),
                }
            )
        return {
            "truth_version": "runtime_artifact_refresh_truth_v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "runtime_root": str(root),
            "mode": "show_stale_only",
            "stale_artifact_ids": stale_detected,
            "writer_results": writer_results,
            "honesty": "No artifacts were written — inspection only.",
        }

    for spec in REGISTRY:
        if not include_advisory and spec.truth_level == "advisory":
            writer_results.append(
                _normalize_writer_result(
                    spec,
                    {"artifact_name": spec.id, "skipped_as_fresh": True, "notes": ["excluded_by_include_advisory_false"]},
                    root,
                )
            )
            skipped.append(spec.id)
            continue

        stale, fp, dep_paths = _artifact_stale(spec, root, state, force=force)
        if stale:
            stale_detected.append(spec.id)

        if not stale and not force:
            skipped.append(spec.id)
            writer_results.append(
                {
                    "artifact_name": spec.id,
                    "path_json": (state.get("artifacts") or {}).get(spec.id, {}).get("path_json", ""),
                    "written": False,
                    "skipped_as_fresh": True,
                    "freshness_basis": {"dep_fingerprint_unchanged": fp.get("combined_sha16")},
                    "dependencies_checked": [str(p) for p in dep_paths],
                    "truth_level": spec.truth_level,
                }
            )
            continue

        try:
            fn = _resolve_writer(spec)
            raw_out = fn(runtime_root=root)
            norm = _normalize_writer_result(spec, raw_out, root)
            norm["written"] = True
            norm["skipped_as_fresh"] = False
            norm["dependencies_checked"] = [str(p) for p in dep_paths]
            norm["freshness_basis"] = {"dep_fingerprint_after_write": fp.get("combined_sha16")}
            writer_results.append(norm)
            refreshed.append(spec.id)
            state["artifacts"][spec.id] = {
                "dep_fingerprint_combined": fp.get("combined_sha16"),
                "path_json": norm.get("path_json", ""),
                "last_refresh_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as exc:
            failures.append({"artifact": spec.id, "error": str(exc)})
            writer_results.append(
                {
                    "artifact_name": spec.id,
                    "written": False,
                    "skipped_as_fresh": False,
                    "notes": [f"refresh_failure:{exc}"],
                    "truth_level": spec.truth_level,
                }
            )

    _save_state(ctrl, state)

    final_switch = {}
    fin_path = ctrl / "gate_b_final_go_live_truth.json"
    if fin_path.is_file():
        try:
            final_switch = json.loads(fin_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            final_switch = {}

    truth_payload = {
        "truth_version": "runtime_artifact_refresh_truth_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "refresh_reason": "force" if force else ("initial_or_dependency_change" if refreshed else "all_fresh"),
        "artifacts_refreshed": refreshed,
        "artifacts_skipped_as_fresh": skipped,
        "stale_artifacts_detected": stale_detected,
        "refresh_failures": failures,
        "dependency_chain_used": [s.id for s in REGISTRY],
        "registry_dependency_graph": registry_dependency_graph(),
        "authoritative_switch_artifact": str(ctrl / "gate_b_final_go_live_truth.json"),
        "remaining_gaps_artifact": str(ctrl / "gate_b_remaining_gaps_final.json"),
        "refresh_complete_and_trustworthy": len(failures) == 0,
        "gate_b_can_be_switched_live_now": final_switch.get("gate_b_can_be_switched_live_now"),
        "honesty": (
            "Refresh is driven by dependency fingerprint changes — not wall-clock. "
            "Failures mean some artifacts may be stale; fix errors and re-run."
        ),
        "writer_results": writer_results,
    }
    (ctrl / REFRESH_TRUTH).write_text(json.dumps(truth_payload, indent=2, default=str) + "\n", encoding="utf-8")
    (ctrl / "runtime_artifact_refresh_truth.txt").write_text(json.dumps(truth_payload, indent=2)[:20000] + "\n", encoding="utf-8")

    if print_final_switch_truth:
        truth_payload["printed_final_switch_truth"] = {
            "gate_b_can_be_switched_live_now": final_switch.get("gate_b_can_be_switched_live_now"),
            "if_false_exact_why": final_switch.get("if_false_exact_why"),
        }

    return truth_payload
