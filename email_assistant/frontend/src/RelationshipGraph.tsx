import { useEffect, useRef } from "react";
import * as d3 from "d3";
import type { RelationshipInsight } from "./api";

interface Props {
  relationships: RelationshipInsight[];
  userEmail: string;
  width?: number;
  height?: number;
}

interface GraphNode extends d3.SimulationNodeDatum {
  id: string;
  label: string;
  role?: string | null;
  org?: string | null;
  weight: number;
  isUser: boolean;
}

interface GraphLink extends d3.SimulationLinkDatum<GraphNode> {
  weight: number;
}

// Stable color palette for organizations
const ORG_COLORS = [
  "#6366f1", "#10b981", "#f59e0b", "#ef4444",
  "#8b5cf6", "#06b6d4", "#f97316", "#84cc16",
  "#ec4899", "#14b8a6",
];

export default function RelationshipGraph({ relationships, userEmail, width = 640, height = 480 }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!svgRef.current) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    if (relationships.length === 0) {
      svg.append("text")
        .attr("x", width / 2)
        .attr("y", height / 2)
        .attr("text-anchor", "middle")
        .attr("fill", "#94a3b8")
        .attr("font-size", "14px")
        .text("No relationship data yet.");
      return;
    }

    // Assign stable color per unique org
    const orgs = Array.from(new Set(relationships.map(r => r.organisation_name ?? "Unknown")));
    const orgColor = new Map(orgs.map((org, i) => [org, ORG_COLORS[i % ORG_COLORS.length]]));

    // Build nodes
    const userNode: GraphNode = {
      id: userEmail,
      label: "You",
      org: null,
      weight: 1,
      isUser: true,
      x: width / 2,
      y: height / 2,
    };

    const contactNodes: GraphNode[] = relationships.map(r => ({
      id: r.person_email,
      label: r.person_name ?? r.person_email,
      role: r.person_role,
      org: r.organisation_name ?? "Unknown",
      weight: r.relationship_weight,
      isUser: false,
    }));

    const nodes: GraphNode[] = [userNode, ...contactNodes];

    // Build links (user → each contact)
    const links: GraphLink[] = contactNodes.map(n => ({
      source: userEmail,
      target: n.id,
      weight: n.weight,
    }));

    // Cluster force: nodes with same org attract each other
    const orgCentroids = new Map<string, { x: number; y: number }>();
    orgs.forEach((org, i) => {
      const angle = (i / orgs.length) * 2 * Math.PI;
      orgCentroids.set(org, {
        x: width / 2 + Math.cos(angle) * (width * 0.28),
        y: height / 2 + Math.sin(angle) * (height * 0.28),
      });
    });

    function clusterForce(alpha: number) {
      for (const node of nodes) {
        if (node.isUser || !node.org) continue;
        const centroid = orgCentroids.get(node.org);
        if (!centroid) continue;
        node.vx = (node.vx ?? 0) + (centroid.x - (node.x ?? 0)) * alpha * 0.3;
        node.vy = (node.vy ?? 0) + (centroid.y - (node.y ?? 0)) * alpha * 0.3;
      }
    }

    const simulation = d3.forceSimulation<GraphNode>(nodes)
      .force("link", d3.forceLink<GraphNode, GraphLink>(links)
        .id(d => d.id)
        .distance(d => 80 + (1 - d.weight) * 80)
        .strength(0.6))
      .force("charge", d3.forceManyBody().strength(-220))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collision", d3.forceCollide(28))
      .force("cluster", clusterForce);

    // Container with zoom
    const g = svg.append("g");

    svg.call(
      d3.zoom<SVGSVGElement, unknown>()
        .scaleExtent([0.4, 3])
        .on("zoom", (event: d3.D3ZoomEvent<SVGSVGElement, unknown>) => {
          g.attr("transform", event.transform.toString());
        })
    );

    // Draw links with thickness = weight
    const link = g.append("g")
      .selectAll<SVGLineElement, GraphLink>("line")
      .data(links)
      .join("line")
      .attr("stroke", "#cbd5e1")
      .attr("stroke-opacity", 0.7)
      .attr("stroke-width", d => Math.max(1, d.weight * 6));

    // Draw nodes
    const node = g.append("g")
      .selectAll<SVGCircleElement, GraphNode>("circle")
      .data(nodes)
      .join("circle")
      .attr("r", d => d.isUser ? 18 : 12)
      .attr("fill", d => {
        if (d.isUser) return "#6366f1";
        return orgColor.get(d.org ?? "Unknown") ?? "#94a3b8";
      })
      .attr("stroke", "#fff")
      .attr("stroke-width", 2)
      .style("cursor", "pointer");

    // Labels
    const label = g.append("g")
      .selectAll<SVGTextElement, GraphNode>("text")
      .data(nodes)
      .join("text")
      .attr("text-anchor", "middle")
      .attr("dy", d => d.isUser ? 32 : 26)
      .attr("font-size", d => d.isUser ? "12px" : "10px")
      .attr("font-weight", d => d.isUser ? "600" : "400")
      .attr("fill", "#e2e8f0")
      .text(d => d.label.length > 16 ? d.label.slice(0, 15) + "…" : d.label)
      .style("pointer-events", "none");

    // Tooltip
    const tooltip = d3.select(tooltipRef.current);

    node
      .on("mouseenter", (event: MouseEvent, d: GraphNode) => {
        if (d.isUser) return;
        tooltip
          .style("opacity", "1")
          .style("left", `${event.offsetX + 14}px`)
          .style("top", `${event.offsetY - 10}px`)
          .html(
            `<strong>${d.label}</strong>` +
            (d.role ? `<br/><span>${d.role}</span>` : "") +
            (d.org ? `<br/><span>${d.org}</span>` : "") +
            `<br/><span>Weight: ${d.weight.toFixed(2)}</span>`
          );
      })
      .on("mouseleave", () => {
        tooltip.style("opacity", "0");
      });

    // Drag
    node.call(
      d3.drag<SVGCircleElement, GraphNode>()
        .on("start", (event: d3.D3DragEvent<SVGCircleElement, GraphNode, GraphNode>, d) => {
          if (!event.active) simulation.alphaTarget(0.3).restart();
          d.fx = d.x;
          d.fy = d.y;
        })
        .on("drag", (event: d3.D3DragEvent<SVGCircleElement, GraphNode, GraphNode>, d) => {
          d.fx = event.x;
          d.fy = event.y;
        })
        .on("end", (event: d3.D3DragEvent<SVGCircleElement, GraphNode, GraphNode>, d) => {
          if (!event.active) simulation.alphaTarget(0);
          d.fx = null;
          d.fy = null;
        })
    );

    simulation.on("tick", () => {
      link
        .attr("x1", d => (d.source as GraphNode).x ?? 0)
        .attr("y1", d => (d.source as GraphNode).y ?? 0)
        .attr("x2", d => (d.target as GraphNode).x ?? 0)
        .attr("y2", d => (d.target as GraphNode).y ?? 0);

      node
        .attr("cx", d => d.x ?? 0)
        .attr("cy", d => d.y ?? 0);

      label
        .attr("x", d => d.x ?? 0)
        .attr("y", d => d.y ?? 0);
    });

    return () => { simulation.stop(); };
  }, [relationships, userEmail, width, height]);

  // Build org legend
  const orgs = Array.from(new Set(relationships.map(r => r.organisation_name ?? "Unknown")));
  const orgColor = new Map(orgs.map((org, i) => [org, ORG_COLORS[i % ORG_COLORS.length]]));

  return (
    <div className="relationship-graph-wrapper">
      <div style={{ position: "relative" }}>
        <svg
          ref={svgRef}
          width={width}
          height={height}
          style={{ width: "100%", height: "auto", background: "rgba(15,23,42,0.6)", borderRadius: "12px" }}
        />
        <div
          ref={tooltipRef}
          style={{
            position: "absolute",
            opacity: 0,
            background: "rgba(15,23,42,0.92)",
            border: "1px solid rgba(99,102,241,0.4)",
            borderRadius: "8px",
            padding: "8px 12px",
            fontSize: "12px",
            color: "#e2e8f0",
            pointerEvents: "none",
            transition: "opacity 0.15s",
            maxWidth: "200px",
          }}
        />
      </div>
      {orgs.length > 0 && (
        <div className="graph-legend">
          <div className="graph-legend-item">
            <span className="graph-legend-dot" style={{ background: "#6366f1" }} />
            <span>You</span>
          </div>
          {orgs.map(org => (
            <div key={org} className="graph-legend-item">
              <span className="graph-legend-dot" style={{ background: orgColor.get(org) }} />
              <span>{org}</span>
            </div>
          ))}
        </div>
      )}
      <p className="graph-hint">Edge thickness = relationship strength · Scroll to zoom · Drag nodes</p>
    </div>
  );
}
