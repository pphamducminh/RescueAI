const API_ROOT = "/api/demo";
const SVG_NS = "http://www.w3.org/2000/svg";
const ROUTE_COLORS = ["#10a88a", "#3d72e6", "#dc8142", "#8b66cb"];

let state = null;
let busy = false;

function byId(id) {
  return document.getElementById(id);
}

function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== "") node.textContent = String(text);
  return node;
}

function svgElement(tag, attributes = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attributes)) {
    node.setAttribute(key, String(value));
  }
  return node;
}

function clear(node) {
  node.replaceChildren();
  return node;
}

function showFeedback(message, error = false) {
  const feedback = byId("feedback");
  feedback.textContent = message;
  feedback.classList.toggle("is-error", error);
  feedback.hidden = false;
}

function setBusy(value) {
  busy = value;
  document.querySelectorAll("button").forEach((button) => {
    button.disabled = value;
  });
  byId("connection").textContent = value ? "Updating…" : "Connected";
  byId("connection").classList.toggle("is-offline", false);
}

function errorText(payload, response) {
  if (typeof payload?.detail === "string") return payload.detail;
  if (Array.isArray(payload?.detail)) {
    return payload.detail.map((item) => item.msg || String(item)).join("; ");
  }
  return `Request failed (${response.status}).`;
}

async function request(path, body) {
  const options = { cache: "no-store" };
  if (body !== undefined) {
    options.method = "POST";
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  const response = await fetch(`${API_ROOT}${path}`, options);
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error(`The server returned an unreadable response (${response.status}).`);
  }
  if (!response.ok) throw new Error(errorText(payload, response));
  return payload;
}

async function mutate(path, body, successMessage) {
  if (busy) return;
  setBusy(true);
  try {
    const next = await request(path, body);
    state = next;
    render();
    showFeedback(successMessage);
  } catch (error) {
    showFeedback(error.message || "The action failed.", true);
  } finally {
    setBusy(false);
  }
}

function operatorId() {
  const value = byId("operator-id").value.trim();
  if (!value) {
    showFeedback("Enter your dispatcher ID in the header before reviewing, updating roads, or approving.", true);
    byId("operator-id").focus();
    return null;
  }
  return value;
}

function titleCase(value) {
  return String(value ?? "unknown").replaceAll("_", " ");
}

function printable(value) {
  if (value === null || value === undefined) return "unknown";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.length ? value.map(printable).join(", ") : "None reported";
  if (typeof value === "object") {
    if ("min" in value) {
      const upper = value.max === null || value.max === undefined ? "+" : value.max;
      return upper === value.min ? String(value.min) : `${value.min}–${upper}`;
    }
    if ("tag" in value) return titleCase(value.tag);
    if ("group" in value) return `${titleCase(value.group)}${value.count ? ` (${printable(value.count)})` : ""}`;
    if ("mode" in value && "status" in value) return `${titleCase(value.mode)}: ${titleCase(value.status)}`;
    const entries = Object.entries(value).filter(([, item]) => item !== null && item !== undefined);
    return entries.map(([key, item]) => `${titleCase(key)} ${printable(item)}`).join("; ");
  }
  return titleCase(value);
}

function seconds(value) {
  if (!Number.isFinite(Number(value))) return "—";
  const total = Math.round(Number(value));
  if (total >= 60) return `${Math.floor(total / 60)} min ${total % 60} sec`;
  return `${total} sec`;
}

function nodeLabel(nodeId) {
  const node = (state?.nodes || []).find((item) => item.node_id === nodeId);
  return node?.label || nodeId || "unknown";
}

function fillNodeSelect(select, selected) {
  clear(select);
  const eligibleIds = new Set(state?.incident_node_ids || (state?.nodes || []).map((node) => node.node_id));
  for (const node of (state?.nodes || []).filter((item) => eligibleIds.has(item.node_id))) {
    const option = element("option", "", `${node.label || node.node_id} (${node.node_id})`);
    option.value = node.node_id;
    option.selected = node.node_id === selected;
    select.append(option);
  }
}

