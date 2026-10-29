"""Validate reviewed evidence and build overview GraphML in drawing coordinates."""

import json
import textwrap
import xml.etree.ElementTree as ET

from data_provenance.overview import project_overview


GRAPHML_NS = "http://graphml.graphdrawing.org/xmlns"
YED_NS = "http://www.yworks.com/xml/graphml"
LABEL_FONT_SIZE = 16
LABEL_LINE_HEIGHT = 22
LABEL_PADDING = 24
RELATIONSHIPS = {
    "direct_ingestion", "calibration", "validation", "shared_ancestry",
    "coincident_measurement",
}


def index_unique(records, key):
    """Reject missing or duplicate identities before resolving references."""
    result = {}
    for record in records:
        identity = record[key]
        if not isinstance(identity, str) or not identity or identity in result:
            raise ValueError(f"Missing or duplicate {key}: {identity!r}")
        result[identity] = record
    return result


def validate_evidence(evidence, source_log):
    """Check explicit identities, source links, endpoints, and self-loop context."""
    nodes = index_unique(evidence["nodes"], "node_id")
    edges = index_unique(evidence["edges"], "edge_id")
    sources = index_unique(source_log["sources"], "source_id")
    for node in nodes.values():
        for key in ("product", "versions", "version_status", "lineage_boundary"):
            if key not in node:
                raise ValueError(f"Node {node['node_id']} lacks {key}")
        if not node["source_ids"]:
            raise ValueError(f"Node {node['node_id']} has no documentary source")
        for source_id in node["source_ids"]:
            if source_id not in sources:
                raise ValueError(f"Unknown node source: {source_id}")
    for edge in edges.values():
        eid = edge["edge_id"]
        if edge["upstream_id"] not in nodes or edge["downstream_id"] not in nodes:
            raise ValueError(f"Missing endpoint for {eid}")
        if edge["relationship_type"] not in RELATIONSHIPS:
            raise ValueError(f"Unknown relationship type for {eid}")
        for key in ("direct_use_claim", "evidence_level", "verification_status",
                    "version_applicability", "temporal_applicability",
                    "geographic_applicability", "acquisition_verification"):
            if not edge.get(key):
                raise ValueError(f"Edge {eid} lacks {key}")
        if not edge["source_references"]:
            raise ValueError(f"No evidence reference for {eid}")
        if edge.get("evidence_ref") != f"edge_evidence.json#{eid}":
            raise ValueError(f"Unresolved evidence reference for {eid}")
        if not isinstance(edge["verified_dependency"], bool):
            raise ValueError(f"Verification must be a boolean for {eid}")
        for ref in edge["source_references"]:
            if ref["source_id"] not in sources or not ref["location"]:
                raise ValueError(f"Unresolved source/location for {eid}")
        if edge["upstream_id"] == edge["downstream_id"]:
            if not edge.get("temporal_dependency") or not edge.get("temporal_annotation"):
                raise ValueError(f"Self-loop lacks temporal explanation: {eid}")
        if edge["verified_dependency"] and edge["relationship_type"] not in {
            "direct_ingestion", "calibration"
        }:
            raise ValueError(f"Non-dependency marked verified: {eid}")
        if (edge["relationship_type"] in {"direct_ingestion", "calibration"}
                and not edge["verified_dependency"]
                and not edge.get("unresolved_dependency_reason")):
            raise ValueError(f"Unresolved dependency needs a relationship-level reason: {eid}")
        if edge["verified_dependency"] and edge.get("unresolved_dependency_reason"):
            raise ValueError(f"Verified dependency has an unresolved relationship reason: {eid}")
        for ref in edge["source_references"]:
            if eid not in sources[ref["source_id"]]["edge_ids"]:
                raise ValueError(f"Source backlink missing for {eid}")
    for source in sources.values():
        for eid in source["edge_ids"]:
            if eid not in edges:
                raise ValueError(f"Unknown source edge backlink: {eid}")
            if source["source_id"] not in {
                ref["source_id"] for ref in edges[eid]["source_references"]
            }:
                raise ValueError(f"Incorrect source edge backlink: {eid}")
    proposals = index_unique(
        evidence.get("unsupported_proposed_relationships", []), "proposal_id"
    )
    for proposal in proposals.values():
        for source_id in proposal["source_ids"]:
            if source_id not in sources:
                raise ValueError(f"Unknown proposal source: {source_id}")
        for endpoint in ("upstream_id", "downstream_id"):
            if endpoint in proposal and proposal[endpoint] not in nodes:
                raise ValueError(f"Unknown proposal endpoint: {proposal[endpoint]}")
    for source in sources.values():
        expected_nodes = {
            nid for nid, node in nodes.items() if source["source_id"] in node["source_ids"]
        }
        expected_proposals = {
            pid for pid, proposal in proposals.items()
            if source["source_id"] in proposal["source_ids"]
        }
        if set(source.get("node_ids", [])) != expected_nodes:
            raise ValueError(f"Incorrect node backlinks: {source['source_id']}")
        if set(source.get("proposal_ids", [])) != expected_proposals:
            raise ValueError(f"Incorrect proposal backlinks: {source['source_id']}")
    return nodes, edges


