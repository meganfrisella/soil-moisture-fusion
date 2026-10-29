"""Render provenance GraphML geometry as a PNG; coordinates are drawing points."""

import math
import xml.etree.ElementTree as ET

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
from matplotlib.path import Path as DrawingPath

from data_provenance.graph import GRAPHML_NS, YED_NS

NS = {"g": GRAPHML_NS, "y": YED_NS}
INK = "#0F172A"


def read_graph(path):
    """Read the displayed overview geometry and styles from the actual GraphML."""
    tree = ET.parse(path)
    if tree.find("g:graph/g:data[@key='graph_view']", NS).text != "overview":
        raise ValueError("Expected an overview GraphML document")
    nodes, edges = {}, []
    for element in tree.findall(".//g:node", NS):
        shape = element.find("g:data/y:ShapeNode", NS)
        frame = shape is None
        if frame:
            realizers = element.find("g:data/y:ProxyAutoBoundsNode/y:Realizers", NS)
            shape = list(realizers)[int(realizers.get("active"))]
        geo = shape.find("y:Geometry", NS)
        label = shape.find("y:NodeLabel", NS)
        nodes[element.get("id")] = {
            "box": tuple(float(geo.get(k)) for k in ("x", "y", "width", "height")),
            "label": label.text, "fill": shape.find("y:Fill", NS).get("color"),
            "bold": label.get("fontStyle") == "bold", "frame": frame,
        }
    for element in tree.findall(".//g:edge", NS):
        line = element.find("g:data/y:PolyLineEdge", NS)
        style, path = line.find("y:LineStyle", NS), line.find("y:Path", NS)
        up, down = element.get("source"), element.get("target")
        a, b = nodes[up]["box"], nodes[down]["box"]
        start = (a[0] + a[2]/2 + float(path.get("sx")), a[1] + a[3]/2 + float(path.get("sy")))
        end = (b[0] + b[2]/2 + float(path.get("tx")), b[1] + b[3]/2 + float(path.get("ty")))
        points = [start] + [(float(p.get("x")), float(p.get("y"))) for p in path] + [end]
        points = [p for i, p in enumerate(points) if i == 0 or p != points[i-1]]
        points = clip_endpoint(points, a)
        points = list(reversed(clip_endpoint(list(reversed(points)), b)))
        label = line.find("y:EdgeLabel", NS)
        edges.append({"id": element.get("id"), "up": up, "down": down, "points": points,
                      "color": style.get("color"), "dashed": style.get("type") == "dashed",
                      "width": float(style.get("width")), "label": label.text if label is not None else ""})
    return nodes, edges


def clip_endpoint(points, box):
    """Clip a polyline's first endpoint to its node border so arrows stay visible."""
    x, y, w, h = box
    while len(points) > 2 and x < points[1][0] < x+w and y < points[1][1] < y+h:
        points.pop(0)
    px, py = points[0]
    dx, dy = points[1][0]-px, points[1][1]-py
    exits = []
    if dx:
        exits.append(((x+w if dx > 0 else x)-px)/dx)
    if dy:
        exits.append(((y+h if dy > 0 else y)-py)/dy)
    fraction = min((t for t in exits if t >= 0), default=0)
    points[0] = (px+dx*min(fraction, 1), py+dy*min(fraction, 1))
    return points


def overlaps(a, b):
    """Measure overlapping area for conservative edge-label placement."""
    x, y, w, h = a
    u, v, p, q = b
    return max(0, min(x+w, u+p)-max(x, u))*max(0, min(y+h, v+q)-max(y, v))


