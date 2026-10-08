"use strict";

const state = {
  devices: [],
  syncing: false,
  backupsInProgress: new Set(),
  prophylaxisDevices: [],
  prophylaxisResults: [],
  checksInProgress: new Set(),
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
  prophylaxisTotal: document.querySelector("#prophylaxis-total"),
  prophylaxisTableBody: document.querySelector("#prophylaxis-table-body"),
};

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
      headers: { Accept: "application/json" },
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
    const dot = document.createElement("span");
    dot.className = `status-dot status-${statusClass(componentStatus)}`;
    dot.setAttribute("aria-hidden", "true");
    target.replaceChildren(dot, document.createTextNode(titleCase(componentStatus)));
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

function latestResult(device) {
  return state.prophylaxisResults.find((result) => result.device === device);
}

function resultText(result) {
  if (!result) return "Not run";
  if (result.status === "ok") {
    return `${Number(result.values.current_percent).toFixed(1)}% CPU`;
  }
  return titleCase(result.error_code);
}

function resultTime(result) {
  if (!result) return "—";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "short",
    timeStyle: "medium",
  }).format(new Date(result.collected_at));
}

function prophylaxisTableMessage(message) {
  const row = document.createElement("tr");
  const cell = document.createElement("td");
  cell.colSpan = 6;
  cell.className = "table-message";
  cell.textContent = message;
  row.append(cell);
  elements.prophylaxisTableBody.replaceChildren(row);
}

function renderProphylaxisDevices() {
  elements.prophylaxisTotal.textContent = `${state.prophylaxisDevices.length} ${
    state.prophylaxisDevices.length === 1 ? "device" : "devices"
  }`;
  if (!state.prophylaxisDevices.length) {
    prophylaxisTableMessage("No active device has the CPU utilization check enabled.");
    return;
  }

  const rows = state.prophylaxisDevices.map((device) => {
    const row = document.createElement("tr");
    const name = document.createElement("td");
    name.className = "device-name";
    name.textContent = device.name;
    const address = document.createElement("td");
    address.textContent = device.ip;
    const platform = document.createElement("td");
    const platformTag = document.createElement("span");
    platformTag.className = "device-model";
    platformTag.textContent = device.platform;
    platform.append(platformTag);

    const stored = latestResult(device.name);
    const result = document.createElement("td");
    result.className = stored && stored.status !== "ok" ? "check-result check-error" : "check-result";
    result.textContent = resultText(stored);
    const collected = document.createElement("td");
    collected.className = "muted";
    collected.textContent = resultTime(stored);

    const action = document.createElement("td");
    action.className = "action-cell";
    const button = document.createElement("button");
    const busy = state.checksInProgress.has(device.id);
    button.type = "button";
    button.className = "button button-compact button-check";
    button.disabled = busy;
    button.textContent = busy ? "Checking…" : "Run CPU check";
    button.setAttribute("aria-label", `Run CPU utilization check for ${device.name}`);
    button.addEventListener("click", () => runCpuCheck(device));
    action.append(button);
    row.append(name, address, platform, result, collected, action);
    return row;
  });
  elements.prophylaxisTableBody.replaceChildren(...rows);
}

function renderProphylaxisUnavailable() {
  state.prophylaxisDevices = [];
  elements.prophylaxisTotal.textContent = "Unavailable";
  prophylaxisTableMessage("Profylaxia devices or local result history are unavailable.");
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
  const [platform, overview, devices, prophylaxisDevices, prophylaxisResults] = await Promise.allSettled([
    apiRequest("/api/status"),
    apiRequest("/api/oxidized/status"),
    apiRequest("/api/oxidized/devices"),
    apiRequest("/api/prophylaxis/devices"),
    apiRequest("/api/prophylaxis/results?limit=50"),
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

  if (prophylaxisDevices.status === "fulfilled" && prophylaxisResults.status === "fulfilled") {
    state.prophylaxisDevices = prophylaxisDevices.value;
    state.prophylaxisResults = prophylaxisResults.value;
    renderProphylaxisDevices();
  } else {
    renderProphylaxisUnavailable();
  }

  if ([platform, overview, devices, prophylaxisDevices, prophylaxisResults].some(
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

async function runCpuCheck(device) {
  if (state.checksInProgress.has(device.id)) return;
  state.checksInProgress.add(device.id);
  renderProphylaxisDevices();
  try {
    const result = await apiRequest(
      `/api/prophylaxis/devices/${device.id}/checks/cpu`,
      { method: "POST" },
    );
    state.prophylaxisResults = [
      result,
      ...state.prophylaxisResults.filter((item) => item.id !== result.id),
    ].slice(0, 50);
    renderProphylaxisDevices();
    toast(
      result.status === "ok"
        ? `${result.device}: ${Number(result.values.current_percent).toFixed(1)}% CPU.`
        : `${result.device}: ${titleCase(result.error_code)}.`,
      result.status === "ok" ? "success" : "error",
    );
  } catch (error) {
    toast(error.safeMessage || `Could not check ${device.name}.`, "error");
  } finally {
    state.checksInProgress.delete(device.id);
    renderProphylaxisDevices();
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

loadDashboard();
