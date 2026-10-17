"use strict";

const state = {
  devices: [],
  syncing: false,
  backupsInProgress: new Set(),
  prophylaxisToken: "",
  prophylaxisRun: null,
  monitoringDevices: [],
  monitoringToken: "",
  monitoringJobs: new Map(),
  monitoringBusy: new Map(),
  monitoringPolling: new Set(),
};

const elements = {
  connection: document.querySelector("#connection-state"),
  refreshButton: document.querySelector("#refresh-button"),
  pageMessage: document.querySelector("#page-message"),
  lastRefreshed: document.querySelector("#last-refreshed"),
  inventoryHealth: document.querySelector("#inventory-health"),
  netboxCount: document.querySelector("#netbox-device-count"),
  runtimeCount: document.querySelector("#runtime-device-count"),
  issueCount: document.querySelector("#inventory-issue-count"),
  syncButton: document.querySelector("#sync-button"),
  resultEmpty: document.querySelector("#result-empty"),
  resultContent: document.querySelector("#result-content"),
  resultStatus: document.querySelector("#result-status"),
  resultEvents: document.querySelector("#result-events"),
  resultAdded: document.querySelector("#result-added"),
  resultUpdated: document.querySelector("#result-updated"),
  resultRemoved: document.querySelector("#result-removed"),
  resultUnchanged: document.querySelector("#result-unchanged"),
  resultSkipped: document.querySelector("#result-skipped"),
  resultErrors: document.querySelector("#result-errors"),
  deviceFilter: document.querySelector("#device-filter"),
  deviceSort: document.querySelector("#device-sort"),
  deviceTotal: document.querySelector("#device-total"),
  deviceTableBody: document.querySelector("#device-table-body"),
  toastRegion: document.querySelector("#toast-region"),
  prophylaxisToken: document.querySelector("#prophylaxis-token"),
  prophylaxisRun: document.querySelector("#prophylaxis-run"),
  prophylaxisStatus: document.querySelector("#prophylaxis-status"),
  prophylaxisSuccess: document.querySelector("#prophylaxis-success"),
  prophylaxisFailure: document.querySelector("#prophylaxis-failure"),
  prophylaxisSkipped: document.querySelector("#prophylaxis-skipped"),
  prophylaxisReport: document.querySelector("#prophylaxis-report"),
  monitoringToken: document.querySelector("#monitoring-token"),
  monitoringMode: document.querySelector("#monitoring-mode"),
  monitoringLoad: document.querySelector("#monitoring-load"),
  monitoringBulk: document.querySelector("#monitoring-bulk"),
  monitoringTotal: document.querySelector("#monitoring-total"),
  monitoringPlan: document.querySelector("#monitoring-plan"),
  monitoringActivity: document.querySelector("#monitoring-activity"),
  monitoringActivityTitle: document.querySelector("#monitoring-activity-title"),
  monitoringActivityDetail: document.querySelector("#monitoring-activity-detail"),
  monitoringTableBody: document.querySelector("#monitoring-table-body"),
};

const MONITORING_TERMINAL = new Set(["COMPLETED", "FAILED", "PARTIAL"]);
const MONITORING_VERIFICATION_TIMEOUT_MS = 120000;

class ApiError extends Error {
  constructor(status, data) {
    super("Atlas API request failed");
    this.status = status;
    this.data = data;
    this.code = data?.error?.code || "request_failed";
    this.safeMessage = data?.error?.message || "The operation could not be completed.";
  }
}

async function apiRequest(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      method: options.method || "GET",
      headers: {
        Accept: "application/json",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.headers || {}),
      },
      body: options.body ? JSON.stringify(options.body) : undefined,
      credentials: "same-origin",
    });
  } catch (_error) {
    throw new ApiError(0, {
      error: {
        code: "connection_failed",
        message: "Atlas API is not reachable.",
      },
    });
  }

  let data = null;
  try {
    data = await response.json();
  } catch (_error) {
    if (!response.ok) {
      throw new ApiError(response.status, null);
    }
  }
  if (!response.ok) {
    throw new ApiError(response.status, data);
  }
  return data;
}

function monitoringHeaders() {
  return { Authorization: `Bearer ${state.monitoringToken}` };
}