def caption_position(edge, nodes, occupied):
    """Place captions outside node rectangles and previously placed captions."""
    width, height = max(18, len(edge["label"])*6.5), 20
    if edge["up"] == edge["down"]:
        x, y, w, h = nodes[edge["up"]]["box"]
        candidates = [(x+w+52+width/2, y+h/2)]
    else:
        candidates = []
        for a, b in zip(edge["points"], edge["points"][1:]):
            dx, dy = b[0]-a[0], b[1]-a[1]
            distance = math.hypot(dx, dy)
            if distance < 20:
                continue
            for fraction in (0.5, 0.3, 0.7):
                for offset in (-15, 15, -28, 28, -42, 42):
                    candidates.append((a[0]+fraction*dx-offset*dy/distance, a[1]+fraction*dy+offset*dx/distance))
    if not candidates:
        candidates = [edge["points"][0]]
    obstacles = [n["box"] for n in nodes.values() if not n["frame"]]
    def score(point):
        box = (point[0]-width/2-4, point[1]-height/2-4, width+8, height+8)
        return sum(overlaps(box, b)*100 for b in obstacles) + sum(overlaps(box, b)*30 for b in occupied)
    point = min(candidates, key=score)
    occupied.append((point[0]-width/2, point[1]-height/2, width, height))
    return point


def condense_vertical(nodes, edges, factor):
    """Compress drawing-space gaps while preserving node sizes and connectivity."""
    padding = max(n["box"][3] for n in nodes.values() if not n["frame"])
    for node in nodes.values():
        x, y, w, h = node["box"]
        if node["frame"]:
            node["box"] = (x, y*factor-(1-factor)*padding/2,
                           w, h*factor+(1-factor)*padding)
        else:
            node["box"] = (x, (y+h/2)*factor-h/2, w, h)
    boxes = [n["box"] for n in nodes.values() if not n["frame"]]
    if any(overlaps(a, b) for i, a in enumerate(boxes) for b in boxes[i+1:]):
        raise ValueError("Vertical scale causes overlapping nodes; use a larger value")
    for edge in edges:
        points = [(x, y*factor) for x, y in edge["points"]]
        points = clip_endpoint(points, nodes[edge["up"]]["box"])
        edge["points"] = list(reversed(clip_endpoint(
            list(reversed(points)), nodes[edge["down"]]["box"])))


