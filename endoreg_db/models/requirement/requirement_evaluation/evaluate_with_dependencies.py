from collections import defaultdict, deque
from typing import Iterable, Dict, List, Literal, Tuple
from endoreg_db.models.requirement import Requirement, RequirementSet
from endoreg_db.models.requirement.requirement_error import RequirementEvaluationError
import logging

logger = logging.getLogger(__name__)

RequirementStatus = Literal["PASSED", "FAILED", "BLOCKED", "ERROR"]


def topologically_sort_requirement_sets(
    sets: Iterable[RequirementSet],
) -> List[RequirementSet]:
    """
    Topologically sort requirement sets by `depends_on`.

    Raises ValueError if a cycle is detected.
    """
    sets = list(sets)
    id_map = {s.id: s for s in sets}

    depends = {s.id: {d.id for d in s.depends_on.all()} for s in sets}
    indegree = defaultdict(int)
    for set_id, deps in depends.items():
        for d_id in deps:
            indegree[set_id] += 1

    queue = deque([sid for sid in id_map if indegree[sid] == 0])
    ordered_ids: List[int] = []

    while queue:
        sid = queue.popleft()
        ordered_ids.append(sid)
        for other_id, deps in depends.items():
            if sid in deps:
                deps.remove(sid)
                indegree[other_id] -= 1
                if indegree[other_id] == 0:
                    queue.append(other_id)

    if len(ordered_ids) != len(sets):
        raise ValueError("RequirementSet dependency cycle detected.")

    return [id_map[sid] for sid in ordered_ids]


def evaluate_requirement_sets_with_dependencies(
    sets: Iterable[RequirementSet],
    *args,
    mode: str = "strict",
    **kwargs,
) -> Dict[int, Dict[int, Tuple[RequirementStatus, str]]]:
    """
    Evaluates RequirementSets with set-level `depends_on`.

    Returns:
        {
          set_id: {
            requirement_id: (status, details)
          }
        }
    """
    ordered_sets = topologically_sort_requirement_sets(sets)
    set_results: Dict[int, Dict[int, Tuple[RequirementStatus, str]]] = {}
    set_statuses: Dict[int, RequirementStatus] = {}

    for req_set in ordered_sets:
        set_id = req_set.id
        dep_set_ids = [s.id for s in req_set.depends_on.all()]

        # Any dependency set FAILED or ERROR → this set is BLOCKED
        blocking_deps = [
            (dep_id, set_statuses[dep_id])
            for dep_id in dep_set_ids
            if dep_id in set_statuses and set_statuses[dep_id] in ("FAILED", "ERROR")
        ]

        reqs = list(req_set.requirements.all())
        set_results[set_id] = {}

        if blocking_deps:
            failed_names = ", ".join(
                str(RequirementSet.objects.get(pk=dep_id).name)
                for dep_id, _ in blocking_deps
            )
            msg = (
                f"Die Voraussetzungengruppe '{req_set.name}' wurde nicht geprüft, "
                f"weil die abhängige(n) Gruppe(n) {failed_names} "
                "noch nicht erfüllt sind."
            )
            for req in reqs:
                set_results[set_id][req.id] = ("BLOCKED", msg)
            set_statuses[set_id] = "BLOCKED"
            continue

        # Dependencies only BLOCKED → also BLOCKED (design choice)
        blocked_only = [
            (dep_id, set_statuses[dep_id])
            for dep_id in dep_set_ids
            if dep_id in set_statuses and set_statuses[dep_id] == "BLOCKED"
        ]
        if blocked_only and not blocking_deps:
            msg = (
                f"Die Voraussetzungengruppe '{req_set.name}' wurde nicht geprüft, "
                "weil abhängige Gruppen noch ausstehend sind."
            )
            for req in reqs:
                set_results[set_id][req.id] = ("BLOCKED", msg)
            set_statuses[set_id] = "BLOCKED"
            continue

        # All deps PASSED or no deps → evaluate each requirement
        had_failed = False
        had_error = False

        for req in reqs:
            try:
                met, details = req.evaluate_with_details(*args, mode=mode, **kwargs)
                status: RequirementStatus = "PASSED" if met else "FAILED"
                if status == "FAILED":
                    had_failed = True
                set_results[set_id][req.id] = (status, details)

            except RequirementEvaluationError as exc:
                # domain-level failure → treat as FAILED but with nice message
                had_failed = True
                msg = (
                    f"Fehler bei der Auswertung der Voraussetzung '{req.name}': "
                    f"{exc.code}: {exc.technical_message}"
                )
                logger.warning(
                    "RequirementSet %s / Requirement %s domain error: %s",
                    req_set.id,
                    req.id,
                    exc,
                )
                set_results[set_id][req.id] = ("FAILED", msg)

            except Exception as exc:
                had_error = True
                logger.exception(
                    "Unexpected error while evaluating RequirementSet %s / Requirement %s",
                    req_set.id,
                    req.id,
                )
                msg = f"Technischer Fehler bei der Auswertung von '{req.name}': {exc}"
                set_results[set_id][req.id] = ("ERROR", msg)

        # derive set status
        if had_failed:
            set_statuses[set_id] = "FAILED"
        elif had_error:
            set_statuses[set_id] = "ERROR"
        else:
            # if no requirements → call it PASSED (vacuous truth), or adjust as you like
            set_statuses[set_id] = "PASSED"

    return set_results


