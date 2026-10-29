"""Provenance simplification checks: pixi run python -m unittest discover -s tests -p 'test_data_provenance.py'."""

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from data_provenance.graph import build_graph, node_display, validate_evidence, GRAPHML_NS, YED_NS
from data_provenance.overview import project_overview, validate_overview


def fixture(node_ids, links, selected, bundles=()):
    """Construct small documented graphs with explicit presentation memberships."""
    nodes = [{"node_id": n, "label": n, "product": n, "versions": {"revision": None},
              "version_status": "unresolved_version", "selected": n in selected,
              "source_ids": ["source"], "lineage_boundary": {"reason": "synthetic"}}
             for n in node_ids]
    edges = []
    for i, (up, down, relationship, verified, level) in enumerate(links):
        record = {"edge_id": f"e{i}", "upstream_id": up, "downstream_id": down,
                  "relationship_type": relationship, "verified_dependency": verified,
                  "evidence_level": level, "source_references": [{"source_id": "source", "location": "synthetic"}]}
        for key in ("version_applicability", "temporal_applicability", "geographic_applicability", "acquisition_verification"):
            record[key] = "synthetic scope"
        if up == down:
            record.update(temporal_dependency=True, temporal_annotation="previous acquisition -> next acquisition")
        edges.append(record)
    membership = []
    bundled = set()
    for gid, members in bundles:
        membership.append({"node_id": gid, "member_node_ids": list(members), "label": gid, "kind": "bundle", "anchor_node_id": None})
        bundled.update(members)
    membership.extend({"node_id": n, "member_node_ids": [n], "label": n,
                       "kind": "selected" if n in selected else "product", "anchor_node_id": n}
                      for n in node_ids if n not in bundled)
    return {"nodes": nodes, "edges": edges,
            "overview": {"groups": membership, "protected_product_ids": list(selected)}}


class ProvenanceOverviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.evidence = json.loads((ROOT / "metadata/data_provenance/edge_evidence.json").read_text())
        cls.sources = json.loads((ROOT / "metadata/data_provenance/sources.json").read_text())

    def test_shared_category_cannot_invent_a_common_source(self):
        """Independent source branches cannot become common ancestry by grouping."""
        evidence = fixture("xyAB", [("x", "A", "direct_ingestion", True, "documented_product_dependency"),
                                    ("y", "B", "direct_ingestion", True, "documented_product_dependency")],
                           {"A", "B"}, [("sources", "xy")])
        with self.assertRaisesRegex(ValueError, "Changed shared ancestry"):
            project_overview(evidence)

    def test_grouping_cannot_bridge_disconnected_processing_segments(self):
        """Grouping two unrelated intermediates must not create a product path."""
        evidence = fixture("AxyB", [("A", "x", "direct_ingestion", True, "documented_product_dependency"),
                                    ("y", "B", "direct_ingestion", True, "documented_product_dependency")],
                           {"A", "B"}, [("intermediates", "xy")])
        with self.assertRaisesRegex(ValueError, "invented product dependency"):
            project_overview(evidence)

    def test_hidden_uncertainty_and_partial_acquisition_are_not_upgraded(self):
        """A verified boundary cannot upgrade an uncertain hidden segment."""
        evidence = fixture("xyA", [("x", "y", "direct_ingestion", False, "documented_product_dependency"),
                                   ("y", "A", "direct_ingestion", True, "acquisition_processing_record")],
                           {"A"}, [("chain", "xy")])
        view = project_overview(evidence)
        edge = next(iter(view["edges"].values()))
        self.assertFalse(edge["verified_dependency"])
        self.assertEqual(edge["evidence_level"], "mixed_with_acquisition_records")
        self.assertEqual(edge["supporting_edge_ids"], ["e0", "e1"])
        damaged = copy.deepcopy(view)
        next(iter(damaged["edges"].values()))["verified_dependency"] = True
        with self.assertRaisesRegex(ValueError, "upgraded unresolved"):
            validate_overview(evidence, damaged)

    def test_calibration_and_ingestion_stay_separate(self):
        """Parallel input and calibration relationships cannot be merged."""
        evidence = fixture("xA", [("x", "A", "direct_ingestion", True, "documented_product_dependency"),
                                  ("x", "A", "calibration", True, "documented_product_dependency")], {"A"})
        view = project_overview(evidence)
        self.assertEqual({e["boundary_relationship_type"] for e in view["edges"].values()}, {"calibration", "direct_ingestion"})

    def test_temporal_loop_keeps_its_explanation(self):
        """A same-product loop retains the prior-acquisition annotation."""
        evidence = fixture("A", [("A", "A", "direct_ingestion", True, "acquisition_processing_record")], {"A"})
        edge = next(iter(project_overview(evidence)["edges"].values()))
        self.assertTrue(edge["temporal_dependency"])
        self.assertIn("previous acquisition", edge["temporal_annotation"])

    def test_real_graph_preserves_shared_ancestors_and_primary_inputs(self):
        """The reviewed projection preserves every shared branch and selected input."""
        view = project_overview(self.evidence)
        self.assertEqual(len(view["nodes"]), 37)
        check = view["validation"]
        self.assertEqual(check["original_edges_accounted_for"], len(self.evidence["edges"]))
        shared_ids = {x["node_id"] for x in check["shared_ancestors"]}
        self.assertTrue({"MODIS-RADIANCES-UNRESOLVED", "SMAP-VWC-CLIM-UNRESOLVED", "GTOPO30-1996", "FAO-DSMW-2003"} <= shared_ids)
        selected = {n["node_id"] for n in self.evidence["nodes"] if n.get("selected")}
        primary_ids = {e["edge_id"] for e in self.evidence["edges"] if e["downstream_id"] in selected and e["relationship_type"] in {"direct_ingestion", "calibration"}}
        shown_ids = {eid for e in view["edges"].values() for eid in e["boundary_edge_ids"]}
        self.assertTrue(primary_ids <= shown_ids)
        excluded = {e["edge_id"]: e for e in self.evidence["edges"] if e["edge_id"] in view["excluded_edge_ids"]}
        self.assertEqual({e["relationship_type"] for e in excluded.values()}, {"direct_ingestion", "validation"})
        with_validation = project_overview(self.evidence, include_validation=True)
        self.assertEqual(with_validation["excluded_edge_ids"], [])
        self.assertTrue(any(e["boundary_relationship_type"] == "validation" for e in with_validation["edges"].values()))

    def test_gnss_is_selected_without_invented_product_dependencies(self):
        """GNSS remains a visible indirect input without invented retrieval/ingestion edges."""
        nid = "GNSS-RINEX-OBSERVATIONS-UNRESOLVED"
        view = project_overview(self.evidence)
        self.assertTrue(view["nodes"][nid]["selected"])
        self.assertEqual(view["nodes"][nid]["member_node_ids"], [nid])
        self.assertFalse(any(nid in (e["upstream_id"], e["downstream_id"])
                             for e in self.evidence["edges"]))
        native = next(n for n in self.evidence["nodes"] if n["node_id"] == nid)
        self.assertEqual(native["input_role"], "indirect_soil_moisture_observation")
        self.assertEqual(native["version_status"], "unresolved_version")
        self.assertEqual(len(native["source_ids"]), 3)

    def test_product_dependencies_do_not_require_acquisition_or_version_resolution(self):
        """Reviewed product links are verified without upgrading acquisition evidence or versions."""
        records = {e["edge_id"]: e for e in self.evidence["edges"]}
        review = self.evidence["dependency_review"]
        self.assertEqual(len(review["reclassified_product_dependency_ids"]), 35)
        for eid in review["reclassified_product_dependency_ids"]:
            self.assertTrue(records[eid]["verified_dependency"])
            self.assertEqual(records[eid]["evidence_level"], "documented_product_dependency")
            self.assertTrue(records[eid]["acquisition_verification"])
        self.assertEqual(sum(e["evidence_level"] == "acquisition_processing_record"
                             for e in records.values()), 13)
        view = project_overview(self.evidence, include_validation=True)
        unresolved = {eid for e in view["edges"].values() for eid in e["unresolved_edge_ids"]}
        self.assertEqual(unresolved, {"E-IMERG-SPL4SMGP"})
        for node in view["nodes"].values():
            self.assertNotIn("[v?]", node_display(node)["display_label"])
        self.assertTrue(any(n["version_status"] == "unresolved_version" for n in view["nodes"].values()))

    def test_unresolved_dependency_requires_a_substantive_reason(self):
        """An uncertain relationship must retain its rationale; comparison edges are exempt."""
        evidence = copy.deepcopy(self.evidence)
        edge = next(e for e in evidence["edges"] if e["edge_id"] == "E-IMERG-SPL4SMGP")
        edge.pop("unresolved_dependency_reason")
        with self.assertRaisesRegex(ValueError, "relationship-level reason"):
            validate_evidence(evidence, self.sources)

    def test_overview_round_trip_and_deterministic_single_output(self):
        """One reproducible graph preserves membership, edge metadata and display styles."""
        ns = {"g": GRAPHML_NS, "y": YED_NS}
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / name / "dependencies.graphml" for name in ("first", "second")]
            for path in paths:
                view = build_graph(self.evidence, self.sources, path)
                self.assertEqual(list(path.parent.iterdir()), [path])
            self.assertEqual(paths[0].read_bytes(), paths[1].read_bytes())
            tree = ET.parse(paths[0])
            xml_nodes = {n.get("id"): n for n in tree.findall(".//g:node", ns)}
            xml_edges = {e.get("id"): e for e in tree.findall(".//g:edge", ns)}
            temporal = json.loads(tree.find("g:graph/g:data[@key='graph_temporal_dependencies']", ns).text)
            temporal = {e["edge_id"]: e for e in temporal}
            self.assertEqual(set(xml_nodes), set(view["nodes"]) | {"FRAME-NLDAS-MODELS"})
            self.assertEqual(set(xml_edges) | set(temporal), set(view["edges"]))
            for nid, node in view["nodes"].items():
                xml_node = xml_nodes[nid]
                self.assertEqual(xml_node.find("g:data[@key='node_label']", ns).text, node["label"])
                self.assertEqual(json.loads(xml_node.find("g:data[@key='node_member_node_ids']", ns).text), node["member_node_ids"])
                self.assertEqual(json.loads(xml_node.find("g:data[@key='node_member_versions']", ns).text), node["member_versions"])
            for eid, edge in view["edges"].items():
                if edge["upstream_id"] == edge["downstream_id"]:
                    self.assertEqual(temporal[eid], edge)
                    self.assertNotIn(eid, xml_edges)
                else:
                    xml_edge = xml_edges[eid]
                    self.assertEqual((xml_edge.get("source"), xml_edge.get("target")), (edge["upstream_id"], edge["downstream_id"]))
                    self.assertEqual(json.loads(xml_edge.find("g:data[@key='edge_supporting_edge_ids']", ns).text), edge["supporting_edge_ids"])
            self.assertNotIn("#DBEAFE", {fill.get("color") for fill in tree.findall(".//y:Fill", ns)})
            styles = tree.findall(".//y:LineStyle", ns)
            self.assertEqual({style.get("color") for style in styles}, {"#64748B"})
            self.assertEqual({style.get("width") for style in styles}, {"1.2"})
            self.assertEqual(sum(style.get("type") == "dashed" for style in styles), 1)
            captions = [label.text for label in tree.findall(".//y:EdgeLabel", ns)]
            self.assertNotIn("calibration", captions)
            self.assertFalse(any("+A" in (caption or "") for caption in captions))
            frame = tree.find(".//g:node[@id='FRAME-NLDAS-MODELS']/g:graph", ns)
            self.assertEqual(len(frame.findall("g:node", ns)), 3)


if __name__ == "__main__":
    unittest.main()