def render_png(input_path, output_path, dpi=240, vertical_scale=0.72):
    """Draw every overview node/edge and a legend using the GraphML encoding."""
    nodes, edges = read_graph(input_path)
    condense_vertical(nodes, edges, vertical_scale)
    caption_boxes = []
    for edge in edges:
        if edge["label"]:
            edge["caption_position"] = caption_position(edge, nodes, caption_boxes)
    xmin = min(n["box"][0] for n in nodes.values())-35
    xmax = max(n["box"][0]+n["box"][2] for n in nodes.values())+50
    xmax = max([xmax]+[b[0]+b[2]+30 for b in caption_boxes])
    top = min(n["box"][1] for n in nodes.values())-160
    graph_right = xmax
    graph_bottom = max(n["box"][1]+n["box"][3] for n in nodes.values())
    legend_x = graph_right+80
    legend_top = top+190
    xmax = legend_x+620
    bottom = max(graph_bottom+115, legend_top+480)
    width, height = xmax-xmin, bottom-top
    # Keep the graph's physical size; the right sidebar widens the canvas.
    scale = 18*72/(graph_right-xmin)
    fig, ax = plt.subplots(figsize=(width*scale/72, height*scale/72), dpi=dpi)
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
    ax.set_xlim(xmin, xmax); ax.set_ylim(bottom, top); ax.set_aspect("equal"); ax.axis("off")
    def text(x, y, label, size=16, weight="normal", color=INK, **kwargs):
        return ax.text(x, y, label, fontsize=size*scale, fontfamily="DejaVu Sans", fontweight=weight,
                       color=color, va="top", **kwargs)
    def box(rect, color, border="#64748B", zorder=2):
        x, y, w, h = rect
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=12",
                                   facecolor=color, edgecolor=border, linewidth=1.1*scale, zorder=zorder))
    text(xmin+20, top+18, "Soil moisture data provenance", size=32, weight="bold")
    node_count = sum(not node["frame"] for node in nodes.values())
    text(xmin+20, top+65, f"Product-level overview  •  {node_count} nodes / {len(edges)} relationships  •  Arrows point from input to dependent product", size=17)
    text(xmin+20, top+96, "Eastern Washington (WGS 84 / EPSG:4326)  |  Study window: 17 Jun–10 Oct 2026 UTC", size=15, color="#475569")
    for node in nodes.values():
        if node["frame"]:
            box(node["box"], "#F8FAFC", border="#CBD5E1", zorder=0)
            x, y, w, h = node["box"]
            text(x+w/2, y+10, node["label"], size=15, weight="bold", ha="center")
    for edge in edges:
        route = DrawingPath(edge["points"], [DrawingPath.MOVETO]+[DrawingPath.LINETO]*(len(edge["points"])-1))
        ax.add_patch(FancyArrowPatch(path=route, arrowstyle="-|>", mutation_scale=13*scale,
                                    color=edge["color"], linewidth=max(1.4, edge["width"])*scale,
                                    linestyle=(0, (5, 3)) if edge["dashed"] else "solid", zorder=1))
    for node in nodes.values():
        if node["frame"]:
            continue
        box(node["box"], node["fill"])
        x, y, w, h = node["box"]
        for i, line in enumerate(node["label"].splitlines()):
            text(x+w/2, y+h/2+(i-(len(node["label"].splitlines())-1)/2)*22, line,
                 weight="bold" if node["bold"] else "normal", ha="center", verticalalignment="center", zorder=3)
    for edge in edges:
        if edge["label"]:
            x, y = edge["caption_position"]
            text(x, y, edge["label"], size=12, color=edge["color"], ha="center", verticalalignment="center", zorder=4,
                 bbox={"facecolor":"white", "edgecolor":"none", "pad":1.5, "alpha":0.96})
    ax.plot([legend_x-35]*2, [legend_top-20, graph_bottom], color="#CBD5E1", linewidth=scale)
    columns = [legend_x]*3
    edge_top = legend_top+220
    text(columns[0], legend_top, "NODE COLORS", size=19, weight="bold")
    for i, (fill, label) in enumerate([
        ("#DCFCE7", "Shared upstream source or source family"),
        ("#F8FAFC", "Product or processing group"),
    ]):
        y=legend_top+48+i*62
        box((columns[0], y, 46, 30), fill)
        text(columns[0]+62, y+3, label, size=15)
    text(legend_x, edge_top, "EDGES", size=19, weight="bold")
    for i, (style, label) in enumerate([
        ("solid", "Documented relationship,\nincluding grouped dependencies"),
        ((0,(5,3)), "Uncertain dependency"),
    ]):
        y=edge_top+48+i*72
        ax.add_patch(FancyArrowPatch((legend_x,y+15),(legend_x+48,y+15),arrowstyle="-|>",mutation_scale=13*scale,color="#64748B",linewidth=1.8*scale,linestyle=style))
        text(legend_x+62,y,label,size=15)
    text(legend_x, edge_top+200, "?", size=15, weight="bold")
    text(legend_x+62, edge_top+200, "Uncertainty in the dependency itself", size=15)
    text(xmin+20,bottom-50,"Audit: 26 Oct 2026; dependency review: 27 Oct 2026  •  Evidence: metadata/data_provenance/  •  Diagram position has no geographic meaning.",size=14,color="#475569")
    # Check actual renderer text bounds before exporting the only requested artifact.
    fig.canvas.draw()
    renderer=fig.canvas.get_renderer()
    for artist in ax.texts:
        bounds=artist.get_window_extent(renderer)
        if bounds.x0 < -1 or bounds.y0 < -1 or bounds.x1 > fig.bbox.width+1 or bounds.y1 > fig.bbox.height+1:
            raise ValueError(f"Text outside PNG canvas: {artist.get_text()}")
    output_path.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(output_path,dpi=dpi,facecolor="white")
    plt.close(fig)
    return len(nodes)-sum(n["frame"] for n in nodes.values()),len(edges)