function monitoringMessage(message) {
  const row = document.createElement("tr");
  const cell = document.createElement("td");
  cell.colSpan = 5;
  cell.className = "table-message";
  cell.textContent = message;
  row.append(cell);
  elements.monitoringTableBody.replaceChildren(row);
}

function monitoringPresentation(job) {
  if (!job) return null;
  if (job.status === "INSPECTING") {
    return { label: "Inspecting", detail: "Reading device and Zabbix state.", tone: "neutral" };
  }
  if (job.status === "PLANNING") {
    return { label: "Planning", detail: "Calculating safe changes.", tone: "neutral" };
  }
  if (job.status === "PENDING") {
    return { label: "Queued", detail: "Atlas accepted the job.", tone: "neutral" };
  }
  if (job.status === "VALIDATING") {
    return { label: "Validating", detail: "Checking the approved plan and current state.", tone: "neutral" };
  }
  if (job.status === "CONFIGURING_DEVICE") {
    return { label: "Configuring device", detail: "Applying and reading back the SNMP policy.", tone: "neutral" };
  }
  if (job.status === "CONFIGURING_ZABBIX") {
    return { label: "Configuring Zabbix", detail: "Creating or updating the monitored host.", tone: "neutral" };
  }
  if (job.status === "VERIFYING") {
    return { label: "Verifying", detail: "Waiting for a fresh SNMP sysObjectID sample.", tone: "degraded" };
  }
  if (job.status === "COMPLETED" && job.verification === "VERIFIED") {
    return { label: "Verified", detail: "Zabbix collected a fresh SNMP sample.", tone: "healthy" };
  }
  if (job.status === "COMPLETED" && job.verification === "PENDING") {
    return { label: "Waiting for SNMP data", detail: "Zabbix is configured; the first successful sample is still pending.", tone: "degraded" };
  }
  if (job.status === "PARTIAL") {
    return { label: "Partially applied", detail: titleCase(job.error_code || "operation stopped"), tone: "unhealthy" };
  }
  if (job.status === "FAILED") {
    return { label: "Failed", detail: titleCase(job.error_code || "operation failed"), tone: "unhealthy" };
  }
  return { label: titleCase(job.status), detail: titleCase(job.current_step || "processing"), tone: "neutral" };
}

function renderMonitoringActivity() {
  const busy = [...state.monitoringBusy.entries()].map(([deviceId, status]) => ({ deviceId, status }));
  const polling = [...state.monitoringPolling].map((deviceId) => ({
    deviceId,
    status: state.monitoringJobs.get(deviceId),
  }));
  const active = busy.length ? busy : polling;
  if (!active.length) {
    elements.monitoringActivity.hidden = true;
    return;
  }
  const current = active[0];
  const device = state.monitoringDevices.find((candidate) => candidate.id === current.deviceId);
  const presentation = monitoringPresentation(current.status);
  elements.monitoringActivityTitle.textContent = active.length === 1
    ? `${device?.name || `Device ${current.deviceId}`}: ${presentation.label}`
    : `${active.length} monitoring operations in progress`;
  elements.monitoringActivityDetail.textContent = active.length === 1
    ? presentation.detail
    : "Atlas is processing the selected devices and will report each result.";
  elements.monitoringActivity.hidden = false;
}

function setMonitoringBusy(deviceId, status = null) {
  if (status) state.monitoringBusy.set(deviceId, { status });
  else state.monitoringBusy.delete(deviceId);
  renderMonitoringDevices();
  renderMonitoringActivity();
}

