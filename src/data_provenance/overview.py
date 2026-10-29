"""Project reviewed provenance onto an overview while preserving dependency paths and evidence strength."""

from collections import defaultdict
from hashlib import sha256
from itertools import combinations


DEPENDENCY_TYPES = {"direct_ingestion", "calibration"}


def reachability(node_ids, pairs):
    """Return reflexive directed reachability, including graphs with cycles."""
    adjacency = {nid: set() for nid in node_ids}
    for upstream, downstream in pairs:
        adjacency[upstream].add(downstream)
    result = {}
    for start in adjacency:
        seen, pending = {start}, [start]
        while pending:
            for target in adjacency[pending.pop()] - seen:
                seen.add(target)
                pending.append(target)
        result[start] = seen
    return result


def project_overview(evidence, include_validation=False):
    """Aggregate boundary records without promoting their evidence strength."""
    nodes = {n["node_id"]: n for n in evidence["nodes"]}
    edges = {e["edge_id"]: e for e in evidence["edges"]}
    spec = evidence["overview"]
    groups = {g["node_id"]: g for g in spec["groups"]}
    membership = {}
    for gid, group in groups.items():
        if not group["member_node_ids"]:
            raise ValueError(f"Empty overview group: {gid}")
        for nid in group["member_node_ids"]:
            if nid not in nodes or nid in membership:
                raise ValueError(f"Invalid or duplicate overview membership: {nid}")
            membership[nid] = gid
    if set(membership) != set(nodes):
        raise ValueError("Overview membership must cover every audited node")
    for nid, node in nodes.items():
        if node.get("selected") and groups[membership[nid]]["member_node_ids"] != [nid]:
            raise ValueError("Selected products must remain separate")
    active = {
        gid: g for gid, g in groups.items()
        if include_validation or g["kind"] != "validation"
    }
    internal = defaultdict(list)
    boundary = defaultdict(list)
    excluded = []
    for eid, edge in edges.items():
        up, down = membership[edge["upstream_id"]], membership[edge["downstream_id"]]
        if up not in active or down not in active:
            excluded.append(eid)
        elif up == down and edge["upstream_id"] != edge["downstream_id"]:
            internal[up].append(eid)
        else:
            boundary[(up, down, edge["relationship_type"])].append(eid)
    view_nodes = {}
    for gid, group in active.items():
        members = group["member_node_ids"]
        unresolved = [nid for nid in members if nodes[nid]["version_status"] == "unresolved_version"]
        view_nodes[gid] = {
            **group, "selected": group["kind"] == "selected",
            "version_status": "unresolved_version" if unresolved else "resolved_named_version",
            "unresolved_version_member_ids": unresolved,
            "member_count": len(members), "internal_edge_ids": sorted(internal[gid]),
            "member_versions": {nid: nodes[nid]["versions"] for nid in members},
            "lineage_boundaries": {nid: nodes[nid]["lineage_boundary"] for nid in members},
            "source_ids": sorted({sid for nid in members for sid in nodes[nid]["source_ids"]}),
            "evidence_refs": [f"edge_evidence.json#node:{nid}" for nid in members],
        }
    view_edges = {}
    for (up, down, relationship), boundary_ids in sorted(boundary.items()):
        # Every internal segment of either endpoint container is included
        # conservatively. A verified boundary cannot upgrade a hidden segment.
        is_loop = up == down
        supporting_ids = set(boundary_ids)
        if not is_loop:
            supporting_ids.update(internal[up])
            supporting_ids.update(internal[down])
        supporting = [edges[eid] for eid in sorted(supporting_ids)]
        acquisition_ids = [e["edge_id"] for e in supporting if e["evidence_level"] == "acquisition_processing_record"]
        all_acquisition = len(acquisition_ids) == len(supporting)
        verified = relationship in DEPENDENCY_TYPES and all(e["verified_dependency"] for e in supporting)
        summary = len(boundary_ids) > 1 or any(len(active[n]["member_node_ids"]) > 1 for n in (up, down))
        eid = "OV-" + sha256(f"{up}\n{down}\n{relationship}".encode()).hexdigest()[:16]
        view_edges[eid] = {
            "edge_id": eid, "upstream_id": up, "downstream_id": down,
            "relationship_type": "dependency_summary" if summary else relationship,
            "boundary_relationship_type": relationship, "is_summary": summary,
            "boundary_edge_ids": sorted(boundary_ids),
            "boundary_member_endpoints": [
                {"edge_id": bid, "upstream_id": edges[bid]["upstream_id"],
                 "downstream_id": edges[bid]["downstream_id"]} for bid in sorted(boundary_ids)
            ],
            "supporting_edge_ids": sorted(supporting_ids),
            "evidence_refs": [f"edge_evidence.json#{sid}" for sid in sorted(supporting_ids)],
            "source_ids": sorted({r["source_id"] for e in supporting for r in e["source_references"]}),
            "verified_dependency": verified,
            "evidence_level": "documented_validation" if relationship == "validation" else "acquisition_processing_record" if all_acquisition else (
                "mixed_with_acquisition_records" if acquisition_ids else "documented_product_dependency"
            ),
            "acquisition_edge_ids": acquisition_ids,
            "unresolved_edge_ids": [e["edge_id"] for e in supporting
                                    if e["relationship_type"] in DEPENDENCY_TYPES and not e["verified_dependency"]],
            "dependency_verification_scope": "documented_product_or_algorithm_family",
            "applicability_by_edge": {
                e["edge_id"]: {k: e[k] for k in ("version_applicability", "temporal_applicability", "geographic_applicability", "acquisition_verification")}
                for e in supporting
            },
            "claim_scope": "Boundary connections between named members; internal segments are elided. Not an all-to-all relationship between members or a new direct-ingestion claim.",
        }
        if is_loop:
            view_edges[eid].update(
                temporal_dependency=True,
                temporal_annotation=" | ".join(edges[bid]["temporal_annotation"] for bid in sorted(boundary_ids)),
            )
    view = {"nodes": view_nodes, "edges": view_edges, "membership": membership,
            "internal_edge_ids": dict(internal), "excluded_edge_ids": sorted(excluded)}
    view["validation"] = validate_overview(evidence, view)
    return view