def value_text(value):
    """Preserve nested native metadata as JSON within GraphML string attributes."""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def node_display(node):
    """Size short, wrapped labels; retain full identities in node properties."""
    label = node["label"] if "member_node_ids" in node else node.get("product", node["label"])
    lines = []
    for paragraph in label.splitlines():
        lines.extend(textwrap.wrap(paragraph, width=34, break_long_words=True, break_on_hyphens=False) or [""])
    return {"display_label": "\n".join(lines),
            "width": max(250, max(map(len, lines)) * 10 + 2 * LABEL_PADDING),
            "height": len(lines) * LABEL_LINE_HEIGHT + 2 * LABEL_PADDING}


def display_positions(nodes, edges, displays):
    """Provide a deterministic nonoverlapping fallback for new memberships."""
    downstreams = {nid: set() for nid in nodes}
    for edge in edges.values():
        if edge["upstream_id"] != edge["downstream_id"]:
            downstreams[edge["upstream_id"]].add(edge["downstream_id"])
    ranks = {}
    remaining = set(nodes)
    while remaining:
        ready = sorted(nid for nid in remaining if downstreams[nid] <= ranks.keys())
        if not ready:
            ready = sorted(remaining)
        next_ranks = {nid: 1 + max((ranks.get(p, -1) for p in downstreams[nid]), default=-1) for nid in ready}
        ranks.update(next_ranks)
        remaining.difference_update(ready)
    positions, x = {}, 0
    for rank in sorted(set(ranks.values()), reverse=True):
        members = sorted(nid for nid in nodes if ranks[nid] == rank)
        y = -sum(displays[nid]["height"] + 70 for nid in members) / 2
        for nid in members:
            positions[nid] = (x, y)
            y += displays[nid]["height"] + 70
        x += max(displays[nid]["width"] for nid in members) + 170
    return positions