function renderMonitoringDevices() {
  elements.monitoringTotal.textContent = `${state.monitoringDevices.length} devices`;
  elements.monitoringBulk.disabled = !state.monitoringDevices.some((device) => !device.error_code);
  if (!state.monitoringDevices.length) {
    monitoringMessage("No monitoring-enabled devices were returned by NetBox.");
    return;
  }
  const rows = state.monitoringDevices.map((device) => {
    const row = document.createElement("tr");
    for (const value of [device.name || `Device ${device.id}`, device.address || "—", device.platform || "—"]) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    const status = document.createElement("td");
    const currentJob = state.monitoringBusy.get(device.id) || state.monitoringJobs.get(device.id);
    const presentation = monitoringPresentation(currentJob);
    if (device.error_code) {
      status.textContent = titleCase(device.error_code);
    } else if (presentation) {
      const wrapper = document.createElement("div");
      wrapper.className = "monitoring-status";
      const pill = document.createElement("span");
      pill.className = `status-pill status-pill-${presentation.tone}`;
      pill.textContent = presentation.label;
      const detail = document.createElement("p");
      detail.className = "monitoring-status-detail";
      detail.textContent = presentation.detail;
      wrapper.append(pill, detail);
      status.append(wrapper);
    } else {
      status.textContent = titleCase(device.provisioning_status);
    }
    row.append(status);
    const action = document.createElement("td");
    action.className = "action-cell monitoring-actions";
    const inspectButton = document.createElement("button");
    inspectButton.type = "button";
    inspectButton.className = "button button-compact";
    inspectButton.textContent = "Inspect";
    const operationActive = state.monitoringBusy.has(device.id) || state.monitoringPolling.has(device.id);
    inspectButton.disabled = Boolean(device.error_code) || operationActive;
    inspectButton.addEventListener("click", () => inspectMonitoringDevice(device));
    const applyButton = document.createElement("button");
    applyButton.type = "button";
    applyButton.className = "button button-compact button-primary";
    applyButton.textContent = "Plan & reconcile";
    applyButton.disabled = Boolean(device.error_code) || operationActive;
    applyButton.addEventListener("click", () => planMonitoringDevice(device));
    action.append(inspectButton, applyButton);
    const savedJob = state.monitoringJobs.get(device.id);
    if (
      savedJob?.status === "COMPLETED"
      && savedJob.verification === "PENDING"
      && !state.monitoringPolling.has(device.id)
    ) {
      const verifyButton = document.createElement("button");
      verifyButton.type = "button";
      verifyButton.className = "button button-compact";
      verifyButton.textContent = "Check verification";
      verifyButton.addEventListener("click", () => {
        state.monitoringPolling.add(device.id);
        renderMonitoringDevices();
        renderMonitoringActivity();
        toast(`${device.name}: checking for a fresh SNMP sample.`, "info");
        pollMonitoringJob(
          device,
          savedJob.job_id,
          Date.now() + MONITORING_VERIFICATION_TIMEOUT_MS,
        );
      });
      action.append(verifyButton);
    }
    row.append(action);
    return row;
  });
  elements.monitoringTableBody.replaceChildren(...rows);
}

async function monitoringRequest(path, options = {}) {
  return apiRequest(path, { ...options, headers: monitoringHeaders() });
}

function showMonitoringPlan(value) {
  elements.monitoringPlan.hidden = false;
  elements.monitoringPlan.textContent = JSON.stringify(value, null, 2);
}

async function loadMonitoringDevices() {
  state.monitoringToken = elements.monitoringToken.value.trim();
  if (!state.monitoringToken) {
    toast("Enter the monitoring operator token.", "warning");
    return;
  }
  elements.monitoringLoad.disabled = true;
  try {
    state.monitoringDevices = await monitoringRequest("/api/monitoring/devices");
    renderMonitoringDevices();
  } catch (error) {
    monitoringMessage(error.safeMessage || "Monitoring devices are unavailable.");
    toast(error.safeMessage || "Monitoring authorization failed.", "error");
  } finally {
    elements.monitoringLoad.disabled = false;
  }
}

async function inspectMonitoringDevice(device) {
  setMonitoringBusy(device.id, "INSPECTING");
  toast(`${device.name}: inspection started.`, "info");
  try {
    const result = await monitoringRequest(`/api/monitoring/devices/${device.id}/inspect`, {
      method: "POST",
      body: { mode: elements.monitoringMode.value },
    });
    showMonitoringPlan(result);
    toast(`${device.name}: inspection completed.`, "success");
  } catch (error) {
    toast(error.safeMessage || "Inspection failed.", "error");
  } finally {
    setMonitoringBusy(device.id);
  }
}