def validate_overview(evidence, view):
    """Reject lost/false paths, hidden shared ancestry, or stronger evidence."""
    nodes = {n["node_id"]: n for n in evidence["nodes"]}
    edges = {e["edge_id"]: e for e in evidence["edges"]}
    groups, membership = view["nodes"], view["membership"]
    dependency_edges = [e for e in edges.values() if e["relationship_type"] in DEPENDENCY_TYPES]
    full = reachability(nodes, [(e["upstream_id"], e["downstream_id"]) for e in dependency_edges])
    projected = reachability(groups, [
        (e["upstream_id"], e["downstream_id"]) for e in view["edges"].values()
        if e["boundary_relationship_type"] in DEPENDENCY_TYPES
    ])
    selected = sorted(nid for nid, n in nodes.items() if n.get("selected"))
    expected_anchors = set(evidence["overview"]["protected_product_ids"])
    expected_anchors.update(g["anchor_node_id"] for g in groups.values() if g.get("anchor_node_id"))
    if not expected_anchors <= nodes.keys():
        raise ValueError("Unknown protected product")
    anchors = sorted(expected_anchors)
    for upstream in anchors:
        for downstream in anchors:
            if (downstream in full[upstream]) != (membership[downstream] in projected[membership[upstream]]):
                raise ValueError(f"Lost or invented product dependency: {upstream} -> {downstream}")
    # Even unanchored bundles may not create a path without an original witness.
    for source, source_group in groups.items():
        for target, target_group in groups.items():
            original = any(
                b in full[a] for a in source_group["member_node_ids"]
                for b in target_group["member_node_ids"]
            )
            if original != (target in projected[source]):
                raise ValueError(f"Unliftable overview path: {source} -> {target}")
    shared = []
    for nid, downstream in full.items():
        targets = set(selected) & downstream
        families = {"NLDAS" if t.startswith("NLDAS-") else t for t in targets}
        if len(families) > 1:
            gid = membership[nid]
            if gid not in groups or targets != set(selected) & projected[gid]:
                raise ValueError(f"Shared ancestor hidden or broadened: {nid}")
            shared.append({"node_id": nid, "overview_node_id": gid, "selected_descendants": sorted(targets)})
    common_pairs = []
    for a, b in combinations(selected, 2):
        original = [n for n in nodes if a in full[n] and b in full[n]]
        simplified = [n for n in groups if a in projected[n] and b in projected[n]]
        if bool(original) != bool(simplified):
            raise ValueError(f"Changed shared ancestry: {a}, {b}")
        common_pairs.append({"products": [a, b], "shared_ancestor_ids": original, "overview_ancestor_ids": simplified})
    accounted = set(view["excluded_edge_ids"])
    for ids in view["internal_edge_ids"].values():
        accounted.update(ids)
    for edge in view["edges"].values():
        ids = edge["supporting_edge_ids"]
        if not set(ids) <= edges.keys() or not set(edge["boundary_edge_ids"]) <= set(ids):
            raise ValueError("Summary evidence reference does not resolve")
        expected_support = set(edge["boundary_edge_ids"])
        if edge["upstream_id"] != edge["downstream_id"]:
            for endpoint in (edge["upstream_id"], edge["downstream_id"]):
                expected_support.update(view["internal_edge_ids"].get(endpoint, []))
        if set(ids) != expected_support:
            raise ValueError("Summary dropped or invented an internal evidence segment")
        accounted.update(edge["boundary_edge_ids"])
        if edge["verified_dependency"] and not all(edges[eid]["verified_dependency"] for eid in ids):
            raise ValueError("Aggregation upgraded unresolved evidence")
        expected_unresolved = {eid for eid in ids if edges[eid]["relationship_type"] in DEPENDENCY_TYPES
                               and not edges[eid]["verified_dependency"]}
        if set(edge["unresolved_edge_ids"]) != expected_unresolved:
            raise ValueError("Summary misclassified dependency uncertainty")
        if edge["evidence_level"] == "acquisition_processing_record" and not all(
            edges[eid]["evidence_level"] == "acquisition_processing_record" for eid in ids
        ):
            raise ValueError("Aggregation upgraded acquisition evidence")
        if any(edges[eid]["relationship_type"] != edge["boundary_relationship_type"] for eid in edge["boundary_edge_ids"]):
            raise ValueError("Aggregation mixed relationship types")
        if edge["upstream_id"] == edge["downstream_id"] and not edge.get("temporal_annotation"):
            raise ValueError("Summary loop lost temporal annotation")
    if accounted != set(edges):
        raise ValueError("Some original relationships have no representation")
    for eid in view["excluded_edge_ids"]:
        edge = edges[eid]
        if edge["relationship_type"] in DEPENDENCY_TYPES and set(selected) & full[edge["downstream_id"]]:
            raise ValueError("Optional layer omitted a product dependency")
    return {
        "status": "passed", "protected_product_count": len(anchors),
        "protected_ordered_pairs_checked": len(anchors) ** 2,
        "group_ordered_pairs_checked": len(groups) ** 2,
        "shared_ancestors": shared, "selected_product_pairs": common_pairs,
        "original_edges_accounted_for": len(accounted),
        "checks": ["Exact protected-product reachability", "Every reachable group pair has an original path witness",
                   "All shared ancestors retain exact selected descendants", "All selected-product pairs retain shared ancestry",
                   "All original edges map to boundaries, interiors or optional comparisons", "Evidence strength never increases",
                   "Boundary relationship types remain distinct", "Temporal annotations preserved"],
    }