function renderFacts(container, extraction) {
  const facts = extraction?.facts;
  if (!facts || typeof facts !== "object") {
    container.append(element("p", "field-hint", "No structured extraction is available."));
    return;
  }
  const evidence = new Map((extraction.evidence || []).map((item) => [item.id, item]));
  const list = element("dl", "fact-grid");
  for (const [name, field] of Object.entries(facts)) {
    const pair = element("div", "fact");
    pair.append(element("dt", "", titleCase(name)));
    const stateName = String(field?.state || "unknown").toLowerCase();
    const value = stateName === "supported"
      ? printable(field.value)
      : `${titleCase(stateName)}${field?.unknown_reason ? ` · ${titleCase(field.unknown_reason)}` : ""}`;
    pair.append(element("dd", `fact-value fact-${stateName}`, value));
    const quotes = (field?.candidates || []).flatMap((candidate) =>
      (candidate.evidence_ids || []).map((id) => evidence.get(id)?.text_span?.quote).filter(Boolean),
    );
    if (quotes.length) pair.append(element("small", "evidence", `Evidence: “${[...new Set(quotes)].join("”; “")}”`));
    list.append(pair);
  }
  container.append(list);
}

function renderPriority(container, priority) {
  if (!priority) {
    container.append(element("p", "field-hint", "No priority suggestion is available."));
    return;
  }
  const attention = element("div", "priority-line");
  attention.append(element("strong", "", `Suggested attention: ${titleCase(priority.suggested_attention)}`));
  attention.append(element("span", "", priority.priority_weight == null
    ? "No suggested weight" : `Suggested weight ${priority.priority_weight}`));
  container.append(attention);
  if (priority.requires_human_review) {
    container.append(element("p", "field-hint", "Provisional suggestion; dispatcher review required."));
  }
  if (Array.isArray(priority.unresolved_fields) && priority.unresolved_fields.length) {
    container.append(element("p", "field-hint", `Unresolved fields: ${priority.unresolved_fields.map(titleCase).join(", ")}.`));
  }
  if (Array.isArray(priority.reasons) && priority.reasons.length) {
    const details = element("details", "reason-details");
    details.append(element("summary", "", "Why this was suggested"));
    const list = element("ul");
    for (const reason of priority.reasons) {
      list.append(element("li", "", reason.explanation || titleCase(reason.rule_id)));
    }
    details.append(list);
    container.append(details);
  }
}

function renderReports() {
  const root = clear(byId("reports"));
  const reports = state?.reports || [];
  byId("report-count").textContent = String(reports.length);
  if (!reports.length) {
    root.append(element("p", "empty-state", "No SOS reports yet. Submit one above to start the demo."));
    return;
  }
  for (const report of reports) {
    const card = element("article", "report-card");
    const heading = element("div", "report-heading");
    heading.append(element("h3", "", report.sos_id));
    heading.append(element("span", report.reviewed_node_id ? "chip chip-ok" : "chip chip-wait",
      report.reviewed_node_id ? "Reviewed" : "Needs review"));
    card.append(heading);
    card.append(element("p", "report-text", report.raw_text));
    card.append(element("p", "field-hint", `Suggested node: ${nodeLabel(report.suggested_node_id)}`));

    const extraction = element("details", "report-details");
    extraction.append(element("summary", "", "Structured extraction and evidence"));
    renderFacts(extraction, report.extraction);
    card.append(extraction);
    renderPriority(card, report.priority);

    const form = element("form", "review-form");
    form.dataset.action = "review";
    form.dataset.id = report.sos_id;
    const nodeLabelElement = element("label", "", "Accepted incident node");
    const nodeSelect = element("select");
    nodeSelect.name = "incident_node_id";
    nodeSelect.required = true;
    fillNodeSelect(nodeSelect, report.reviewed_node_id || report.suggested_node_id);
    nodeLabelElement.append(nodeSelect);
    const weightLabel = element("label", "", "Approved dispatch weight");
    const weightInput = element("input");
    weightInput.type = "number";
    weightInput.name = "dispatch_weight";
    weightInput.min = "1";
    weightInput.max = "5";
    weightInput.step = "1";
    weightInput.required = true;
    weightInput.placeholder = "1 to 5";
    if (report.approved_dispatch_weight != null) weightInput.value = String(report.approved_dispatch_weight);
    weightLabel.append(weightInput);
    const capabilities = element("fieldset", "capability-field");
    capabilities.append(element("legend", "", "Required team capabilities"));
    const capabilityOptions = element("div", "capability-options");
    const suggested = report.reviewed_required_capabilities
      ?? report.suggested_required_capabilities ?? ["basic"];
    for (const capability of ["basic", "medical", "boat"]) {
      const label = element("label", "capability-option");
      const input = element("input");
      input.type = "checkbox";
      input.name = "required_capabilities";
      input.value = capability;
      input.checked = capability === "basic" || suggested.includes(capability);
      input.disabled = capability === "basic";
      label.append(input, element("span", "", titleCase(capability)));
      capabilityOptions.append(label);
    }
    capabilities.append(capabilityOptions);
    form.append(nodeLabelElement, weightLabel, capabilities,
      element("p", "field-hint", "Confirm node, weight and capabilities before saving. Preselected extra capabilities are suggestions until reviewed."));
    const button = element("button", "button button-secondary", report.reviewed_node_id ? "Update review" : "Accept review");
    button.type = "submit";
    form.append(button);
    card.append(form);
    root.append(card);
  }
}