async function planMonitoringDevice(device) {
  setMonitoringBusy(device.id, "PLANNING");
  try {
    const mode = elements.monitoringMode.value;
    const plan = await monitoringRequest(`/api/monitoring/devices/${device.id}/plan`, {
      method: "POST", body: { mode },
    });
    showMonitoringPlan(plan);
    const changes = plan.planned_changes?.length ? plan.planned_changes.join("\n• ") : "No changes required";
    if (!window.confirm(`Apply this current plan for ${device.name}?\n\n• ${changes}`)) {
      setMonitoringBusy(device.id);
      return;
    }
    const result = await monitoringRequest(`/api/monitoring/devices/${device.id}/reconcile`, {
      method: "POST", body: { mode, plan_id: plan.plan_id, confirmed: true },
    });
    state.monitoringBusy.delete(device.id);
    state.monitoringJobs.set(device.id, result);
    state.monitoringPolling.add(device.id);
    renderMonitoringDevices();
    renderMonitoringActivity();
    toast(`${device.name}: reconciliation started.`, "info");
    pollMonitoringJob(device, result.job_id, Date.now() + MONITORING_VERIFICATION_TIMEOUT_MS);
  } catch (error) {
    toast(error.safeMessage || "Monitoring plan failed.", "error");
    setMonitoringBusy(device.id);
  }
}

async function pollMonitoringJob(device, jobId, verificationDeadline) {
  try {
    const job = await monitoringRequest(`/api/monitoring/jobs/${jobId}`);
    state.monitoringJobs.set(device.id, job);
    renderMonitoringDevices();
    renderMonitoringActivity();
    showMonitoringPlan(job);
    const waitingForVerification = job.status === "COMPLETED" && job.verification === "PENDING";
    if (!MONITORING_TERMINAL.has(job.status) || (waitingForVerification && Date.now() < verificationDeadline)) {
      window.setTimeout(() => pollMonitoringJob(device, jobId, verificationDeadline), waitingForVerification ? 3000 : 1500);
    } else {
      state.monitoringPolling.delete(device.id);
      renderMonitoringDevices();
      renderMonitoringActivity();
      if (job.status === "COMPLETED" && job.verification === "VERIFIED") {
        toast(`${device.name}: monitoring verified by a fresh SNMP sample.`, "success");
      } else if (waitingForVerification) {
        toast(`${device.name}: Zabbix is configured, but the first SNMP sample is still pending.`, "warning");
      } else {
        toast(`${device.name}: ${titleCase(job.status)} (${titleCase(job.error_code)}).`, "error");
      }
    }
  } catch (error) {
    state.monitoringPolling.delete(device.id);
    renderMonitoringDevices();
    renderMonitoringActivity();
    toast(error.safeMessage || "Could not read monitoring job.", "error");
  }
}

async function planAllMonitoringDevices() {
  const devices = state.monitoringDevices.filter((device) => !device.error_code);
  if (!devices.length) return;
  elements.monitoringBulk.disabled = true;
  toast(`Preparing monitoring plans for ${devices.length} devices.`, "info");
  try {
    const mode = elements.monitoringMode.value;
    const plans = [];
    for (const device of devices) {
      const plan = await monitoringRequest(`/api/monitoring/devices/${device.id}/plan`, { method: "POST", body: { mode } });
      plans.push({ device, plan });
    }
    showMonitoringPlan(plans.map(({ device, plan }) => ({ device: device.name, planned_changes: plan.planned_changes })));
    if (!window.confirm(`Apply ${plans.length} current monitoring plans?`)) return;
    const result = await monitoringRequest("/api/monitoring/provision/bulk", {
      method: "POST",
      body: { plans: plans.map(({ plan }) => ({ mode, plan_id: plan.plan_id, confirmed: true })) },
    });
    for (const item of result.jobs) {
      const device = devices.find((candidate) => candidate.id === item.device_id);
      state.monitoringJobs.set(item.device_id, item);
      state.monitoringPolling.add(item.device_id);
      pollMonitoringJob(device, item.job_id, Date.now() + MONITORING_VERIFICATION_TIMEOUT_MS);
    }
    renderMonitoringDevices();
    renderMonitoringActivity();
    toast(`${result.jobs.length} monitoring jobs started.`, "info");
  } catch (error) {
    toast(error.safeMessage || "Bulk monitoring plan failed.", "error");
  } finally {
    elements.monitoringBulk.disabled = false;
  }
}

