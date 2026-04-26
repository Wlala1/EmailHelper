import { useEffect, useRef } from "react";
import * as d3 from "d3";
import type { RelationshipPerson } from "./api";

interface Props {
  persons: RelationshipPerson[];
  userEmail: string;
  width?: number;
  height?: number;
}

interface GraphNode extends d3.SimulationNodeDatum {
  id: string;
  label: string;
  type: "user" | "person" | "org";
  role?: string | null;
  emailCategory?: string | null;
  decayedWeight: number;
  observationCount: number;
  recentTopics?: string[] | null;
  lastSummary?: string | null;
}

interface GraphLink extends d3.SimulationLinkDatum<GraphNode> {
  type: "contact" | "member_of";
  weight: number;
}

const CATEGORY_COLORS = [
  "#6366f1","#10b981","#f59e0b","#ef4444",
  "#8b5cf6","#06b6d4","#f97316","#84cc16",
  "#ec4899","#14b8a6",
];

const ORG_RECT_W = 100;
const ORG_RECT_H = 28;

/** Extract the org part from "Org · Role" or return "Other" */
function orgFromRole(role: string | null | undefined): string {
  if (!role) return "Other";
  const idx = role.indexOf("·");
  return idx > 0 ? role.slice(0, idx).trim() : "Other";
}