# TODO Remove when sure that no per requirement evaluation will happen. Needs the depends on attribute for the reatuirements.


def topologically_sort_requirements(
    requirements: Iterable[Requirement],
) -> List[Requirement]:
    """
    Topologically sort requirements by `depends_on`.

    Raises ValueError if a cycle is detected.
    """
    reqs = list(requirements)
    id_map = {r.id: r for r in reqs}

    # Build adjacency + indegree for Kahn's algorithm
    depends = {r.id: {d.id for d in r.depends_on.all()} for r in reqs}
    indegree = defaultdict(int)
    for r_id, deps in depends.items():
        for d_id in deps:
            indegree[r_id] += 1

    queue = deque([r_id for r_id in id_map if indegree[r_id] == 0])
    ordered_ids: List[int] = []

    while queue:
        r_id = queue.popleft()
        ordered_ids.append(r_id)
        # remove this node as dep from others
        for other_id, deps in depends.items():
            if r_id in deps:
                deps.remove(r_id)
                indegree[other_id] -= 1
                if indegree[other_id] == 0:
                    queue.append(other_id)

    if len(ordered_ids) != len(reqs):
        raise ValueError("Requirement dependency cycle detected.")

    return [id_map[rid] for rid in ordered_ids]


def evaluate_requirements_with_dependencies(
    requirements: Iterable[Requirement],
    *args,
    mode: str = "strict",
    **kwargs,
) -> Dict[int, Tuple[RequirementStatus, str]]:
    ordered = topologically_sort_requirements(requirements)
    results: Dict[int, Tuple[RequirementStatus, str]] = {}

    for req in ordered:
        dep_ids = [d.id for d in req.depends_on.all()]

        # If any dependency FAILED or ERROR → BLOCKED
        blocking = [
            (dep_id, results[dep_id])
            for dep_id in dep_ids
            if dep_id in results and results[dep_id][0] in ("FAILED", "ERROR")
        ]

        if blocking:
            failed_names = ", ".join(
                str(Requirement.objects.get(pk=dep_id).name) for dep_id, _ in blocking
            )
            results[req.id] = (
                "BLOCKED",
                f"Die Voraussetzung '{req.name}' wurde nicht geprüft, "
                f"weil die abhängige(n) Voraussetzung(en) {failed_names} "
                "noch nicht erfüllt sind.",
            )
            continue

        # If dependencies exist but are only BLOCKED → still BLOCKED (design choice)
        blocked_only = [
            (dep_id, results[dep_id])
            for dep_id in dep_ids
            if dep_id in results and results[dep_id][0] == "BLOCKED"
        ]
        if blocked_only and not blocking:
            results[req.id] = (
                "BLOCKED",
                f"Die Voraussetzung '{req.name}' wurde nicht geprüft, "
                "weil abhängige Voraussetzungen noch ausstehend sind.",
            )
            continue

        # All deps passed (or none) → evaluate requirement itself
        try:
            met, details = req.evaluate_with_details(*args, mode=mode, **kwargs)
        except RequirementEvaluationError as exc:
            met = False
            results[req.id] = (
                "FAILED",
                f"Nachtragebedarf bei der Auswertung von '{req.name}': {exc}",
            )
        except Exception as exc:
            met = False
            logger.exception("Unexpected error while evaluating requirement %s", req)
            results[req.id] = (
                "ERROR",
                f"Technischer Fehler bei der Auswertung von '{req.name}': {exc}",
            )
        else:
            status: RequirementStatus = "PASSED" if met else "FAILED"
            results[req.id] = (status, details)

    return results