function renderProposal() {
  const root = clear(byId("proposal"));
  const proposal = state?.proposal;
  const approved = state?.approved;
  const status = byId("proposal-status");
  if (!proposal) {
    status.textContent = "No current proposal";
    status.className = "status-pill";
    root.append(element("p", "empty-state", "Review an SOS report to generate a dispatch proposal."));
    return;
  }
  const plan = proposal.plan || {};
  const assignments = plan.assignments || [];
  const isApproved = approved?.proposal_id === proposal.proposal_id
    && approved?.graph_revision === proposal.graph_revision;
  status.textContent = isApproved ? "Approved" : assignments.length ? "Awaiting approval" : "Needs review";
  status.className = `status-pill ${isApproved ? "status-approved" : "status-pending"}`;
  root.append(element("p", "field-hint", `Proposal ${proposal.proposal_id} · graph revision ${proposal.graph_revision}`));
  if (approved && !isApproved) {
    root.append(element("p", "warning", "A previous approval is not for this current proposal. Review the new routes before approving."));
  }

  if (!assignments.length) root.append(element("p", "empty-state", "No feasible team assignment. Review the incident node, capabilities, and current road status before approval."));
  assignments.forEach((assignment, index) => {
    const card = element("article", "assignment");
    const stripe = element("i", "route-stripe");
    stripe.style.backgroundColor = ROUTE_COLORS[index % ROUTE_COLORS.length];
    card.append(stripe);
    const content = element("div");
    content.append(element("strong", "", `${assignment.team_id} → ${assignment.request_id}`));
    content.append(element("p", "", `Route: ${(assignment.route?.node_ids || []).map(nodeLabel).join(" → ") || "not available"}`));
    content.append(element("small", "", `Travel ETA ${seconds(assignment.eta_seconds)} · Predicted response ${seconds(assignment.predicted_response_seconds)}`));
    card.append(content);
    root.append(card);
  });

  const unserved = plan.unserved_requests || [];
  if (unserved.length) {
    const section = element("div", "unserved");
    section.append(element("h3", "", `Unserved requests (${unserved.length})`));
    for (const item of unserved) {
      section.append(element("p", "", `${item.request_id}: ${(item.reasons || []).map(titleCase).join(", ")}`));
    }
    root.append(section);
  }
  const objective = plan.objective_value;
  if (objective) {
    root.append(element("p", "objective", `Objective: pending loss ${objective.weighted_pending_loss}; weighted travel ${objective.weighted_travel_seconds} sec; scalar cost ${objective.scalar_cost}.`));
  }
  if (isApproved) {
    root.append(element("p", "approved-message", `Approved by ${approved.approved_by} at ${new Date(approved.approved_at).toLocaleString()}.`));
  } else if (assignments.length) {
    const button = element("button", "button button-primary", "Approve current proposal");
    button.type = "button";
    button.dataset.action = "approve";
    button.dataset.id = proposal.proposal_id;
    root.append(button);
  }
}