export default function RelationshipGraph({ persons, userEmail, width = 700, height = 480 }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!svgRef.current) return;
    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    if (persons.length === 0) {
      svg.append("text")
        .attr("x", width / 2).attr("y", height / 2)
        .attr("text-anchor", "middle")
        .attr("fill", "#94a3b8").attr("font-size", "14px")
        .text("No relationship data yet.");
      return;
    }

    // ── Org color map (derived from sender_role) ──────────────────────────────
    const orgs = Array.from(new Set(persons.map(p => orgFromRole(p.person_role))));
    const orgColor = new Map(
      orgs.map((org, i) => [org, CATEGORY_COLORS[i % CATEGORY_COLORS.length]])
    );

    const maxWeight = Math.max(...persons.map(p => p.decayed_weight), 1);
    const maxObs    = Math.max(...persons.map(p => p.observation_count), 1);

    // ── Nodes ─────────────────────────────────────────────────────────────────
    const userNode: GraphNode = {
      id: userEmail, label: "You", type: "user",
      decayedWeight: 1, observationCount: 0,
      fx: width / 2, fy: height / 2,
    };

    const personNodes: GraphNode[] = persons.map(p => ({
      id: p.person_email,
      label: p.person_name ?? p.person_email,
      type: "person" as const,
      role: p.person_role,
      emailCategory: p.email_category,
      decayedWeight: p.decayed_weight,
      observationCount: p.observation_count,
      recentTopics: p.recent_topics,
      lastSummary: p.last_interaction_summary,
    }));

    const orgNodes: GraphNode[] = orgs.map(org => ({
      id: `__org__${org}`,
      label: org,
      type: "org" as const,
      decayedWeight: 0,
      observationCount: 0,
    }));

    const nodes: GraphNode[] = [userNode, ...personNodes, ...orgNodes];

    // ── Links ─────────────────────────────────────────────────────────────────
    const contactLinks: GraphLink[] = personNodes.map(n => ({
      source: userEmail, target: n.id,
      type: "contact" as const, weight: n.decayedWeight,
    }));
    const memberLinks: GraphLink[] = personNodes.map(n => ({
      source: n.id,
      target: `__org__${orgFromRole(n.role)}`,
      type: "member_of" as const,
      weight: 0,
    }));
    const links: GraphLink[] = [...contactLinks, ...memberLinks];

    // ── Edge color ────────────────────────────────────────────────────────────
    const edgeColor = d3.scaleLinear<string>()
      .domain([0, maxWeight])
      .range(["#334155", "#0e7c66"])
      .clamp(true);

    // ── Simulation ────────────────────────────────────────────────────────────
    const simulation = d3.forceSimulation<GraphNode>(nodes)
      .force("link", d3.forceLink<GraphNode, GraphLink>(links)
        .id(d => d.id)
        .distance(d => d.type === "member_of" ? 70 : 100 + (1 - d.weight / maxWeight) * 80)
        .strength(d => d.type === "member_of" ? 0.8 : 0.5))
      .force("charge", d3.forceManyBody().strength(
        d => (d as GraphNode).type === "org" ? -80 : -200
      ))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collision", d3.forceCollide(
        (d: GraphNode) => d.type === "org" ? ORG_RECT_W / 2 : 22
      ));

    const g = svg.append("g");
    svg.call(
      d3.zoom<SVGSVGElement, unknown>()
        .scaleExtent([0.3, 3])
        .on("zoom", (event: d3.D3ZoomEvent<SVGSVGElement, unknown>) => {
          g.attr("transform", event.transform.toString());
        })
    );

    // ── Member-of dashed links ────────────────────────────────────────────────
    const memberLink = g.append("g")
      .selectAll<SVGLineElement, GraphLink>("line")
      .data(memberLinks)
      .join("line")
      .attr("stroke", d => orgColor.get(orgFromRole((d.source as GraphNode).role)) ?? "#475569")
      .attr("stroke-opacity", 0.35)
      .attr("stroke-width", 1)
      .attr("stroke-dasharray", "4 3");

    // ── Contact links ─────────────────────────────────────────────────────────
    const contactLink = g.append("g")
      .selectAll<SVGLineElement, GraphLink>("line")
      .data(contactLinks)
      .join("line")
      .attr("stroke", d => edgeColor(d.weight))
      .attr("stroke-opacity", 0.75)
      .attr("stroke-width", d => 1 + (d.weight / maxWeight) * 8);

    // ── Org blocks ────────────────────────────────────────────────────────────
    const orgGroup = g.append("g")
      .selectAll<SVGRectElement, GraphNode>("rect")
      .data(orgNodes)
      .join("rect")
      .attr("width", ORG_RECT_W).attr("height", ORG_RECT_H)
      .attr("rx", 6).attr("ry", 6)
      .attr("fill", d => orgColor.get(d.label) ?? "#94a3b8")
      .attr("fill-opacity", 0.15)
      .attr("stroke", d => orgColor.get(d.label) ?? "#94a3b8")
      .attr("stroke-width", 1.5)
      .style("cursor", "default");

    const orgLabel = g.append("g")
      .selectAll<SVGTextElement, GraphNode>("text")
      .data(orgNodes)
      .join("text")
      .attr("text-anchor", "middle").attr("dy", "0.35em")
      .attr("font-size", "8px")
      .attr("fill", d => orgColor.get(d.label) ?? "#94a3b8")
      .text(d => d.label.length > 14 ? d.label.slice(0, 13) + "…" : d.label)
      .style("pointer-events", "none");

    // ── Person nodes ──────────────────────────────────────────────────────────
    const personGroup = g.append("g")
      .selectAll<SVGCircleElement, GraphNode>("circle")
      .data(personNodes)
      .join("circle")
      .attr("r", d => 8 + (d.observationCount / maxObs) * 10)
      .attr("fill", d => orgColor.get(orgFromRole(d.role)) ?? "#94a3b8")
      .attr("fill-opacity", 0.85)
      .attr("stroke", "#1e293b")
      .attr("stroke-width", 1.5)
      .style("cursor", "pointer");

    // ── User node (stored references for tick updates) ─────────────────────────
    const userCircle = g.append("circle")
      .datum(userNode)
      .attr("r", 22)
      .attr("fill", "#6366f1")
      .attr("stroke", "#fff")
      .attr("stroke-width", 2.5);
    const userLabelEl = g.append("text")
      .datum(userNode)
      .attr("text-anchor", "middle").attr("dy", "0.35em")
      .attr("font-size", "10px").attr("font-weight", "700")
      .attr("fill", "#fff").attr("pointer-events", "none")
      .text("You");

    // ── Person name labels ────────────────────────────────────────────────────
    const personLabel = g.append("g")
      .selectAll<SVGTextElement, GraphNode>("text")
      .data(personNodes)
      .join("text")
      .attr("text-anchor", "middle")
      .attr("dy", d => -(8 + (d.observationCount / maxObs) * 10) - 4)
      .attr("font-size", "9px")
      .attr("fill", "#cbd5e1")
      .text(d => d.label.length > 16 ? d.label.slice(0, 15) + "…" : d.label)
      .style("pointer-events", "none");

    // ── Tooltip ───────────────────────────────────────────────────────────────
    const tooltip = d3.select(tooltipRef.current);

    personGroup
      .on("mouseenter", (event: MouseEvent, d: GraphNode) => {
        const p = persons.find(p => p.person_email === d.id);
        const topics = (p?.recent_topics ?? []).join(", ");
        tooltip
          .style("opacity", "1")
          .style("left", `${(event as MouseEvent & {offsetX: number}).offsetX + 14}px`)
          .style("top", `${(event as MouseEvent & {offsetY: number}).offsetY - 10}px`)
          .html(
            `<strong>${d.role ?? "Unknown"}</strong>` +
            (d.label !== d.id ? `<br/><span style="color:#e2e8f0">${d.label}</span>` : "") +
            `<br/><span style="color:#7dd3fc;font-size:10px">${d.id}</span>` +
            `<br/><span style="color:#94a3b8">Contacts: ${d.observationCount} · Weight: ${d.decayedWeight.toFixed(2)}</span>` +
            (topics ? `<br/><span style="color:#7dd3fc">Topics: ${topics}</span>` : "") +
            (d.lastSummary ? `<br/><span style="color:#d1fae5;font-size:10px">${d.lastSummary.slice(0, 120)}${d.lastSummary.length > 120 ? "…" : ""}</span>` : "")
          );
      })
      .on("mouseleave", () => tooltip.style("opacity", "0"));

    // ── Drag ──────────────────────────────────────────────────────────────────
    personGroup.call(
      d3.drag<SVGCircleElement, GraphNode>()
        .on("start", (event, d) => { if (!event.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
        .on("drag",  (event, d) => { d.fx = event.x; d.fy = event.y; })
        .on("end",   (event, d) => { if (!event.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; })
    );

    // ── Tick ──────────────────────────────────────────────────────────────────
    simulation.on("tick", () => {
      contactLink
        .attr("x1", d => (d.source as GraphNode).x ?? 0)
        .attr("y1", d => (d.source as GraphNode).y ?? 0)
        .attr("x2", d => (d.target as GraphNode).x ?? 0)
        .attr("y2", d => (d.target as GraphNode).y ?? 0);
      memberLink
        .attr("x1", d => (d.source as GraphNode).x ?? 0)
        .attr("y1", d => (d.source as GraphNode).y ?? 0)
        .attr("x2", d => (d.target as GraphNode).x ?? 0)
        .attr("y2", d => (d.target as GraphNode).y ?? 0);
      personGroup
        .attr("cx", d => d.x ?? 0)
        .attr("cy", d => d.y ?? 0);
      personLabel
        .attr("x", d => d.x ?? 0)
        .attr("y", d => d.y ?? 0);
      orgGroup
        .attr("x", d => (d.x ?? 0) - ORG_RECT_W / 2)
        .attr("y", d => (d.y ?? 0) - ORG_RECT_H / 2);
      orgLabel
        .attr("x", d => d.x ?? 0)
        .attr("y", d => d.y ?? 0);
      userCircle
        .attr("cx", userNode.x ?? width / 2)
        .attr("cy", userNode.y ?? height / 2);
      userLabelEl
        .attr("x", userNode.x ?? width / 2)
        .attr("y", userNode.y ?? height / 2);
    });

    return () => { simulation.stop(); };
  }, [persons, userEmail, width, height]);

  // ── Legend ────────────────────────────────────────────────────────────────
  const orgs = Array.from(new Set(persons.map(p => orgFromRole(p.person_role))));
  const legendColor = new Map(
    orgs.map((org, i) => [org, CATEGORY_COLORS[i % CATEGORY_COLORS.length]])
  );

  return (
    <div className="relationship-graph-wrapper">
      <div style={{ position: "relative" }}>
        <svg
          ref={svgRef}
          width={width}
          height={height}
          style={{ width: "100%", height: "auto", background: "rgba(15,23,42,0.65)", borderRadius: "14px" }}
        />
        <div
          ref={tooltipRef}
          style={{
            position: "absolute", opacity: 0,
            background: "rgba(15,23,42,0.95)",
            border: "1px solid rgba(99,102,241,0.4)",
            borderRadius: "8px", padding: "8px 12px",
            fontSize: "12px", color: "#e2e8f0",
            pointerEvents: "none", transition: "opacity 0.15s",
            maxWidth: "240px", lineHeight: "1.5",
          }}
        />
      </div>
      <div className="graph-legend">
        <div className="graph-legend-item">
          <span className="graph-legend-dot" style={{ background: "#6366f1" }} />
          <span>You</span>
        </div>
        {orgs.map(org => (
          <div key={org} className="graph-legend-item">
            <span className="graph-legend-dot" style={{ background: legendColor.get(org) }} />
            <span>{org}</span>
          </div>
        ))}
        <div className="graph-legend-item" style={{ marginLeft: "auto" }}>
          <span style={{ fontSize: "0.75rem", color: "#94a3b8" }}>
            Edge color = strength · Node size = contact frequency · — — org
          </span>
        </div>
      </div>
      <p className="graph-hint">Scroll to zoom · Drag nodes · Hover for details</p>
    </div>
  );
}