function titleCase(value) {
  const labels = {
    ambiguous_service_port: "Ambiguous service port",
    api_token_missing_or_invalid: "API token missing or invalid",
    invalid_service_port: "Invalid service port",
  };
  if (labels[value]) return labels[value];
  return String(value || "unknown")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function statusClass(status) {
  return ["healthy", "degraded", "unhealthy"].includes(status)
    ? status
    : "unknown";
}

function setConnection(status, text) {
  const dot = document.createElement("span");
  dot.className = `status-dot status-${statusClass(status)}`;
  dot.setAttribute("aria-hidden", "true");
  elements.connection.replaceChildren(dot, document.createTextNode(text));
}

function setPageMessage(message = "") {
  elements.pageMessage.textContent = message;
  elements.pageMessage.hidden = !message;
}

function setPill(element, status, text = titleCase(status)) {
  element.className = `status-pill status-pill-${statusClass(status)}`;
  element.textContent = text;
}

function renderPlatformStatus(payload) {
  for (const name of ["atlas", "openbao", "netbox", "oxidized", "zabbix"]) {
    const card = document.querySelector(`[data-component="${name}"]`);
    const target = card.querySelector(".component-status");
    const componentStatus = payload.components?.[name]?.status || "unknown";
    const componentUrl = payload.components?.[name]?.url;
    const dot = document.createElement("span");
    dot.className = `status-dot status-${statusClass(componentStatus)}`;
    dot.setAttribute("aria-hidden", "true");
    target.replaceChildren(dot, document.createTextNode(titleCase(componentStatus)));
    const link = card.querySelector(".component-link");
    if (link) {
      if (componentUrl) {
        link.href = componentUrl;
        link.hidden = false;
        link.setAttribute("aria-label", `Open ${card.querySelector("h3").textContent} in a new tab`);
      } else {
        link.removeAttribute("href");
        link.hidden = true;
      }
    }
  }
  setConnection(payload.status, titleCase(payload.status));
}

function renderPlatformUnavailable() {
  for (const name of ["atlas", "openbao", "netbox", "oxidized", "zabbix"]) {
    const target = document.querySelector(
      `[data-component="${name}"] .component-status`,
    );
    const dot = document.createElement("span");
    dot.className = "status-dot status-unknown";
    dot.setAttribute("aria-hidden", "true");
    target.replaceChildren(dot, document.createTextNode("Unavailable"));
  }
  setConnection("unhealthy", "API unavailable");
}

function renderOxidizedStatus(payload) {
  setPill(elements.inventoryHealth, payload.status);
  elements.netboxCount.textContent = String(payload.netbox_enabled_devices);
  elements.runtimeCount.textContent = String(payload.runtime_inventory_devices);
  elements.issueCount.textContent = String(payload.inventory_issues);
}

function renderOxidizedStatusUnavailable() {
  setPill(elements.inventoryHealth, "unhealthy", "Unavailable");
  elements.netboxCount.textContent = "—";
  elements.runtimeCount.textContent = "—";
  elements.issueCount.textContent = "—";
}

function visibleDevices() {
  const query = elements.deviceFilter.value.trim().toLocaleLowerCase();
  const field = elements.deviceSort.value;
  return state.devices
    .filter((device) => {
      if (!query) return true;
      return [device.name, device.ip, device.model].some((value) =>
        value.toLocaleLowerCase().includes(query),
      );
    })
    .sort((left, right) =>
      left[field].localeCompare(right[field], undefined, {
        numeric: true,
        sensitivity: "base",
      }),
    );
}

function tableMessage(message) {
  const row = document.createElement("tr");
  const cell = document.createElement("td");
  cell.colSpan = 4;
  cell.className = "table-message";
  cell.textContent = message;
  row.append(cell);
  elements.deviceTableBody.replaceChildren(row);
}

function renderDevices() {
  const devices = visibleDevices();
  const filterActive = Boolean(elements.deviceFilter.value.trim());
  elements.deviceTotal.textContent = filterActive
    ? `${devices.length} of ${state.devices.length} devices`
    : `${state.devices.length} ${state.devices.length === 1 ? "device" : "devices"}`;

  if (!devices.length) {
    tableMessage(filterActive ? "No devices match the filter." : "No runtime devices are available.");
    return;
  }

  const rows = devices.map((device) => {
    const row = document.createElement("tr");

    const name = document.createElement("td");
    name.className = "device-name";
    name.textContent = device.name;

    const address = document.createElement("td");
    address.textContent = device.ip;

    const modelCell = document.createElement("td");
    const model = document.createElement("span");
    model.className = "device-model";
    model.textContent = device.model;
    modelCell.append(model);

    const action = document.createElement("td");
    action.className = "action-cell";
    const button = document.createElement("button");
    const busy = state.backupsInProgress.has(device.name);
    button.type = "button";
    button.className = "button button-compact";
    button.dataset.device = device.name;
    button.disabled = busy;
    button.textContent = busy ? "Queueing…" : "Queue backup";
    button.setAttribute("aria-label", `Queue backup for ${device.name}`);
    button.addEventListener("click", () => queueBackup(device.name));
    action.append(button);

    row.append(name, address, modelCell, action);
    return row;
  });
  elements.deviceTableBody.replaceChildren(...rows);
}

function renderDevicesUnavailable() {
  state.devices = [];
  elements.deviceTotal.textContent = "Unavailable";
  tableMessage("The runtime device list is unavailable.");
}

function renderProphylaxisRun(run = state.prophylaxisRun) {
  const status = run?.status || "IDLE";
  const visualStatus = status === "COMPLETED"
    ? "healthy"
    : status === "PARTIAL"
      ? "degraded"
      : status === "FAILED"
        ? "unhealthy"
        : "unknown";
  setPill(elements.prophylaxisStatus, visualStatus, status);
  elements.prophylaxisSuccess.textContent = String(run?.summary?.success ?? 0);
  elements.prophylaxisFailure.textContent = String(run?.summary?.failure ?? 0);
  elements.prophylaxisSkipped.textContent = String(run?.summary?.skipped ?? 0);
  elements.prophylaxisReport.textContent = run
    ? `Run ${run.run_id} · Report: ${run.report_path}`
    : "No run has been launched.";
  elements.prophylaxisRun.disabled = ["PENDING", "RUNNING"].includes(status);
}

function addResultEvent(symbol, kind, device, detail = "") {
  const item = document.createElement("li");
  const marker = document.createElement("span");
  marker.className = "event-kind";
  marker.textContent = symbol;
  marker.setAttribute("aria-label", kind);
  const text = document.createElement("span");
  text.textContent = `${device}${detail ? ` — ${detail}` : ""}`;
  item.append(marker, text);
  elements.resultEvents.append(item);
}

function renderSyncResult(result) {
  elements.resultEmpty.hidden = true;
  elements.resultContent.hidden = false;
  setPill(
    elements.resultStatus,
    result.status === "success"
      ? "healthy"
      : result.status === "partial_success"
        ? "degraded"
        : "unhealthy",
    titleCase(result.status),
  );

  const summary = result.summary || {};
  elements.resultAdded.textContent = String(summary.added ?? 0);
  elements.resultUpdated.textContent = String(summary.updated ?? 0);
  elements.resultRemoved.textContent = String(summary.removed ?? 0);
  elements.resultUnchanged.textContent = String(summary.unchanged ?? 0);
  elements.resultSkipped.textContent = String(summary.skipped ?? 0);
  elements.resultErrors.textContent = String(summary.errors ?? 0);
  elements.resultEvents.replaceChildren();

  for (const device of result.added || []) addResultEvent("+", "Added", device, "Added");
  for (const device of result.updated || []) addResultEvent("~", "Updated", device, "Updated");
  for (const device of result.removed || []) addResultEvent("−", "Removed", device, "Removed");
  for (const issue of result.issues || []) {
    addResultEvent("!", "Issue", issue.device || "Oxidized", titleCase(issue.code));
  }

  if (!elements.resultEvents.childElementCount) {
    addResultEvent("=", "No changes", "Inventory", "No changes required");
  }
}

function toast(message, type = "success") {
  const item = document.createElement("div");
  item.className = `toast toast-${type}`;
  item.textContent = message;
  elements.toastRegion.append(item);
  window.setTimeout(() => item.remove(), 5000);
}

function setSyncBusy(busy) {
  state.syncing = busy;
  elements.syncButton.disabled = busy;
  elements.syncButton.classList.toggle("is-busy", busy);
  elements.syncButton.querySelector(".button-label").textContent = busy
    ? "Syncing inventory"
    : "Sync inventory";
}

async function loadDashboard() {
  elements.refreshButton.disabled = true;
  setPageMessage();
  const [platform, overview, devices] = await Promise.allSettled([
    apiRequest("/api/status"),
    apiRequest("/api/oxidized/status"),
    apiRequest("/api/oxidized/devices"),
  ]);

  if (platform.status === "fulfilled") {
    renderPlatformStatus(platform.value);
  } else {
    renderPlatformUnavailable();
  }

  if (overview.status === "fulfilled") {
    renderOxidizedStatus(overview.value);
  } else {
    renderOxidizedStatusUnavailable();
  }

  if (devices.status === "fulfilled") {
    state.devices = devices.value;
    renderDevices();
  } else {
    renderDevicesUnavailable();
  }

  if ([platform, overview, devices].some(
    (result) => result.status === "rejected",
  )) {
    setPageMessage("Some operational data is currently unavailable. Refresh after the dependency recovers.");
  }
  elements.lastRefreshed.textContent = `Refreshed ${new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date())}`;
  elements.refreshButton.disabled = false;
}

function prophylaxisHeaders() {
  return { Authorization: `Bearer ${state.prophylaxisToken}` };
}

async function pollProphylaxisRun() {
  const run = state.prophylaxisRun;
  if (!run || !["PENDING", "RUNNING"].includes(run.status)) return;
  try {
    state.prophylaxisRun = await apiRequest(
      `/api/prophylaxis/runs/${run.run_id}`,
      { headers: prophylaxisHeaders() },
    );
    renderProphylaxisRun();
    if (["PENDING", "RUNNING"].includes(state.prophylaxisRun.status)) {
      window.setTimeout(pollProphylaxisRun, 1500);
    } else {
      toast(`Profylaxia finished with status ${state.prophylaxisRun.status}.`,
        state.prophylaxisRun.status === "COMPLETED" ? "success" : "warning");
    }
  } catch (error) {
    elements.prophylaxisRun.disabled = false;
    toast(error.safeMessage || "Could not read the Profylaxia run.", "error");
  }
}

async function launchProphylaxisRun() {
  const token = elements.prophylaxisToken.value.trim();
  if (!token) {
    toast("Enter the operator token.", "warning");
    return;
  }
  state.prophylaxisToken = token;
  elements.prophylaxisRun.disabled = true;
  try {
    state.prophylaxisRun = await apiRequest("/api/prophylaxis/runs", {
      method: "POST",
      headers: prophylaxisHeaders(),
    });
    renderProphylaxisRun();
    window.setTimeout(pollProphylaxisRun, 500);
  } catch (error) {
    elements.prophylaxisRun.disabled = false;
    toast(error.safeMessage || "Could not launch Profylaxia.", "error");
  }
}

async function synchronizeInventory() {
  if (state.syncing) return;
  setSyncBusy(true);
  setPageMessage();
  try {
    const result = await apiRequest("/api/oxidized/sync", { method: "POST" });
    renderSyncResult(result);
    toast(
      result.status === "partial_success"
        ? "Inventory synchronized with reported issues."
        : "Inventory synchronization completed.",
      result.status === "partial_success" ? "warning" : "success",
    );
    await loadDashboard();
  } catch (error) {
    if (error instanceof ApiError && error.data?.status === "error") {
      renderSyncResult(error.data);
      toast("Inventory was updated, but Oxidized reload failed.", "error");
      await loadDashboard();
    } else if (error instanceof ApiError && error.code === "sync_already_running") {
      toast("Another inventory synchronization is already running.", "warning");
    } else {
      toast(error.safeMessage || "Inventory synchronization failed.", "error");
    }
  } finally {
    setSyncBusy(false);
  }
}

async function queueBackup(device) {
  if (state.backupsInProgress.has(device)) return;
  state.backupsInProgress.add(device);
  renderDevices();
  try {
    const result = await apiRequest(
      `/api/oxidized/devices/${encodeURIComponent(device)}/backup`,
      { method: "POST" },
    );
    toast(`${result.device} was queued for Oxidized processing.`, "success");
  } catch (error) {
    toast(error.safeMessage || `Could not queue ${device}.`, "error");
  } finally {
    state.backupsInProgress.delete(device);
    renderDevices();
  }
}

elements.refreshButton.addEventListener("click", loadDashboard);
elements.syncButton.addEventListener("click", synchronizeInventory);
elements.deviceFilter.addEventListener("input", renderDevices);
elements.deviceSort.addEventListener("change", renderDevices);
elements.prophylaxisRun.addEventListener("click", launchProphylaxisRun);
elements.monitoringLoad.addEventListener("click", loadMonitoringDevices);
elements.monitoringBulk.addEventListener("click", planAllMonitoringDevices);

loadDashboard();