def write_graph(nodes, edges, output_path, graph_attrs, layout=None):
    """Serialize the overview using yEd graphics."""
    temporal_dependencies = [edge for edge in edges.values()
                             if edge["upstream_id"] == edge["downstream_id"]]
    edges = {eid: edge for eid, edge in edges.items()
             if edge["upstream_id"] != edge["downstream_id"]}
    graph_attrs = {**graph_attrs, "temporal_dependencies": temporal_dependencies,
                   "displayed_edge_count": len(edges)}
    tag = lambda name: f"{{{GRAPHML_NS}}}{name}"
    ytag = lambda name: f"{{{YED_NS}}}{name}"
    ET.register_namespace("", GRAPHML_NS)
    ET.register_namespace("y", YED_NS)
    records = dict(nodes)
    children = {}
    roots = list(nodes)
    displays = {nid: node_display(node) for nid, node in records.items()}
    positions = display_positions(nodes, edges, {nid: displays[nid] for nid in nodes})
    if layout:
        positions.update({nid: (box["x"], box["y"]) for nid, box in layout.get("nodes", {}).items() if nid in records})
    expanded = {}
    # A visual frame keeps the three distinct selected NLDAS models together.
    nldas = [nid for nid in roots if nid in nodes and nodes[nid].get("selected") and nid.startswith("NLDAS-")]
    if len(nldas) == 3:
        fid = "FRAME-NLDAS-MODELS"
        records[fid] = {"node_id": fid, "label": "NLDAS land models", "member_node_ids": [], "kind": "visual_frame"}
        displays[fid] = node_display(records[fid])
        left = min(positions[n][0] for n in nldas) - 25
        top = min(positions[n][1] for n in nldas) - 50
        right = max(positions[n][0] + displays[n]["width"] for n in nldas) + 25
        bottom = max(positions[n][1] + displays[n]["height"] for n in nldas) + 25
        positions[fid] = (left, top)
        expanded[fid] = (left, top, right-left, bottom-top)
        children[fid] = nldas
        roots = [n for n in roots if n not in nldas] + [fid]
    display_nodes = {nid: {**node, **displays[nid]} for nid, node in records.items()}
    root = ET.Element(tag("graphml"))
    node_fields = sorted(set().union(*(set(n) for n in display_nodes.values())) - {"node_id"})
    edge_fields = sorted(set().union(*(set(e) for e in edges.values())) - {"edge_id", "upstream_id", "downstream_id"}) if edges else []
    for domain, fields in (("graph", sorted(graph_attrs)), ("node", node_fields), ("edge", edge_fields)):
        for field in fields:
            ET.SubElement(root, tag("key"), {"id": f"{domain}_{field}", "for": domain,
                "attr.name": field, "attr.type": "double" if domain == "node" and field in {"width", "height"} else "string"})
    for domain in ("node", "edge"):
        ET.SubElement(root, tag("key"), {"id": f"{domain}_graphics", "for": domain, "yfiles.type": f"{domain}graphics"})
    graph = ET.SubElement(root, tag("graph"), id="data_provenance", edgedefault="directed")

    def add_data(element, domain, record, fields):
        for field in fields:
            if field in record:
                ET.SubElement(element, tag("data"), key=f"{domain}_{field}").text = value_text(record[field])

    def shape_graphics(parent, nid, group=False, closed=False):
        node = display_nodes[nid]
        shape = ET.SubElement(parent, ytag("GroupNode" if group else "ShapeNode"))
        x, y = positions.get(nid, (0, 0))
        w, h = node["width"], node["height"]
        if group and not closed:
            x, y, w, h = expanded[nid]
        ET.SubElement(shape, ytag("Geometry"), x=str(x), y=str(y), width=str(w), height=str(h))
        color = "#F8FAFC"
        if node.get("kind", "").startswith("shared"):
            color = "#DCFCE7"
        if node.get("kind") == "visual_frame":
            color = "#F8FAFC"
        ET.SubElement(shape, ytag("Fill"), color=color, transparent="false")
        ET.SubElement(shape, ytag("BorderStyle"), color="#64748B", type="line", width="1.0")
        ET.SubElement(shape, ytag("NodeLabel"), {
            "alignment": "center", "autoSizePolicy": "content", "fontFamily": "Arial",
            "fontSize": str(LABEL_FONT_SIZE), "fontStyle": "bold" if node.get("selected") else "plain",
            "hasBackgroundColor": "false", "hasLineColor": "false", "modelName": "internal",
            "modelPosition": "t" if group and not closed else "c", "textColor": "#0F172A", "visible": "true",
        }).text = node["display_label"]
        ET.SubElement(shape, ytag("Shape"), type="roundrectangle")
        if group:
            ET.SubElement(shape, ytag("State"), closed=str(closed).lower(), innerGraphDisplayEnabled="false")
            ET.SubElement(shape, ytag("Insets"), left="24", right="24", top="48", bottom="24")
        return shape

    def add_node(parent, nid):
        attrs = {"id": nid}
        is_group = nid in children
        if is_group:
            attrs["yfiles.foldertype"] = "group"
        element = ET.SubElement(parent, tag("node"), attrs)
        add_data(element, "node", display_nodes[nid], node_fields)
        data = ET.SubElement(element, tag("data"), key="node_graphics")
        if is_group:
            proxy = ET.SubElement(data, ytag("ProxyAutoBoundsNode"))
            realizers = ET.SubElement(proxy, ytag("Realizers"), active="0")
            shape_graphics(realizers, nid, group=True, closed=False)
            shape_graphics(realizers, nid, group=True, closed=True)
            inner = ET.SubElement(element, tag("graph"), id=f"{nid}:contents", edgedefault="directed")
            for child in children[nid]:
                add_node(inner, child)
        else:
            shape_graphics(data, nid)

    add_data(graph, "graph", graph_attrs, sorted(graph_attrs))
    for nid in sorted(roots):
        add_node(graph, nid)
    for identity, edge in sorted(edges.items()):
        element = ET.SubElement(graph, tag("edge"), id=identity, source=edge["upstream_id"], target=edge["downstream_id"])
        add_data(element, "edge", edge, edge_fields)
        line = ET.SubElement(ET.SubElement(element, tag("data"), key="edge_graphics"), ytag("PolyLineEdge"))
        up, down = edge["upstream_id"], edge["downstream_id"]
        path = ET.SubElement(line, ytag("Path"), sx="0.0", sy="0.0", tx="0.0", ty="0.0")
        points = layout.get("edges", {}).get(identity, []) if layout else []
        for px, py in points:
            ET.SubElement(path, ytag("Point"), x=str(px), y=str(py))
        relation = edge.get("boundary_relationship_type", edge["relationship_type"])
        uncertain = relation in {"direct_ingestion", "calibration"} and not edge["verified_dependency"]
        color = "#64748B"
        ET.SubElement(line, ytag("LineStyle"), color=color,
                      type="dashed" if uncertain else "line", width="1.2")
        ET.SubElement(line, ytag("Arrows"), source="none", target="standard")
        caption = ""
        if relation == "validation":
            caption = relation
        elif up == "VIEW-IMERG":
            caption = "historical / residual ?"
        if uncertain and "?" not in caption:
            caption = (caption + " ?").strip()
        if caption:
            ET.SubElement(line, ytag("EdgeLabel"), fontFamily="Arial", fontSize="12", modelName="six_pos", modelPosition="tail", textColor=color).text = caption
        ET.SubElement(line, ytag("BendStyle"), smoothed="false")
    ET.indent(root, space="  ")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(".graphml.tmp")
    ET.ElementTree(root).write(temporary, encoding="utf-8", xml_declaration=True)
    parsed = ET.parse(temporary)
    parsed_nodes = parsed.findall(f".//{tag('node')}")
    parsed_edges = parsed.findall(f".//{tag('edge')}")
    if len(parsed_nodes) != len(display_nodes) or {n.get("id") for n in parsed_nodes} != set(display_nodes):
        raise ValueError("GraphML node/group round-trip failed")
    if len(parsed_edges) != len(edges) or {e.get("id") for e in parsed_edges} != set(edges):
        raise ValueError("GraphML edge round-trip failed")
    for element in parsed_edges:
        edge = edges[element.get("id")]
        if (element.get("source"), element.get("target")) != (edge["upstream_id"], edge["downstream_id"]):
            raise ValueError("GraphML endpoint round-trip failed")
    temporary.replace(output_path)


def build_graph(evidence, source_log, output_path, include_validation=False):
    """Build the overview after provenance checks."""
    validate_evidence(evidence, source_log)
    view = project_overview(evidence, include_validation=include_validation)
    attrs = {
        "scope": evidence["scope"], "audit_status": evidence["status"],
        "evidence_file": "edge_evidence.json", "source_log_file": "sources.json",
        "semantics": evidence["overview"]["semantics"], "view": "overview",
        "validation": view["validation"],
        "legend": "Green: shared source. Neutral boxes: products and processing groups. All edges use one color and width. Solid: documented relationship, including grouped and calibration relationships. Dashed / ?: uncertainty in the dependency itself. Acquisition evidence, relationship types and version gaps remain in properties/evidence, without distinct visual styling. Self-loops are omitted; their records are retained in graph temporal_dependencies metadata and the evidence JSON.",
    }
    layout = evidence["overview"].get("layout")
    write_graph(view["nodes"], view["edges"], output_path, attrs, layout=layout)
    return view