function renderRoads() {
  const root = clear(byId("roads"));
  const usedEdges = new Set((state?.proposal?.plan?.assignments || []).flatMap((assignment) => assignment.route?.edge_ids || []));
  const roads = [...(state?.roads || [])].sort((left, right) =>
    Number(usedEdges.has(right.edge_id)) - Number(usedEdges.has(left.edge_id))
      || left.edge_id.localeCompare(right.edge_id));
  for (const road of roads) {
    const row = element("div", "road-row");
    const info = element("div");
    info.append(element("strong", "", `${nodeLabel(road.source_node_id)} → ${nodeLabel(road.target_node_id)}`));
    info.append(element("small", "", `${road.edge_id} · ${road.distance_meters} m · ${seconds(road.base_travel_time_seconds)} · risk ${road.risk_score}`));
    if (usedEdges.has(road.edge_id)) info.append(element("small", "used-edge", "Used by a current proposed route"));
    const action = element("button", road.status === "BLOCKED" ? "button button-small button-secondary" : "button button-small button-danger",
      road.status === "BLOCKED" ? "Unblock" : "Block");
    action.type = "button";
    action.dataset.action = road.status === "BLOCKED" ? "unblock" : "block";
    action.dataset.id = road.edge_id;
    action.setAttribute("aria-label", `${action.textContent} road ${road.edge_id}`);
    const badge = element("span", `road-status road-${String(road.status).toLowerCase()}`, road.status);
    row.append(info, badge, action);
    root.append(row);
  }
}

function renderTeams() {
  const root = clear(byId("teams"));
  for (const team of state?.teams || []) {
    const row = element("div", "team-row");
    row.append(element("strong", "", team.team_id));
    row.append(element("span", "", `At ${nodeLabel(team.graph_node_id)}`));
    row.append(element("span", team.available ? "chip chip-ok" : "chip chip-wait", team.available ? "Available" : "Unavailable"));
    if (team.capabilities?.length) row.append(element("small", "", `Capabilities: ${team.capabilities.join(", ")}`));
    root.append(row);
  }
}

function renderMap() {
  const root = clear(byId("map"));
  const nodes = state?.nodes || [];
  const roads = state?.roads || [];
  if (!nodes.length) {
    root.append(element("p", "empty-state", "No graph nodes available."));
    return;
  }
  const xs = nodes.map((node) => Number(node.x)).filter(Number.isFinite);
  const ys = nodes.map((node) => Number(node.y)).filter(Number.isFinite);
  const minX = Math.min(...xs, 0);
  const maxX = Math.max(...xs, 1);
  const minY = Math.min(...ys, 0);
  const maxY = Math.max(...ys, 1);
  const spanX = Math.max(maxX - minX, 1);
  const spanY = Math.max(maxY - minY, 1);
  const positions = new Map(nodes.map((node, index) => {
    const fallbackAngle = 2 * Math.PI * index / nodes.length;
    const rawX = Number.isFinite(Number(node.x)) ? Number(node.x) : minX + spanX * (0.5 + 0.45 * Math.cos(fallbackAngle));
    const rawY = Number.isFinite(Number(node.y)) ? Number(node.y) : minY + spanY * (0.5 + 0.45 * Math.sin(fallbackAngle));
    return [node.node_id, { x: 70 + (rawX - minX) / spanX * 560, y: 60 + (rawY - minY) / spanY * 300 }];
  }));

  const svg = svgElement("svg", { viewBox: "0 0 700 420", role: "img", "aria-label": "Synthetic road graph with proposed rescue routes" });
  const defs = svgElement("defs");
  const marker = svgElement("marker", { id: "road-arrow", markerWidth: 9, markerHeight: 9, refX: 7, refY: 4.5, orient: "auto" });
  marker.append(svgElement("path", { d: "M 0 0 L 9 4.5 L 0 9 z", fill: "#8fa3ad" }));
  defs.append(marker);
  svg.append(defs);

  for (const road of roads) {
    const from = positions.get(road.source_node_id);
    const to = positions.get(road.target_node_id);
    if (!from || !to) continue;
    const line = svgElement("line", {
      x1: from.x, y1: from.y, x2: to.x, y2: to.y,
      class: `graph-road graph-${String(road.status).toLowerCase()}`,
      "marker-end": road.status === "BLOCKED" ? "" : "url(#road-arrow)",
    });
    line.append(svgElement("title"));
    line.lastChild.textContent = `${road.edge_id}: ${road.status}`;
    svg.append(line);
  }

  for (const [index, assignment] of (state?.proposal?.plan?.assignments || []).entries()) {
    for (const edgeId of assignment.route?.edge_ids || []) {
      const road = roads.find((item) => item.edge_id === edgeId);
      const from = positions.get(road?.source_node_id);
      const to = positions.get(road?.target_node_id);
      if (!from || !to) continue;
      const line = svgElement("line", {
        x1: from.x, y1: from.y, x2: to.x, y2: to.y,
        class: "graph-route", stroke: ROUTE_COLORS[index % ROUTE_COLORS.length],
      });
      line.append(svgElement("title"));
      line.lastChild.textContent = `${assignment.team_id} → ${assignment.request_id}: ${edgeId}`;
      svg.append(line);
    }
  }

  const teamLocations = new Set((state?.teams || []).map((team) => team.graph_node_id));
  const incidentLocations = new Set((state?.reports || []).map((report) => report.reviewed_node_id || report.suggested_node_id));
  for (const node of nodes) {
    const point = positions.get(node.node_id);
    const group = svgElement("g");
    const role = teamLocations.has(node.node_id) ? "team" : incidentLocations.has(node.node_id) ? "incident" : "normal";
    group.append(svgElement("circle", { cx: point.x, cy: point.y, r: 15, class: `graph-node node-${role}` }));
    const label = svgElement("text", { x: point.x, y: point.y + 31, class: "graph-label", "text-anchor": "middle" });
    label.textContent = node.label || node.node_id;
    group.append(label);
    group.append(svgElement("title"));
    group.lastChild.textContent = `${node.label || node.node_id} (${node.node_id})`;
    svg.append(group);
  }
  root.append(svg);
}

function render() {
  if (!state) return;
  byId("revision").textContent = `Graph revision ${state.graph_revision}`;
  byId("map-revision").textContent = `Revision ${state.graph_revision}`;
  const selected = byId("suggested-node").value;
  fillNodeSelect(byId("suggested-node"), selected || state.incident_node_ids?.[0] || state.nodes?.[0]?.node_id);
  renderReports();
  renderProposal();
  renderRoads();
  renderTeams();
  renderMap();
}

byId("sos-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const text = form.elements.text.value.trim();
  const suggestedNode = form.elements.suggested_node_id.value;
  if (!text || !suggestedNode) return;
  await mutate("/sos", { text, suggested_node_id: suggestedNode }, "SOS submitted. Review the suggestions and approve dispatch inputs below.");
  if (!byId("feedback").classList.contains("is-error")) form.elements.text.value = "";
});

byId("reports").addEventListener("submit", async (event) => {
  const form = event.target.closest("form[data-action='review']");
  if (!form) return;
  event.preventDefault();
  const reviewerId = operatorId();
  if (!reviewerId) return;
  const weight = Number(form.elements.dispatch_weight.value);
  if (!Number.isSafeInteger(weight) || weight < 1 || weight > 5) {
    showFeedback("Dispatch weight must be a whole number from 1 to 5.", true);
    return;
  }
  await mutate(`/sos/${encodeURIComponent(form.dataset.id)}/review`, {
    incident_node_id: form.elements.incident_node_id.value,
    dispatch_weight: weight,
    required_capabilities: [...form.querySelectorAll("input[name='required_capabilities']:checked")].map((input) => input.value),
    reviewer_id: reviewerId,
  }, "Review saved. Dispatch proposal refreshed.");
});

byId("roads").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const actorId = operatorId();
  if (!actorId) return;
  const action = button.dataset.action;
  await mutate(`/roads/${encodeURIComponent(button.dataset.id)}/${action}`, { actor_id: actorId },
    `Road ${button.dataset.id} ${action === "block" ? "blocked" : "unblocked"}. Proposal and routes refreshed.`);
});

byId("proposal").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action='approve']");
  if (!button) return;
  const dispatcherId = operatorId();
  if (!dispatcherId) return;
  await mutate(`/proposals/${encodeURIComponent(button.dataset.id)}/approve`, { dispatcher_id: dispatcherId },
    "Current dispatch proposal approved by the dispatcher.");
});

byId("reset").addEventListener("click", async () => {
  await mutate("/reset", {}, "Synthetic demo reset to its initial state.");
});

async function initialize() {
  try {
    state = await request("/state");
    render();
    byId("connection").textContent = "Connected";
  } catch (error) {
    byId("connection").textContent = "Disconnected";
    byId("connection").classList.add("is-offline");
    showFeedback(`Could not load the demo: ${error.message}`, true);
  }
}

initialize();
