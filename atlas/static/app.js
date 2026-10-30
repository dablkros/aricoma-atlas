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
  monitoringBulkPlans: [],
  monitoringBulkActive: false,
  operatorToken: "",
  inventoryPlan: null,
  inventoryBusy: false,
  inventoryActivityTimer: null,
  inventoryActivityStarted: 0,
  inventoryActivityKind: null,
  inventoryStructurePlan: null,
  credentialOptions: null,
  credentialPlan: null,
  credentialBusy: false,
  credentialSelectedDeviceIds: new Set(),
  vpnAssignmentOptions: null,
  vpnAssignmentPlan: null,
  vpnAssignmentBusy: false,
  vpnAssignmentSelectedIds: new Set(),
  authenticated: false,
  principal: null,
  adminExpiryTimer: null,
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
  prophylaxisWarning: document.querySelector("#prophylaxis-warning"),
  prophylaxisUnknown: document.querySelector("#prophylaxis-unknown"),
  prophylaxisFailure: document.querySelector("#prophylaxis-failure"),
  prophylaxisSkipped: document.querySelector("#prophylaxis-skipped"),
  prophylaxisReport: document.querySelector("#prophylaxis-report"),
  prophylaxisResults: document.querySelector("#prophylaxis-results"),
  prophylaxisCharts: document.querySelector("#prophylaxis-charts"),
  monitoringToken: document.querySelector("#monitoring-token"),
  monitoringMode: document.querySelector("#monitoring-mode"),
  monitoringLoad: document.querySelector("#monitoring-load"),
  monitoringBulk: document.querySelector("#monitoring-bulk"),
  monitoringBulkPreview: document.querySelector("#monitoring-bulk-preview"),
  monitoringBulkPreviewSummary: document.querySelector("#monitoring-bulk-preview-summary"),
  monitoringBulkPreviewMode: document.querySelector("#monitoring-bulk-preview-mode"),
  monitoringBulkPreviewBody: document.querySelector("#monitoring-bulk-preview-body"),
  monitoringBulkCancel: document.querySelector("#monitoring-bulk-cancel"),
  monitoringBulkApply: document.querySelector("#monitoring-bulk-apply"),
  monitoringTotal: document.querySelector("#monitoring-total"),
  monitoringPlan: document.querySelector("#monitoring-plan"),
  monitoringActivity: document.querySelector("#monitoring-activity"),
  monitoringActivityTitle: document.querySelector("#monitoring-activity-title"),
  monitoringActivityDetail: document.querySelector("#monitoring-activity-detail"),
  monitoringTableBody: document.querySelector("#monitoring-table-body"),
  operatorToken: document.querySelector("#operator-token"),
  operatorStatus: document.querySelector("#operator-status"),
  operatorResult: document.querySelector("#operator-result"),
  ciscoTest: document.querySelector("#cisco-test"),
  zabbixSync: document.querySelector("#zabbix-sync"),
  inventoryImportOpen: document.querySelector("#inventory-import-open"),
  inventoryImportDialog: document.querySelector("#inventory-import-dialog"),
  inventoryImportClose: document.querySelector("#inventory-import-close"),
  inventoryTenant: document.querySelector("#inventory-tenant"),
  inventorySite: document.querySelector("#inventory-site"),
  inventoryTable: document.querySelector("#inventory-table"),
  inventoryExample: document.querySelector("#inventory-example"),
  inventoryClear: document.querySelector("#inventory-clear"),
  inventoryPlanButton: document.querySelector("#inventory-plan"),
  inventoryPlanLabel: document.querySelector("#inventory-plan-label"),
  inventoryActivity: document.querySelector("#inventory-activity"),
  inventoryActivityTitle: document.querySelector("#inventory-activity-title"),
  inventoryActivityDetail: document.querySelector("#inventory-activity-detail"),
  inventoryActivityTime: document.querySelector("#inventory-activity-time"),
  inventoryPlanResult: document.querySelector("#inventory-plan-result"),
  inventoryPlanStatus: document.querySelector("#inventory-plan-status"),
  inventoryCreateCount: document.querySelector("#inventory-create-count"),
  inventoryUnchangedCount: document.querySelector("#inventory-unchanged-count"),
  inventoryInvalidCount: document.querySelector("#inventory-invalid-count"),
  inventoryConflictCount: document.querySelector("#inventory-conflict-count"),
  inventoryErrorCount: document.querySelector("#inventory-error-count"),
  inventoryPlanMessage: document.querySelector("#inventory-plan-message"),
  inventoryPlanRows: document.querySelector("#inventory-plan-rows"),
  inventoryDeviceTypeResolution: document.querySelector("#inventory-device-type-resolution"),
  inventoryDeviceTypeMessage: document.querySelector("#inventory-device-type-message"),
  inventoryDeviceTypeModel: document.querySelector("#inventory-device-type-model"),
  inventoryDeviceTypeFilename: document.querySelector("#inventory-device-type-filename"),
  inventoryDeviceTypeYaml: document.querySelector("#inventory-device-type-yaml"),
  inventoryDeviceTypeError: document.querySelector("#inventory-device-type-error"),
  inventoryDeviceTypeReplace: document.querySelector("#inventory-device-type-replace"),
  inventoryDeviceTypeUpload: document.querySelector("#inventory-device-type-upload"),
  inventoryRetry: document.querySelector("#inventory-retry"),
  inventoryApplyConfirm: document.querySelector("#inventory-apply-confirm"),
  inventoryApply: document.querySelector("#inventory-apply"),
  inventoryStructureOpen: document.querySelector("#inventory-structure-open"),
  inventoryStructureDialog: document.querySelector("#inventory-structure-dialog"),
  inventoryStructureClose: document.querySelector("#inventory-structure-close"),
  inventoryStructureKind: document.querySelector("#inventory-structure-kind"),
  inventoryStructureTenantField: document.querySelector("#inventory-structure-tenant-field"),
  inventoryStructureTenant: document.querySelector("#inventory-structure-tenant"),
  inventoryStructureName: document.querySelector("#inventory-structure-name"),
  inventoryStructureSlugField: document.querySelector("#inventory-structure-slug-field"),
  inventoryStructureSlug: document.querySelector("#inventory-structure-slug"),
  inventoryStructureGroupField: document.querySelector("#inventory-structure-group-field"),
  inventoryStructureGroup: document.querySelector("#inventory-structure-group"),
  inventoryStructureResult: document.querySelector("#inventory-structure-result"),
  inventoryStructureConfirm: document.querySelector("#inventory-structure-confirm"),
  inventoryStructurePlan: document.querySelector("#inventory-structure-plan"),
  inventoryStructureApply: document.querySelector("#inventory-structure-apply"),
  credentialOpen: document.querySelector("#credential-onboarding-open"),
  credentialDialog: document.querySelector("#credential-onboarding-dialog"),
  credentialClose: document.querySelector("#credential-onboarding-close"),
  credentialTenant: document.querySelector("#credential-tenant"),
  credentialManufacturer: document.querySelector("#credential-manufacturer"),
  credentialDeviceFilter: document.querySelector("#credential-device-filter"),
  credentialDeviceList: document.querySelector("#credential-device-list"),
  credentialDeviceCount: document.querySelector("#credential-device-count"),
  credentialPlatform: document.querySelector("#credential-platform"),
  credentialProfile: document.querySelector("#credential-profile"),
  credentialProfileOptions: document.querySelector("#credential-profile-options"),
  credentialAuth: document.querySelector("#credential-fortios-auth"),
  credentialProphylaxisEnabled: document.querySelector("#credential-prophylaxis-enabled"),
  credentialProphylaxisSshFields: document.querySelector("#credential-prophylaxis-ssh-fields"),
  credentialProphylaxisApiFields: document.querySelector("#credential-prophylaxis-api-fields"),
  credentialProphylaxisUsername: document.querySelector("#credential-prophylaxis-username"),
  credentialProphylaxisPassword: document.querySelector("#credential-prophylaxis-password"),
  credentialProphylaxisEnable: document.querySelector("#credential-prophylaxis-enable"),
  credentialProphylaxisApiToken: document.querySelector("#credential-prophylaxis-api-token"),
  credentialFortiosApiPort: document.querySelector("#credential-fortios-api-port"),
  credentialOxidizedEnabled: document.querySelector("#credential-oxidized-enabled"),
  credentialOxidizedUsername: document.querySelector("#credential-oxidized-username"),
  credentialOxidizedPassword: document.querySelector("#credential-oxidized-password"),
  credentialMonitoringEnabled: document.querySelector("#credential-monitoring-enabled"),
  credentialSnmpCommunity: document.querySelector("#credential-snmp-community"),
  credentialReplace: document.querySelector("#credential-replace"),
  credentialClear: document.querySelector("#credential-clear"),
  credentialPlanButton: document.querySelector("#credential-plan"),
  credentialActivity: document.querySelector("#credential-activity"),
  credentialPlanResult: document.querySelector("#credential-plan-result"),
  credentialPlanStatus: document.querySelector("#credential-plan-status"),
  credentialPlanOperations: document.querySelector("#credential-plan-operations"),
  credentialApplyConfirm: document.querySelector("#credential-apply-confirm"),
  credentialApply: document.querySelector("#credential-apply"),
  vpnAssignmentOpen: document.querySelector("#vpn-assignment-open"),
  vpnAssignmentDialog: document.querySelector("#vpn-assignment-dialog"),
  vpnAssignmentClose: document.querySelector("#vpn-assignment-close"),
  vpnAssignmentTenant: document.querySelector("#vpn-assignment-tenant"),
  vpnAssignmentConnection: document.querySelector("#vpn-assignment-connection"),
  vpnAssignmentTargetType: document.querySelector("#vpn-assignment-target-type"),
  vpnAssignmentFilter: document.querySelector("#vpn-assignment-filter"),
  vpnAssignmentSelectAll: document.querySelector("#vpn-assignment-select-all"),
  vpnAssignmentTargets: document.querySelector("#vpn-assignment-targets"),
  vpnAssignmentCount: document.querySelector("#vpn-assignment-count"),
  vpnAssignmentPlanButton: document.querySelector("#vpn-assignment-plan"),
  vpnAssignmentActivity: document.querySelector("#vpn-assignment-activity"),
  vpnAssignmentPlanResult: document.querySelector("#vpn-assignment-plan-result"),
  vpnAssignmentPlanStatus: document.querySelector("#vpn-assignment-plan-status"),
  vpnAssignmentOperations: document.querySelector("#vpn-assignment-operations"),
  vpnAssignmentConfirm: document.querySelector("#vpn-assignment-confirm"),
  vpnAssignmentApply: document.querySelector("#vpn-assignment-apply"),
  adminSessionButton: document.querySelector("#admin-session-button"),
  adminSessionIdentity: document.querySelector("#admin-session-identity"),
  adminLoginDialog: document.querySelector("#admin-login-dialog"),
  adminLoginForm: document.querySelector("#admin-login-form"),
  adminUsername: document.querySelector("#admin-username"),
  adminPassword: document.querySelector("#admin-password"),
  adminLoginError: document.querySelector("#admin-login-error"),
  adminLoginSubmit: document.querySelector("#admin-login-submit"),
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
    const method = options.method || "GET";
    const csrf = document.cookie.split("; ").find((value) => value.startsWith("atlas_csrf="));
    response = await fetch(path, {
      method,
      headers: {
        Accept: "application/json",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(!["GET", "HEAD", "OPTIONS"].includes(method) && csrf
          ? { "X-CSRF-Token": decodeURIComponent(csrf.split("=").slice(1).join("=")) }
          : {}),
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
  if (state.authenticated) return {};
  return { Authorization: `Bearer ${state.monitoringToken}` };
}

function operatorHeaders() {
  if (state.authenticated) return {};
  return { Authorization: `Bearer ${state.operatorToken}` };
}

function requireOperatorToken() {
  if (state.authenticated) return true;
  const token = elements.operatorToken.value.trim();
  if (!token) {
    toast("Enter the operator token.", "warning");
    return false;
  }
  state.operatorToken = token;
  state.prophylaxisToken = token;
  state.monitoringToken = token;
  elements.prophylaxisToken.value = token;
  elements.monitoringToken.value = token;
  setPill(elements.operatorStatus, "healthy", "Ready");
  return true;
}

function renderAdminSession() {
  if (state.authenticated) {
    elements.adminSessionIdentity.textContent = state.principal.username;
    elements.adminSessionIdentity.hidden = false;
    elements.adminSessionButton.textContent = "Log out";
    setPill(elements.operatorStatus, "healthy", `Admin: ${state.principal.username}`);
  } else {
    elements.adminSessionIdentity.textContent = "";
    elements.adminSessionIdentity.hidden = true;
    elements.adminSessionButton.textContent = "Sign in";
    setPill(elements.operatorStatus, "neutral", "Login required");
  }
}

function scheduleAdminExpiry(expiresIn) {
  if (state.adminExpiryTimer) window.clearTimeout(state.adminExpiryTimer);
  state.adminExpiryTimer = null;
  if (!Number.isFinite(expiresIn) || expiresIn <= 0) return;
  state.adminExpiryTimer = window.setTimeout(() => {
    state.authenticated = false;
    state.principal = null;
    state.adminExpiryTimer = null;
    renderAdminSession();
    toast("Administrator session expired. Sign in again to continue.", "warning");
  }, expiresIn * 1000);
}

async function refreshAdminSession({ prompt = false } = {}) {
  try {
    state.principal = await apiRequest("/api/auth/me");
    state.authenticated = true;
    scheduleAdminExpiry(state.principal.expires_in);
  } catch (_error) {
    scheduleAdminExpiry(0);
    state.principal = null;
    state.authenticated = false;
  }
  renderAdminSession();
  if (prompt && !state.authenticated && !elements.adminLoginDialog.open) {
    elements.adminLoginDialog.showModal();
    elements.adminUsername.focus();
  }
  return state.authenticated;
}

async function submitAdminLogin(event) {
  event.preventDefault();
  elements.adminLoginSubmit.disabled = true;
  elements.adminLoginError.hidden = true;
  try {
    const principal = await apiRequest("/api/auth/login", {
      method: "POST",
      body: { username: elements.adminUsername.value, password: elements.adminPassword.value },
    });
    state.principal = principal;
    state.authenticated = true;
    scheduleAdminExpiry(principal.expires_in);
    elements.adminPassword.value = "";
    elements.adminLoginDialog.close();
    renderAdminSession();
    toast(`Signed in as ${principal.username}.`, "success");
  } catch (error) {
    elements.adminLoginError.textContent = error.safeMessage || "Sign in failed.";
    elements.adminLoginError.hidden = false;
  } finally {
    elements.adminLoginSubmit.disabled = false;
  }
}

async function toggleAdminSession() {
  if (!state.authenticated) {
    elements.adminLoginDialog.showModal();
    elements.adminUsername.focus();
    return;
  }
  try { await apiRequest("/api/auth/logout", { method: "POST" }); } catch (_error) { /* local state still clears */ }
  state.authenticated = false;
  state.principal = null;
  scheduleAdminExpiry(0);
  renderAdminSession();
  toast("Administrator session ended.", "info");
}

async function testCiscoIntegration() {
  if (!requireOperatorToken()) return;
  elements.ciscoTest.disabled = true;
  try {
    const result = await apiRequest("/api/integrations/cisco/test", {
      method: "POST",
      headers: operatorHeaders(),
    });
    elements.operatorResult.textContent = `Cisco APIs: ${titleCase(result.overall)} · OAuth ${titleCase(result.oauth)} · Software ${titleCase(result.software_suggestion)} · EoX ${titleCase(result.eox)}`;
    toast("Cisco integration test completed.", result.overall === "READY" ? "success" : "warning");
  } catch (error) {
    elements.operatorResult.textContent = error.safeMessage || "Cisco integration test failed.";
    toast(elements.operatorResult.textContent, "error");
  } finally {
    elements.ciscoTest.disabled = false;
  }
}

async function synchronizeZabbix() {
  if (!requireOperatorToken()) return;
  elements.zabbixSync.disabled = true;
  try {
    const result = await apiRequest("/api/zabbix/sync", {
      method: "POST",
      headers: operatorHeaders(),
    });
    const summary = result.summary || {};
    elements.operatorResult.textContent = `Zabbix sync: ${titleCase(result.status)} · created ${summary.created ?? 0}, updated ${summary.updated ?? 0}, disabled ${summary.disabled ?? 0}`;
    toast("Zabbix synchronization completed.", "success");
  } catch (error) {
    elements.operatorResult.textContent = error.safeMessage || "Zabbix synchronization failed.";
    toast(elements.operatorResult.textContent, "error");
  } finally {
    elements.zabbixSync.disabled = false;
  }
}

function inventoryPayload() {
  const tenant = elements.inventoryTenant.value.trim();
  const site = elements.inventorySite.value.trim();
  return {
    table: elements.inventoryTable.value,
    ...(tenant ? { tenant } : {}),
    ...(site ? { site } : {}),
  };
}

function inventoryOption(value, label) {
  const option = document.createElement("option");
  option.value = value;
  option.textContent = label;
  return option;
}

function resetInventorySiteOptions(label = "Select a tenant to override site") {
  elements.inventorySite.replaceChildren(inventoryOption("", label));
  elements.inventorySite.disabled = true;
}

async function loadInventoryImportSites(preferredSite = "") {
  const tenant = elements.inventoryTenant.value;
  if (!tenant) {
    resetInventorySiteOptions();
    return;
  }
  resetInventorySiteOptions("Loading sites…");
  const sites = await apiRequest(
    `/api/inventory/sites?tenant=${encodeURIComponent(tenant)}`,
    { headers: operatorHeaders() },
  );
  const options = [inventoryOption("", "Use table or configured default")];
  for (const site of sites.filter(
    (value) => value && typeof value.slug === "string" && typeof value.name === "string",
  ).sort((left, right) => (
    String(left.name || left.slug).localeCompare(String(right.name || right.slug))
  ))) {
    options.push(inventoryOption(site.slug, `${site.name} (${site.slug})`));
  }
  elements.inventorySite.replaceChildren(...options);
  elements.inventorySite.disabled = false;
  if ([...elements.inventorySite.options].some((option) => option.value === preferredSite)) {
    elements.inventorySite.value = preferredSite;
  } else if (preferredSite) {
    resetInventoryPlan();
  }
}

async function loadInventoryImportTenants() {
  const preferredTenant = elements.inventoryTenant.value;
  const preferredSite = elements.inventorySite.value;
  elements.inventoryTenant.replaceChildren(inventoryOption("", "Loading tenants…"));
  elements.inventoryTenant.disabled = true;
  resetInventorySiteOptions("Select a tenant to override site");
  const tenants = await apiRequest("/api/inventory/tenants", {
    headers: operatorHeaders(),
  });
  const options = [inventoryOption("", "Use table or configured default")];
  for (const tenant of tenants.filter(
    (value) => value && typeof value.slug === "string" && typeof value.name === "string",
  ).sort((left, right) => (
    String(left.name || left.slug).localeCompare(String(right.name || right.slug))
  ))) {
    options.push(inventoryOption(tenant.slug, `${tenant.name} (${tenant.slug})`));
  }
  elements.inventoryTenant.replaceChildren(...options);
  elements.inventoryTenant.disabled = false;
  if ([...elements.inventoryTenant.options].some((option) => option.value === preferredTenant)) {
    elements.inventoryTenant.value = preferredTenant;
  } else if (preferredTenant) {
    resetInventoryPlan();
  }
  await loadInventoryImportSites(preferredSite);
}

async function openInventoryImport() {
  if (!requireOperatorToken()) return;
  elements.inventoryImportDialog.showModal();
  elements.inventoryTable.focus();
  try {
    await loadInventoryImportTenants();
  } catch (error) {
    elements.inventoryTenant.replaceChildren(
      inventoryOption("", "Tenant list unavailable"),
    );
    elements.inventoryTenant.disabled = true;
    resetInventorySiteOptions("Site list unavailable");
    toast(error.safeMessage || "Tenant and site lists are unavailable.", "error");
  }
}

function clearCredentialSecrets() {
  for (const input of [
    elements.credentialProphylaxisPassword,
    elements.credentialProphylaxisEnable,
    elements.credentialProphylaxisApiToken,
    elements.credentialOxidizedPassword,
    elements.credentialSnmpCommunity,
  ]) input.value = "";
  resetCredentialPlan();
}

function resetCredentialPlan() {
  state.credentialPlan = null;
  elements.credentialPlanResult.hidden = true;
  elements.credentialApplyConfirm.checked = false;
  elements.credentialApplyConfirm.disabled = true;
  elements.credentialApply.disabled = true;
}

function selectedCredentialDevices() {
  return (state.credentialOptions?.devices || []).filter(
    (device) => state.credentialSelectedDeviceIds.has(device.id),
  );
}

function selectedCredentialPlatform() {
  return state.credentialOptions?.platforms?.find(
    (platform) => platform.slug === elements.credentialPlatform.value,
  ) || null;
}

function renderCredentialManufacturerOptions() {
  const tenantId = elements.credentialTenant.value;
  const manufacturers = [...new Set(
    (state.credentialOptions?.devices || [])
      .filter((device) => String(device.tenant?.id ?? "") === tenantId)
      .map((device) => device.manufacturer?.name)
      .filter(Boolean),
  )].sort((left, right) => left.localeCompare(right));
  elements.credentialManufacturer.replaceChildren(
    new Option(tenantId ? "Select a manufacturer" : "Select a tenant first", ""),
    ...manufacturers.map((manufacturer) => new Option(manufacturer, manufacturer)),
  );
  elements.credentialManufacturer.disabled = !tenantId;
}

function renderCredentialProfileOptions() {
  const tenantId = elements.credentialTenant.value;
  const profiles = [...new Set(
    (state.credentialOptions?.devices || [])
      .filter((device) => String(device.tenant?.id ?? "") === tenantId)
      .map((device) => device.credential_profile)
      .filter(Boolean),
  )].sort((left, right) => left.localeCompare(right));
  elements.credentialProfileOptions.replaceChildren(
    ...profiles.map((profile) => new Option(profile)),
  );
}

function renderCredentialPlatformOptions() {
  const manufacturer = elements.credentialManufacturer.value;
  const devices = selectedCredentialDevices();
  const previous = elements.credentialPlatform.value;
  const platforms = (state.credentialOptions?.platforms || []).filter(
    (platform) => platform.manufacturer === manufacturer,
  );
  elements.credentialPlatform.replaceChildren(
    new Option(manufacturer ? "Select platform" : "Select a manufacturer first", ""),
    ...platforms.map((platform) => new Option(platform.name, platform.slug)),
  );
  const selectedPlatforms = new Set(devices.map((device) => device.platform).filter(Boolean));
  const preferred = selectedPlatforms.size === 1 ? [...selectedPlatforms][0] : previous;
  if (platforms.some((item) => item.slug === preferred)) {
    elements.credentialPlatform.value = preferred;
  }
  const profiles = new Set(devices.map((device) => device.credential_profile).filter(Boolean));
  if (profiles.size === 1) elements.credentialProfile.value = [...profiles][0];
  elements.credentialProphylaxisEnabled.checked = devices.length > 0
    && devices.every((device) => device.prophylaxis_enabled === true);
  elements.credentialOxidizedEnabled.checked = devices.length > 0
    && devices.every((device) => device.oxidized_enabled === true);
  elements.credentialMonitoringEnabled.checked = devices.length > 0
    && devices.every((device) => device.monitoring_enabled === true);
  renderCredentialAuthOptions();
}

function renderCredentialDeviceOptions() {
  const tenantId = elements.credentialTenant.value;
  const manufacturer = elements.credentialManufacturer.value;
  const query = elements.credentialDeviceFilter.value.trim().toLocaleLowerCase();
  const selected = state.credentialSelectedDeviceIds;
  const devices = (state.credentialOptions?.devices || []).filter((device) => (
    String(device.tenant?.id ?? "") === tenantId
    && device.manufacturer?.name === manufacturer
    && (!query || device.name.toLocaleLowerCase().includes(query))
  ));
  if (!tenantId) {
    elements.credentialDeviceList.replaceChildren(
      Object.assign(document.createElement("p"), {
        className: "field-help", textContent: "Select a tenant first.",
      }),
    );
  } else if (!manufacturer) {
    elements.credentialDeviceList.replaceChildren(
      Object.assign(document.createElement("p"), {
        className: "field-help", textContent: "Select a manufacturer first.",
      }),
    );
  } else if (!devices.length) {
    elements.credentialDeviceList.replaceChildren(
      Object.assign(document.createElement("p"), {
        className: "field-help", textContent: "No matching active devices.",
      }),
    );
  } else {
    elements.credentialDeviceList.replaceChildren(...devices.map((device) => {
      const checkbox = Object.assign(document.createElement("input"), {
        type: "checkbox", value: String(device.id), checked: selected.has(device.id),
      });
      const label = document.createElement("label");
      label.className = "credential-device-option";
      const name = document.createElement("span");
      name.textContent = `${device.name}${device.platform ? ` — ${device.platform}` : ""}`;
      label.append(checkbox, name);
      return label;
    }));
  }
  elements.credentialDeviceCount.textContent = `${selected.size} selected`;
}

function credentialDeviceSelectionChanged() {
  const count = selectedCredentialDevices().length;
  elements.credentialDeviceCount.textContent = `${count} selected`;
  renderCredentialPlatformOptions();
  resetCredentialPlan();
}

function renderCredentialAuthOptions() {
  const platform = selectedCredentialPlatform();
  const choices = platform?.prophylaxis_auth || [];
  elements.credentialAuth.replaceChildren(
    ...choices.map((value) => new Option(value === "api_token" ? "HTTPS API token" : "SSH", value)),
  );
  if (!choices.length) elements.credentialAuth.append(new Option("Unsupported", ""));
  elements.credentialProphylaxisEnabled.disabled = !choices.length;
  elements.credentialOxidizedEnabled.disabled = !platform?.oxidized_supported;
  renderCredentialAuthFields();
}

function renderCredentialAuthFields() {
  const api = elements.credentialAuth.value === "api_token";
  elements.credentialProphylaxisSshFields.hidden = api;
  elements.credentialProphylaxisApiFields.hidden = !api;
}

async function openCredentialOnboarding() {
  if (!requireOperatorToken()) return;
  elements.credentialOpen.disabled = true;
  try {
    state.credentialOptions = await apiRequest("/api/credentials/options", {
      headers: operatorHeaders(),
    });
    elements.credentialTenant.replaceChildren(
      new Option("Select a tenant", ""),
      ...(state.credentialOptions.tenants || []).map(
        (tenant) => new Option(tenant.name, String(tenant.id)),
      ),
    );
    renderCredentialManufacturerOptions();
    renderCredentialProfileOptions();
    state.credentialSelectedDeviceIds.clear();
    elements.credentialDeviceFilter.value = "";
    renderCredentialDeviceOptions();
    renderCredentialPlatformOptions();
    elements.credentialDialog.showModal();
    elements.credentialTenant.focus();
  } catch (error) {
    toast(error.safeMessage || "Could not load credential onboarding options.", "error");
  } finally {
    elements.credentialOpen.disabled = false;
  }
}

function credentialPayload() {
  const prophylaxis = elements.credentialProphylaxisEnabled.checked;
  const oxidized = elements.credentialOxidizedEnabled.checked;
  const monitoring = elements.credentialMonitoringEnabled.checked;
  const auth = elements.credentialAuth.value;
  const enablePassword = elements.credentialProphylaxisEnable.value;
  const payload = {
    device_ids: selectedCredentialDevices().map((device) => device.id),
    platform: elements.credentialPlatform.value,
    credential_profile: elements.credentialProfile.value.trim(),
    prophylaxis_enabled: prophylaxis,
    oxidized_enabled: oxidized,
    monitoring_enabled: monitoring,
    replace_existing: elements.credentialReplace.checked,
  };
  if (prophylaxis) {
    payload.prophylaxis_auth = auth;
    if (auth === "api_token") {
      payload.prophylaxis_api = { api_token: elements.credentialProphylaxisApiToken.value };
      payload.fortios_api_port = Number(elements.credentialFortiosApiPort.value);
    } else {
      payload.prophylaxis_ssh = {
        username: elements.credentialProphylaxisUsername.value,
        password: elements.credentialProphylaxisPassword.value,
        ...(enablePassword ? { enable_password: enablePassword } : {}),
      };
    }
  }
  if (oxidized) payload.oxidized = {
    username: elements.credentialOxidizedUsername.value,
    password: elements.credentialOxidizedPassword.value,
  };
  if (monitoring) payload.snmp = { community: elements.credentialSnmpCommunity.value };
  return payload;
}

function renderCredentialPlan(result) {
  state.credentialPlan = result;
  elements.credentialPlanResult.hidden = false;
  setPill(
    elements.credentialPlanStatus,
    result.apply_allowed ? "healthy" : "warning",
    result.apply_allowed ? "Ready to apply" : "Action required",
  );
  elements.credentialPlanOperations.replaceChildren(...result.operations.map((operation) => {
    const row = document.createElement("tr");
    for (const value of [
      operation.device?.name || `${result.tenant?.name || "Selected tenant"} — shared profile`,
      operation.target,
      operation.purpose || operation.object || "—",
      operation.action,
      operation.reason ? titleCase(operation.reason) : "—",
    ]) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    return row;
  }));
  elements.credentialApplyConfirm.disabled = !result.apply_allowed;
  elements.credentialApplyConfirm.checked = false;
  elements.credentialApply.disabled = true;
}

function setCredentialBusy(busy) {
  state.credentialBusy = busy;
  elements.credentialActivity.hidden = !busy;
  elements.credentialPlanButton.disabled = busy;
  elements.credentialApply.disabled = busy || !elements.credentialApplyConfirm.checked;
  elements.credentialDialog.setAttribute("aria-busy", String(busy));
}

async function planCredentials() {
  if (!requireOperatorToken()) return;
  if (!elements.credentialTenant.value) {
    toast("Select a tenant for this credential batch.", "warning");
    return;
  }
  if (!selectedCredentialDevices().length) {
    toast("Select at least one device.", "warning");
    return;
  }
  if (!elements.credentialPlatform.value) {
    toast("Select the platform for this device batch.", "warning");
    return;
  }
  if (!elements.credentialProfile.value.trim()) {
    toast("Enter a shared credential profile.", "warning");
    return;
  }
  setCredentialBusy(true);
  try {
    const result = await apiRequest("/api/credentials/plan", {
      method: "POST", headers: operatorHeaders(), body: credentialPayload(),
    });
    renderCredentialPlan(result);
    toast("Credential onboarding plan is ready for review.", result.apply_allowed ? "success" : "warning");
  } catch (error) {
    resetCredentialPlan();
    toast(error.safeMessage || "Credential planning failed.", "error");
  } finally {
    setCredentialBusy(false);
  }
}

async function applyCredentials() {
  if (!state.credentialPlan?.apply_allowed || !elements.credentialApplyConfirm.checked) return;
  setCredentialBusy(true);
  try {
    const result = await apiRequest("/api/credentials/apply", {
      method: "POST",
      headers: operatorHeaders(),
      body: { ...credentialPayload(), plan_hash: state.credentialPlan.plan_hash },
    });
    renderCredentialPlan(result);
    clearCredentialSecrets();
    elements.credentialPlanResult.hidden = false;
    setPill(elements.credentialPlanStatus, "healthy", "Applied");
    toast("Credentials and NetBox selections were applied.", "success");
  } catch (error) {
    if (error.code === "credential_onboarding_plan_stale") resetCredentialPlan();
    toast(error.safeMessage || "Credential onboarding apply failed.", "error");
  } finally {
    setCredentialBusy(false);
  }
}

function resetVPNAssignmentPlan() {
  state.vpnAssignmentPlan = null;
  elements.vpnAssignmentPlanResult.hidden = true;
  elements.vpnAssignmentConfirm.checked = false;
  elements.vpnAssignmentConfirm.disabled = true;
  elements.vpnAssignmentApply.disabled = true;
}

function vpnAssignmentTargets() {
  if (!state.vpnAssignmentOptions) return [];
  return elements.vpnAssignmentTargetType.value === "device"
    ? state.vpnAssignmentOptions.devices || []
    : state.vpnAssignmentOptions.sites || [];
}

function filteredVPNAssignmentTargets() {
  const query = elements.vpnAssignmentFilter.value.trim().toLocaleLowerCase();
  return vpnAssignmentTargets().filter((target) => {
    const searchable = `${target.name || ""} ${target.site_name || ""}`.toLocaleLowerCase();
    return !query || searchable.includes(query);
  });
}

function updateVPNAssignmentControls() {
  const ready = Boolean(
    elements.vpnAssignmentTenant.value
    && elements.vpnAssignmentConnection.value
    && state.vpnAssignmentSelectedIds.size,
  );
  elements.vpnAssignmentPlanButton.disabled = state.vpnAssignmentBusy || !ready;
  elements.vpnAssignmentCount.textContent = `${state.vpnAssignmentSelectedIds.size} selected`;
}

function renderVPNAssignmentTargets() {
  const targets = filteredVPNAssignmentTargets();
  const selected = state.vpnAssignmentSelectedIds;
  const targetType = elements.vpnAssignmentTargetType.value;
  elements.vpnAssignmentFilter.placeholder = targetType === "device"
    ? "Filter Devices by name or Site" : "Filter Sites by name";
  elements.vpnAssignmentSelectAll.textContent = targetType === "device"
    ? "Select all Devices" : "Select all Sites";
  if (!elements.vpnAssignmentTenant.value) {
    elements.vpnAssignmentTargets.replaceChildren(
      Object.assign(document.createElement("p"), {
        className: "field-help", textContent: "Select a tenant first.",
      }),
    );
  } else if (!targets.length) {
    elements.vpnAssignmentTargets.replaceChildren(
      Object.assign(document.createElement("p"), {
        className: "field-help", textContent: "No matching targets.",
      }),
    );
  } else {
    elements.vpnAssignmentTargets.replaceChildren(...targets.map((target) => {
      const checkbox = Object.assign(document.createElement("input"), {
        type: "checkbox", value: String(target.id), checked: selected.has(target.id),
      });
      const label = document.createElement("label");
      label.className = "credential-device-option";
      const text = document.createElement("span");
      const site = targetType === "device" && target.site_name ? ` — ${target.site_name}` : "";
      const current = target.connection_id ? ` · ${target.connection_id}` : " · no assignment";
      text.textContent = `${target.name || target.id}${site}${current}`;
      label.append(checkbox, text);
      return label;
    }));
  }
  elements.vpnAssignmentSelectAll.disabled = !targets.length;
  updateVPNAssignmentControls();
}

function renderVPNAssignmentOptions() {
  const options = state.vpnAssignmentOptions;
  elements.vpnAssignmentConnection.replaceChildren(
    new Option("Select a VPN connection", ""),
    ...(options?.connections || []).map((connection) => new Option(
      `${connection.name} (${connection.connection_id})`, connection.connection_id,
    )),
    new Option("Remove assignment / use Site inheritance", "__remove__"),
  );
  const enabled = Boolean(options);
  elements.vpnAssignmentConnection.disabled = !enabled;
  elements.vpnAssignmentTargetType.disabled = !enabled;
  elements.vpnAssignmentFilter.disabled = !enabled;
  renderVPNAssignmentTargets();
}

async function openVPNAssignment() {
  if (!requireOperatorToken()) return;
  elements.vpnAssignmentDialog.showModal();
  elements.vpnAssignmentOpen.disabled = true;
  resetVPNAssignmentPlan();
  state.vpnAssignmentOptions = null;
  state.vpnAssignmentSelectedIds.clear();
  try {
    const tenants = await apiRequest("/api/inventory/tenants", {
      headers: operatorHeaders(),
    });
    elements.vpnAssignmentTenant.replaceChildren(
      new Option("Select a tenant", ""),
      ...tenants.map((tenant) => new Option(
        `${tenant.name} (${tenant.slug})`, String(tenant.id),
      )),
    );
    renderVPNAssignmentOptions();
    elements.vpnAssignmentTenant.focus();
  } catch (error) {
    toast(error.safeMessage || "Could not load VPN assignment tenants.", "error");
  } finally {
    elements.vpnAssignmentOpen.disabled = false;
  }
}

async function loadVPNAssignmentOptions() {
  resetVPNAssignmentPlan();
  state.vpnAssignmentOptions = null;
  state.vpnAssignmentSelectedIds.clear();
  elements.vpnAssignmentFilter.value = "";
  const tenantId = elements.vpnAssignmentTenant.value;
  if (!tenantId) {
    renderVPNAssignmentOptions();
    return;
  }
  setVPNAssignmentBusy(true);
  try {
    state.vpnAssignmentOptions = await apiRequest(
      `/api/v1/vpn/assignments/options?tenant_id=${encodeURIComponent(tenantId)}`,
      { headers: operatorHeaders() },
    );
    renderVPNAssignmentOptions();
  } catch (error) {
    renderVPNAssignmentOptions();
    toast(error.safeMessage || "Could not load tenant VPN assignments.", "error");
  } finally {
    setVPNAssignmentBusy(false);
  }
}

function vpnAssignmentPayload() {
  const selectedConnection = elements.vpnAssignmentConnection.value;
  return {
    tenant_id: Number(elements.vpnAssignmentTenant.value),
    connection_id: selectedConnection === "__remove__" ? null : selectedConnection,
    target_type: elements.vpnAssignmentTargetType.value,
    target_ids: [...state.vpnAssignmentSelectedIds].sort((left, right) => left - right),
  };
}

function renderVPNAssignmentPlan(result) {
  state.vpnAssignmentPlan = result;
  elements.vpnAssignmentPlanResult.hidden = false;
  setPill(elements.vpnAssignmentPlanStatus, "healthy", "Ready to apply");
  elements.vpnAssignmentOperations.replaceChildren(...result.operations.map((operation) => {
    const row = document.createElement("tr");
    for (const value of [
      operation.target_name,
      operation.current_connection_id || "—",
      operation.desired_connection_id || "Site inheritance / none",
      operation.action,
    ]) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    return row;
  }));
  elements.vpnAssignmentConfirm.disabled = false;
  elements.vpnAssignmentConfirm.checked = false;
  elements.vpnAssignmentApply.disabled = true;
}

function setVPNAssignmentBusy(busy) {
  state.vpnAssignmentBusy = busy;
  elements.vpnAssignmentActivity.hidden = !busy;
  elements.vpnAssignmentDialog.setAttribute("aria-busy", String(busy));
  updateVPNAssignmentControls();
  elements.vpnAssignmentApply.disabled = busy
    || !state.vpnAssignmentPlan
    || !elements.vpnAssignmentConfirm.checked;
}

async function planVPNAssignment() {
  if (!requireOperatorToken()) return;
  setVPNAssignmentBusy(true);
  try {
    const result = await apiRequest("/api/v1/vpn/assignments/plan", {
      method: "POST", headers: operatorHeaders(), body: vpnAssignmentPayload(),
    });
    renderVPNAssignmentPlan(result);
    toast("VPN assignment plan is ready for review.", "success");
  } catch (error) {
    resetVPNAssignmentPlan();
    toast(error.safeMessage || "VPN assignment planning failed.", "error");
  } finally {
    setVPNAssignmentBusy(false);
  }
}

async function applyVPNAssignment() {
  if (!state.vpnAssignmentPlan || !elements.vpnAssignmentConfirm.checked) return;
  setVPNAssignmentBusy(true);
  try {
    const result = await apiRequest("/api/v1/vpn/assignments/apply", {
      method: "POST",
      headers: operatorHeaders(),
      body: { ...vpnAssignmentPayload(), plan_hash: state.vpnAssignmentPlan.plan_hash },
    });
    renderVPNAssignmentPlan(result);
    setPill(elements.vpnAssignmentPlanStatus, "healthy", "Applied");
    for (const operation of result.operations) {
      const target = vpnAssignmentTargets().find(
        (item) => item.id === operation.target_id,
      );
      if (target) target.connection_id = operation.desired_connection_id;
    }
    renderVPNAssignmentTargets();
    state.vpnAssignmentPlan = null;
    elements.vpnAssignmentConfirm.disabled = true;
    elements.vpnAssignmentApply.disabled = true;
    toast("VPN assignments were applied to NetBox.", "success");
  } catch (error) {
    if (error.code === "vpn_assignment_plan_stale") resetVPNAssignmentPlan();
    toast(error.safeMessage || "VPN assignment apply failed.", "error");
  } finally {
    setVPNAssignmentBusy(false);
  }
}

function resetInventoryPlan() {
  state.inventoryPlan = null;
  elements.inventoryPlanResult.hidden = true;
  elements.inventoryApplyConfirm.checked = false;
  elements.inventoryApplyConfirm.disabled = true;
  elements.inventoryApply.disabled = true;
}

function resetInventoryStructurePlan() {
  state.inventoryStructurePlan = null;
  elements.inventoryStructureResult.textContent = "No plan created.";
  elements.inventoryStructureConfirm.checked = false;
  elements.inventoryStructureConfirm.disabled = true;
  elements.inventoryStructureApply.disabled = true;
}

function inventoryStructurePayload() {
  const kind = elements.inventoryStructureKind.value;
  const payload = { name: elements.inventoryStructureName.value.trim() };
  if (kind === "tenant") {
    const slug = elements.inventoryStructureSlug.value.trim();
    if (slug) payload.slug = slug;
    const group = elements.inventoryStructureGroup.value.trim();
    if (group) payload.group = group;
  } else {
    payload.tenant = elements.inventoryStructureTenant.value;
  }
  return payload;
}

function renderInventoryStructureFields() {
  const site = elements.inventoryStructureKind.value === "site";
  elements.inventoryStructureTenantField.hidden = !site;
  elements.inventoryStructureSlugField.hidden = site;
  elements.inventoryStructureGroupField.hidden = site;
  resetInventoryStructurePlan();
}

async function loadInventoryTenants() {
  const tenants = await apiRequest("/api/inventory/tenants", {
    headers: operatorHeaders(),
  });
  elements.inventoryStructureTenant.replaceChildren(...tenants.map((tenant) => {
    const option = document.createElement("option");
    option.value = tenant.slug;
    option.textContent = `${tenant.name} (${tenant.slug})`;
    return option;
  }));
}

async function openInventoryStructure() {
  if (!requireOperatorToken()) return;
  try {
    await loadInventoryTenants();
    elements.inventoryStructureDialog.showModal();
    elements.inventoryStructureName.focus();
  } catch (error) {
    toast(error.safeMessage || "Tenant list is unavailable.", "error");
  }
}

async function runInventoryStructure(apply) {
  if (!requireOperatorToken()) return;
  const kind = elements.inventoryStructureKind.value;
  const payload = inventoryStructurePayload();
  if (!payload.name || (kind === "site" && !payload.tenant)) {
    toast("Enter the required name and tenant.", "warning");
    return;
  }
  elements.inventoryStructurePlan.disabled = true;
  elements.inventoryStructureApply.disabled = true;
  try {
    if (apply) payload.plan_hash = state.inventoryStructurePlan?.plan_hash;
    const result = await apiRequest(`/api/inventory/${kind}s/${apply ? "apply" : "plan"}`, {
      method: "POST", headers: operatorHeaders(), body: payload,
    });
    state.inventoryStructurePlan = result;
    const target = result.object?.slug || result.object?.name || payload.name;
    const managementVrf = kind === "tenant" ? result.management_vrf?.name : null;
    elements.inventoryStructureResult.textContent = managementVrf
      ? `${titleCase(result.operation)} ${kind}: ${target}; management VRF: ${managementVrf}`
      : `${titleCase(result.operation)} ${kind}: ${target}`;
    elements.inventoryStructureConfirm.checked = false;
    elements.inventoryStructureConfirm.disabled = apply;
    if (apply) {
      toast(`${titleCase(kind)} operation applied.`, "success");
      await loadInventoryTenants();
    }
  } catch (error) {
    resetInventoryStructurePlan();
    toast(error.safeMessage || `${titleCase(kind)} operation failed.`, "error");
  } finally {
    elements.inventoryStructurePlan.disabled = false;
  }
}

function inventoryFilename(model) {
  const slug = String(model || "device-type")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 100);
  return `${slug || "device-type"}.yaml`;
}

function updateInventoryDeviceTypeFilename() {
  elements.inventoryDeviceTypeFilename.value = inventoryFilename(
    elements.inventoryDeviceTypeModel.value,
  );
}

function setInventoryDeviceTypeError(message = "") {
  elements.inventoryDeviceTypeError.textContent = message;
  elements.inventoryDeviceTypeError.hidden = !message;
  elements.inventoryDeviceTypeYaml.setAttribute("aria-invalid", message ? "true" : "false");
}

function inventoryStatusTone(status) {
  if (["CREATE", "UNCHANGED"].includes(status)) return "healthy";
  if (status === "INVALID") return "degraded";
  return "unhealthy";
}

function renderInventoryPlan(result) {
  state.inventoryPlan = result;
  const report = result.report || {};
  const summary = report.summary || {};
  const issues = result.device_type_issues || [];
  const uploadable = issues.filter((issue) => issue.yaml_upload_allowed);
  const blocked = (summary.INVALID || 0) + (summary.CONFLICT || 0) + (summary.ERROR || 0);

  elements.inventoryPlanResult.hidden = false;
  elements.inventoryCreateCount.textContent = String(summary.CREATE || 0);
  elements.inventoryUnchangedCount.textContent = String(summary.UNCHANGED || 0);
  elements.inventoryInvalidCount.textContent = String(summary.INVALID || 0);
  elements.inventoryConflictCount.textContent = String(summary.CONFLICT || 0);
  elements.inventoryErrorCount.textContent = String(summary.ERROR || 0);
  setPill(
    elements.inventoryPlanStatus,
    result.apply_allowed ? "healthy" : "degraded",
    result.apply_allowed ? "Ready to apply" : "Action required",
  );
  elements.inventoryPlanMessage.textContent = result.apply_allowed
    ? `${summary.total_rows || 0} rows were checked. The exact plan can now be approved.`
    : `${blocked} rows require attention. Apply remains locked until the plan has no invalid, conflicting, or failed rows.`;

  elements.inventoryPlanRows.replaceChildren(...(report.rows || []).map((entry) => {
    const row = document.createElement("tr");
    for (const value of [
      entry.row,
      entry.hostname || "—",
      entry.model || "—",
      entry.management_ip || "—",
      titleCase(entry.operation || "none"),
    ]) {
      const cell = document.createElement("td");
      cell.textContent = String(value);
      row.append(cell);
    }
    const status = document.createElement("td");
    const pill = document.createElement("span");
    pill.className = `status-pill status-pill-${inventoryStatusTone(entry.status)}`;
    pill.textContent = titleCase(entry.status);
    status.append(pill);
    row.append(status);
    const reason = document.createElement("td");
    reason.textContent = entry.reason ? titleCase(entry.reason) : "—";
    row.append(reason);
    return row;
  }));

  elements.inventoryDeviceTypeResolution.hidden = issues.length === 0;
  elements.inventoryDeviceTypeUpload.hidden = uploadable.length === 0;
  elements.inventoryDeviceTypeModel.replaceChildren(...issues.map((issue) => {
    const option = document.createElement("option");
    option.value = issue.model;
    option.textContent = `${issue.model} — ${titleCase(issue.reason)}`;
    option.disabled = !issue.yaml_upload_allowed;
    return option;
  }));
  if (uploadable.length) {
    elements.inventoryDeviceTypeModel.value = uploadable[0].model;
    updateInventoryDeviceTypeFilename();
    elements.inventoryDeviceTypeMessage.textContent = `${uploadable.length} Device Type definition${uploadable.length === 1 ? " is" : "s are"} missing. Upload one YAML at a time; Atlas validates it and automatically retries the plan.`;
  } else if (issues.length) {
    elements.inventoryDeviceTypeMessage.textContent = "The Device Type issue cannot be fixed by upload. Review the reported catalog or ambiguity error, then retry the plan.";
  }

  elements.inventoryApplyConfirm.checked = false;
  elements.inventoryApplyConfirm.disabled = !result.apply_allowed;
  elements.inventoryApply.disabled = true;
}

function setInventoryBusy(busy) {
  state.inventoryBusy = busy;
  for (const button of [
    elements.inventoryPlanButton,
    elements.inventoryRetry,
    elements.inventoryDeviceTypeUpload,
    elements.inventoryApply,
  ]) button.disabled = busy || (button === elements.inventoryApply && !elements.inventoryApplyConfirm.checked);
}

function inventoryElapsed(seconds) {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

function renderInventoryActivity() {
  if (!state.inventoryActivityKind) {
    elements.inventoryActivity.hidden = true;
    return;
  }
  const seconds = Math.max(0, Math.floor((Date.now() - state.inventoryActivityStarted) / 1000));
  let title = "Working with NetBox";
  let detail = "Atlas is processing the inventory operation.";
  if (state.inventoryActivityKind === "plan") {
    title = seconds < 8 ? "Validating device table" : seconds < 25 ? "Resolving Device Types" : "Building dry-run plan";
    detail = seconds < 8
      ? "Checking rows, tenant, site and management addresses."
      : seconds < 25
        ? "Comparing hardware models with NetBox, the Atlas catalog and the pinned upstream library."
        : "Reading the current NetBox inventory and calculating changes. Large Device Types can take a few minutes.";
  } else if (state.inventoryActivityKind === "upload") {
    title = "Validating Device Type YAML";
    detail = "Atlas is checking and storing the definition before retrying the plan.";
  } else if (state.inventoryActivityKind === "apply") {
    title = "Applying approved inventory plan";
    detail = "Atlas is validating the plan again and writing the approved changes to NetBox.";
  }
  elements.inventoryActivityTitle.textContent = title;
  elements.inventoryActivityDetail.textContent = detail;
  elements.inventoryActivityTime.textContent = inventoryElapsed(seconds);
  elements.inventoryActivity.hidden = false;
}

function startInventoryActivity(kind) {
  if (state.inventoryActivityTimer) window.clearInterval(state.inventoryActivityTimer);
  state.inventoryActivityKind = kind;
  state.inventoryActivityStarted = Date.now();
  elements.inventoryPlanButton.classList.toggle("is-busy", kind === "plan");
  elements.inventoryPlanLabel.textContent = kind === "plan" ? "Creating plan…" : "Create dry-run plan";
  elements.inventoryImportDialog.setAttribute("aria-busy", "true");
  renderInventoryActivity();
  state.inventoryActivityTimer = window.setInterval(renderInventoryActivity, 1000);
}

function stopInventoryActivity() {
  if (state.inventoryActivityTimer) window.clearInterval(state.inventoryActivityTimer);
  state.inventoryActivityTimer = null;
  state.inventoryActivityKind = null;
  elements.inventoryPlanButton.classList.remove("is-busy");
  elements.inventoryPlanLabel.textContent = "Create dry-run plan";
  elements.inventoryImportDialog.removeAttribute("aria-busy");
  renderInventoryActivity();
}

async function planInventory() {
  if (!requireOperatorToken()) return;
  if (!elements.inventoryTable.value.trim()) {
    toast("Paste the device table first.", "warning");
    return;
  }
  setInventoryBusy(true);
  startInventoryActivity("plan");
  try {
    const result = await apiRequest("/api/inventory/import/plan", {
      method: "POST",
      headers: operatorHeaders(),
      body: inventoryPayload(),
    });
    renderInventoryPlan(result);
    toast(
      result.apply_allowed ? "Inventory dry-run is ready for review." : "Inventory plan contains issues that must be resolved.",
      result.apply_allowed ? "success" : "warning",
    );
  } catch (error) {
    resetInventoryPlan();
    toast(error.safeMessage || "Inventory planning failed.", "error");
  } finally {
    stopInventoryActivity();
    setInventoryBusy(false);
  }
}

async function uploadInventoryDeviceType() {
  if (!requireOperatorToken()) return;
  const filename = elements.inventoryDeviceTypeFilename.value.trim();
  const yamlContent = elements.inventoryDeviceTypeYaml.value;
  if (!filename || !yamlContent.trim()) {
    setInventoryDeviceTypeError("Enter the YAML filename and content.");
    toast("Enter the YAML filename and content.", "warning");
    return;
  }
  setInventoryDeviceTypeError();
  setInventoryBusy(true);
  startInventoryActivity("upload");
  try {
    const result = await apiRequest("/api/inventory/device-types/upload", {
      method: "POST",
      headers: operatorHeaders(),
      body: {
        filename,
        yaml_content: yamlContent,
        replace: elements.inventoryDeviceTypeReplace.checked,
      },
    });
    toast(`${result.model} YAML was validated and stored. Retrying the plan.`, "success");
    elements.inventoryDeviceTypeYaml.value = "";
    elements.inventoryDeviceTypeReplace.checked = false;
  } catch (error) {
    const message = error.safeMessage || "Device Type YAML upload failed.";
    setInventoryDeviceTypeError(message);
    elements.inventoryDeviceTypeYaml.focus();
    toast("Device Type YAML is invalid. Review the message below the editor.", "error");
    stopInventoryActivity();
    setInventoryBusy(false);
    return;
  }
  stopInventoryActivity();
  setInventoryBusy(false);
  await planInventory();
}

async function applyInventory() {
  if (!requireOperatorToken()) return;
  if (!state.inventoryPlan?.apply_allowed || !elements.inventoryApplyConfirm.checked) {
    toast("Review and confirm a valid dry-run plan first.", "warning");
    return;
  }
  setInventoryBusy(true);
  startInventoryActivity("apply");
  try {
    const result = await apiRequest("/api/inventory/import/apply", {
      method: "POST",
      headers: operatorHeaders(),
      body: { ...inventoryPayload(), plan_hash: state.inventoryPlan.plan_hash },
    });
    renderInventoryPlan(result);
    setPill(elements.inventoryPlanStatus, "healthy", "Applied");
    elements.inventoryPlanMessage.textContent = `${result.report?.summary?.total_rows || 0} rows were processed. The result below reflects the completed NetBox operation.`;
    state.inventoryPlan = null;
    elements.inventoryApplyConfirm.checked = false;
    elements.inventoryApplyConfirm.disabled = true;
    elements.inventoryApply.disabled = true;
    toast("Approved inventory plan was applied to NetBox.", "success");
    await loadDashboard();
  } catch (error) {
    if (error.code === "inventory_plan_stale" || error.code === "inventory_plan_blocked") {
      resetInventoryPlan();
    }
    toast(error.safeMessage || "Inventory apply failed.", "error");
  } finally {
    stopInventoryActivity();
    setInventoryBusy(false);
  }
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
  elements.monitoringBulk.disabled = state.monitoringBulkActive
    || !state.monitoringDevices.some((device) => !device.error_code);
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

function clearMonitoringBulkPreview() {
  state.monitoringBulkPlans = [];
  elements.monitoringBulkPreview.hidden = true;
  elements.monitoringBulkPreviewBody.replaceChildren();
  elements.monitoringBulkApply.disabled = true;
}

function appendMonitoringPlanDetail(container, label, value) {
  if (!value) return;
  const row = document.createElement("p");
  const term = document.createElement("strong");
  term.textContent = `${label}: `;
  row.append(term, document.createTextNode(String(value)));
  container.append(row);
}

function renderMonitoringBulkPreview(entries, mode) {
  state.monitoringBulkPlans = entries;
  const ready = entries.filter((entry) => entry.plan);
  const blocked = entries.filter((entry) => entry.error);
  const changes = ready.reduce(
    (count, entry) => count + (entry.plan.planned_changes?.length || 0),
    0,
  );
  elements.monitoringBulkPreviewMode.textContent = titleCase(mode);
  elements.monitoringBulkPreviewSummary.textContent = blocked.length
    ? `${ready.length} of ${entries.length} plans are ready. Resolve ${blocked.length} planning ${blocked.length === 1 ? "error" : "errors"} before applying the batch.`
    : `${ready.length} plans are ready with ${changes} proposed ${changes === 1 ? "change" : "changes"}. No writes have been made.`;

  const rows = entries.map((entry) => {
    const row = document.createElement("tr");
    const deviceCell = document.createElement("td");
    const deviceName = document.createElement("strong");
    deviceName.textContent = entry.device.name || `Device ${entry.device.id}`;
    const deviceMeta = document.createElement("p");
    deviceMeta.className = "monitoring-plan-detail";
    deviceMeta.textContent = `${entry.device.address || "No address"} · ${entry.device.platform || "Unknown platform"}`;
    deviceCell.append(deviceName, deviceMeta);

    const targetCell = document.createElement("td");
    targetCell.className = "monitoring-plan-target";
    const resolved = entry.plan?.resolved || {};
    appendMonitoringPlanDetail(
      targetCell,
      "Endpoint",
      resolved.api_endpoint || resolved.ssh_endpoint || entry.device.address,
    );
    appendMonitoringPlanDetail(targetCell, "SNMP source", resolved.monitoring_source_ip);
    appendMonitoringPlanDetail(targetCell, "Zabbix template", resolved.zabbix_template);
    appendMonitoringPlanDetail(targetCell, "Credentials", resolved.credential_profile);
    appendMonitoringPlanDetail(targetCell, "Proxy", resolved.proxy_name);
    if (entry.error) {
      appendMonitoringPlanDetail(targetCell, "Error", entry.error.safeMessage);
    }

    const changesCell = document.createElement("td");
    const plannedChanges = entry.plan?.planned_changes || [];
    if (plannedChanges.length) {
      const list = document.createElement("ul");
      list.className = "monitoring-plan-changes";
      for (const change of plannedChanges) {
        const item = document.createElement("li");
        item.textContent = change;
        list.append(item);
      }
      changesCell.append(list);
    } else {
      const empty = document.createElement("span");
      empty.className = "monitoring-plan-no-change";
      empty.textContent = entry.error ? "Plan unavailable" : "No changes required";
      changesCell.append(empty);
    }

    const statusCell = document.createElement("td");
    const status = document.createElement("span");
    status.className = `status-pill status-pill-${entry.error ? "unhealthy" : "healthy"}`;
    status.textContent = entry.error ? titleCase(entry.error.code) : "Ready";
    statusCell.append(status);
    row.append(deviceCell, targetCell, changesCell, statusCell);
    return row;
  });

  elements.monitoringBulkPreviewBody.replaceChildren(...rows);
  elements.monitoringBulkApply.textContent = `Apply all ${ready.length} ${ready.length === 1 ? "plan" : "plans"}`;
  elements.monitoringBulkApply.disabled = !ready.length || Boolean(blocked.length);
  elements.monitoringBulkPreview.hidden = false;
  elements.monitoringBulkPreview.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function loadMonitoringDevices() {
  state.monitoringToken = state.authenticated ? "" : elements.monitoringToken.value.trim();
  if (!state.authenticated && !state.monitoringToken) {
    toast("Enter the monitoring operator token.", "warning");
    return;
  }
  elements.monitoringLoad.disabled = true;
  clearMonitoringBulkPreview();
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
  clearMonitoringBulkPreview();
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
  clearMonitoringBulkPreview();
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
  state.monitoringBulkActive = true;
  renderMonitoringDevices();
  clearMonitoringBulkPreview();
  elements.monitoringPlan.hidden = true;
  toast(`Preparing monitoring plans for ${devices.length} devices.`, "info");
  try {
    const mode = elements.monitoringMode.value;
    const entries = [];
    for (const device of devices) {
      setMonitoringBusy(device.id, "PLANNING");
      try {
        const plan = await monitoringRequest(`/api/monitoring/devices/${device.id}/plan`, {
          method: "POST",
          body: { mode },
        });
        entries.push({ device, plan, mode });
      } catch (error) {
        entries.push({ device, error, mode });
      } finally {
        setMonitoringBusy(device.id);
      }
    }
    renderMonitoringBulkPreview(entries, mode);
    const blocked = entries.filter((entry) => entry.error).length;
    toast(
      blocked
        ? `Bulk preview is ready with ${blocked} blocked ${blocked === 1 ? "device" : "devices"}.`
        : `Review ${entries.length} monitoring plans before applying them.`,
      blocked ? "warning" : "success",
    );
  } catch (error) {
    toast(error.safeMessage || "Bulk monitoring plan failed.", "error");
  } finally {
    state.monitoringBulkActive = false;
    renderMonitoringDevices();
  }
}

async function applyAllMonitoringPlans() {
  const entries = state.monitoringBulkPlans;
  const ready = entries.filter((entry) => entry.plan);
  if (!ready.length || entries.some((entry) => entry.error)) return;
  state.monitoringBulkActive = true;
  renderMonitoringDevices();
  elements.monitoringBulkApply.disabled = true;
  try {
    const result = await monitoringRequest("/api/monitoring/provision/bulk", {
      method: "POST",
      body: {
        plans: ready.map(({ mode, plan }) => ({
          mode,
          plan_id: plan.plan_id,
          confirmed: true,
        })),
      },
    });
    for (const item of result.jobs) {
      const device = state.monitoringDevices.find(
        (candidate) => candidate.id === item.device_id,
      );
      state.monitoringJobs.set(item.device_id, item);
      state.monitoringPolling.add(item.device_id);
      pollMonitoringJob(
        device,
        item.job_id,
        Date.now() + MONITORING_VERIFICATION_TIMEOUT_MS,
      );
    }
    clearMonitoringBulkPreview();
    renderMonitoringDevices();
    renderMonitoringActivity();
    toast(`${result.jobs.length} monitoring jobs started.`, "info");
  } catch (error) {
    elements.monitoringBulkApply.disabled = false;
    toast(error.safeMessage || "Bulk monitoring apply failed.", "error");
  } finally {
    state.monitoringBulkActive = false;
    renderMonitoringDevices();
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
    const disabled = payload.components?.[name]?.status === "disabled";
    card.hidden = disabled;
    document.querySelectorAll(`[data-requires-component="${name}"]`).forEach(
      (section) => { section.hidden = disabled; },
    );
    if (disabled) continue;
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

function formatSlovakUnit(value, one, few, many) {
  const label = value === 1 ? one : value >= 2 && value <= 4 ? few : many;
  return `${value} ${label}`;
}

function formatUptimeSeconds(totalSeconds) {
  if (!Number.isInteger(totalSeconds) || totalSeconds < 0) {
    return String(totalSeconds);
  }
  let remaining = totalSeconds;
  const years = Math.floor(remaining / 31536000);
  remaining %= 31536000;
  const days = Math.floor(remaining / 86400);
  remaining %= 86400;
  const hours = Math.floor(remaining / 3600);
  remaining %= 3600;
  const minutes = Math.floor(remaining / 60);
  const seconds = remaining % 60;
  const parts = [
    [years, "rok", "roky", "rokov"],
    [days, "deň", "dni", "dní"],
    [hours, "hodina", "hodiny", "hodín"],
    [minutes, "minúta", "minúty", "minút"],
    [seconds, "sekunda", "sekundy", "sekúnd"],
  ].filter(([value]) => value > 0)
    .map(([value, one, few, many]) => formatSlovakUnit(value, one, few, many));
  return parts.join(" ") || "0 sekúnd";
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
  elements.prophylaxisWarning.textContent = String(run?.summary?.warning ?? 0);
  elements.prophylaxisUnknown.textContent = String(run?.summary?.unknown ?? 0);
  elements.prophylaxisFailure.textContent = String(run?.summary?.failure ?? 0);
  elements.prophylaxisSkipped.textContent = String(run?.summary?.skipped ?? 0);
  elements.prophylaxisReport.textContent = run
    ? `Run ${run.run_id} · Report: ${run.report_path}`
    : "No run has been launched.";
  elements.prophylaxisRun.disabled = ["PENDING", "RUNNING"].includes(status);
  renderProphylaxisCharts(run);
  const results = run?.results || [];
  if (!results.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 4;
    cell.className = "table-message";
    cell.textContent = ["PENDING", "RUNNING"].includes(status)
      ? "Checks are running…"
      : "No results yet.";
    row.append(cell);
    elements.prophylaxisResults.replaceChildren(row);
    return;
  }
  const rows = results.map((result) => {
    const row = document.createElement("tr");
    const payload = result.values || result.facts || {};
    const details = [];
    for (const [key, value] of Object.entries(payload)) {
      if (value === null || value === undefined || key === "outcome" || key === "sensors") continue;
      if (key === "uptime_seconds") {
        details.push(`Uptime: ${formatUptimeSeconds(value)}`);
        continue;
      }
      if (key === "records" && Array.isArray(value)) {
        for (const record of value) {
          if (!record || typeof record !== "object") continue;
          const identity = record.hostname || record.name
            || record.serial_number || record.record_type || "Record";
          const fields = Object.entries(record)
            .filter(([field, item]) => field !== "record_type"
              && item !== null && item !== undefined && item !== identity)
            .map(([field, item]) => `${titleCase(field)}=${item}`);
          details.push(`${identity}: ${fields.join(", ")}`);
        }
        continue;
      }
      if (Array.isArray(value)) {
        if (value.length) details.push(`${titleCase(key)}: ${value.join(", ")}`);
        continue;
      }
      if (typeof value === "object") {
        const fields = Object.entries(value)
          .filter(([, item]) => item !== null && item !== undefined)
          .map(([field, item]) => `${titleCase(field)}=${item}`);
        if (fields.length) details.push(`${titleCase(key)}: ${fields.join(", ")}`);
        continue;
      }
      details.push(`${titleCase(key)}: ${value}`);
    }
    if (Array.isArray(payload.sensors)) {
      details.push(payload.sensors.map((sensor) =>
        `Fan ${sensor.index}: ${sensor.status_text} (${sensor.outcome})`).join(", "));
    }
    for (const value of [
      result.device_name,
      titleCase(result.check),
      result.evaluation,
      result.error ? titleCase(result.error) : details.join(" · ") || "Collected",
    ]) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    return row;
  });
  elements.prophylaxisResults.replaceChildren(...rows);
}

async function renderProphylaxisCharts(run) {
  elements.prophylaxisCharts.replaceChildren();
  const charts = (run?.charts || []).filter((chart) =>
    chart.status === "generated" && chart.artifact);
  for (const chart of charts) {
    const figure = document.createElement("figure");
    figure.className = "prophylaxis-chart";
    const caption = document.createElement("figcaption");
    caption.textContent = [
      chart.device_name,
      titleCase(chart.check),
      chart.window,
    ].join(" · ");
    try {
      const response = await fetch(
        `/api/prophylaxis/runs/${run.run_id}/charts/${encodeURIComponent(chart.artifact)}`,
        { headers: prophylaxisHeaders() },
      );
      if (!response.ok) throw new Error("chart_unavailable");
      const image = document.createElement("img");
      image.alt = caption.textContent;
      image.src = URL.createObjectURL(await response.blob());
      image.addEventListener(
        "load",
        () => URL.revokeObjectURL(image.src),
        { once: true },
      );
      figure.append(image, caption);
    } catch (_) {
      caption.textContent += " · unavailable";
      figure.append(caption);
    }
    elements.prophylaxisCharts.append(figure);
  }
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
  const openDialogs = [...document.querySelectorAll("dialog[open]")];
  const activeDialog = openDialogs.at(-1);
  let region = elements.toastRegion;
  if (activeDialog) {
    region = activeDialog.querySelector("[data-dialog-toast-region]");
    if (!region) {
      region = document.createElement("div");
      region.className = "toast-region";
      region.dataset.dialogToastRegion = "true";
      region.setAttribute("aria-live", "polite");
      region.setAttribute("aria-atomic", "true");
      activeDialog.append(region);
    }
  }
  region.append(item);
  window.setTimeout(() => {
    item.remove();
    if (region !== elements.toastRegion && !region.childElementCount) region.remove();
  }, 5000);
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
  const platform = await Promise.resolve(apiRequest("/api/status"))
    .then((value) => ({ status: "fulfilled", value }))
    .catch((reason) => ({ status: "rejected", reason }));

  if (platform.status === "fulfilled") {
    renderPlatformStatus(platform.value);
  } else {
    renderPlatformUnavailable();
  }

  const oxidizedEnabled = platform.status === "fulfilled"
    && platform.value.components?.oxidized?.status !== "disabled";
  const oxidizedResults = oxidizedEnabled
    ? await Promise.allSettled([
        apiRequest("/api/oxidized/status"),
        apiRequest("/api/oxidized/devices"),
      ])
    : [];
  const [overview, devices] = oxidizedResults;
  if (oxidizedEnabled) {
    if (overview.status === "fulfilled") renderOxidizedStatus(overview.value);
    else renderOxidizedStatusUnavailable();
    if (devices.status === "fulfilled") {
      state.devices = devices.value;
      renderDevices();
    } else renderDevicesUnavailable();
  }

  if ([platform, ...oxidizedResults].some(
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
  if (state.authenticated) return {};
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
  if (state.authenticated) {
    state.prophylaxisToken = "";
  } else {
  const token = elements.prophylaxisToken.value.trim();
  if (!token) {
    toast("Enter the operator token.", "warning");
    return;
  }
  state.prophylaxisToken = token;
  }
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
    if (!requireOperatorToken()) return;
    const result = await apiRequest("/api/oxidized/sync", {
      method: "POST",
      headers: operatorHeaders(),
    });
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
  if (!requireOperatorToken()) return;
  if (state.backupsInProgress.has(device)) return;
  state.backupsInProgress.add(device);
  renderDevices();
  try {
    const result = await apiRequest(
      `/api/oxidized/devices/${encodeURIComponent(device)}/backup`,
      { method: "POST", headers: operatorHeaders() },
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
elements.adminSessionButton.addEventListener("click", toggleAdminSession);
elements.adminLoginForm.addEventListener("submit", submitAdminLogin);
elements.syncButton.addEventListener("click", synchronizeInventory);
elements.deviceFilter.addEventListener("input", renderDevices);
elements.deviceSort.addEventListener("change", renderDevices);
elements.prophylaxisRun.addEventListener("click", launchProphylaxisRun);
elements.monitoringLoad.addEventListener("click", loadMonitoringDevices);
elements.monitoringBulk.addEventListener("click", planAllMonitoringDevices);
elements.monitoringBulkApply.addEventListener("click", applyAllMonitoringPlans);
elements.monitoringBulkCancel.addEventListener("click", clearMonitoringBulkPreview);
elements.monitoringMode.addEventListener("change", () => {
  clearMonitoringBulkPreview();
  elements.monitoringPlan.hidden = true;
});
elements.operatorToken.addEventListener("input", () => {
  state.operatorToken = "";
  setPill(elements.operatorStatus, "neutral", "Token required");
});
elements.ciscoTest.addEventListener("click", testCiscoIntegration);
elements.zabbixSync.addEventListener("click", synchronizeZabbix);
elements.inventoryStructureOpen.addEventListener("click", openInventoryStructure);
elements.inventoryStructureClose.addEventListener("click", () => {
  elements.inventoryStructureDialog.close();
});
elements.inventoryStructureKind.addEventListener("change", renderInventoryStructureFields);
elements.inventoryStructureName.addEventListener("input", resetInventoryStructurePlan);
elements.inventoryStructureSlug.addEventListener("input", resetInventoryStructurePlan);
elements.inventoryStructureGroup.addEventListener("input", resetInventoryStructurePlan);
elements.inventoryStructureTenant.addEventListener("change", resetInventoryStructurePlan);
elements.inventoryStructurePlan.addEventListener("click", () => runInventoryStructure(false));
elements.inventoryStructureApply.addEventListener("click", () => runInventoryStructure(true));
elements.inventoryStructureConfirm.addEventListener("change", () => {
  elements.inventoryStructureApply.disabled = !state.inventoryStructurePlan
    || !elements.inventoryStructureConfirm.checked;
});
elements.credentialOpen.addEventListener("click", openCredentialOnboarding);
elements.credentialClose.addEventListener("click", () => {
  clearCredentialSecrets();
  elements.credentialDialog.close();
});
elements.credentialTenant.addEventListener("change", () => {
  state.credentialSelectedDeviceIds.clear();
  elements.credentialDeviceFilter.value = "";
  elements.credentialProfile.value = "";
  elements.credentialProphylaxisUsername.value = "";
  elements.credentialOxidizedUsername.value = "";
  elements.credentialReplace.checked = false;
  clearCredentialSecrets();
  renderCredentialManufacturerOptions();
  renderCredentialProfileOptions();
  renderCredentialDeviceOptions();
  renderCredentialPlatformOptions();
  resetCredentialPlan();
});
elements.credentialManufacturer.addEventListener("change", () => {
  state.credentialSelectedDeviceIds.clear();
  elements.credentialDeviceFilter.value = "";
  renderCredentialDeviceOptions();
  renderCredentialPlatformOptions();
  resetCredentialPlan();
});
elements.credentialDeviceFilter.addEventListener("input", renderCredentialDeviceOptions);
elements.credentialDeviceList.addEventListener("change", (event) => {
  if (!(event.target instanceof HTMLInputElement) || event.target.type !== "checkbox") return;
  const id = Number(event.target.value);
  if (event.target.checked) state.credentialSelectedDeviceIds.add(id);
  else state.credentialSelectedDeviceIds.delete(id);
  credentialDeviceSelectionChanged();
});
elements.credentialPlatform.addEventListener("change", () => {
  renderCredentialAuthOptions();
  resetCredentialPlan();
});
elements.credentialAuth.addEventListener("change", () => {
  renderCredentialAuthFields();
  resetCredentialPlan();
});
elements.credentialClear.addEventListener("click", clearCredentialSecrets);
elements.credentialPlanButton.addEventListener("click", planCredentials);
elements.credentialApply.addEventListener("click", applyCredentials);
elements.credentialApplyConfirm.addEventListener("change", () => {
  elements.credentialApply.disabled = state.credentialBusy
    || !state.credentialPlan?.apply_allowed
    || !elements.credentialApplyConfirm.checked;
});
// VPN profile import never renders or stores decrypted credentials in the UI.
const vpnImport = {
  open: document.querySelector('#vpn-import-open'),
  dialog: document.querySelector('#vpn-import-dialog'),
  close: document.querySelector('#vpn-import-close'),
  message: document.querySelector('#vpn-import-message'),
  file: document.querySelector('#vpn-import-file'),
  format: document.querySelector('#vpn-import-format'),
  profile: document.querySelector('#vpn-import-profile'),
  exportPassword: document.querySelector('#vpn-import-export-password'),
  tenant: document.querySelector('#vpn-import-tenant'),
  vrf: document.querySelector('#vpn-import-vrf'),
  id: document.querySelector('#vpn-import-id'),
  name: document.querySelector('#vpn-import-name'),
  prefixes: document.querySelector('#vpn-import-prefixes'),
  username: document.querySelector('#vpn-import-username'),
  password: document.querySelector('#vpn-import-password'),
  planButton: document.querySelector('#vpn-import-plan'),
  apply: document.querySelector('#vpn-import-apply'),
  review: document.querySelector('#vpn-import-review'),
  data: null, options: null, plan: null, busy: false, revision: 0,
};

function resetVPNImportPlan() {
  vpnImport.revision += 1;
  vpnImport.plan = null;
  vpnImport.apply.disabled = true;
  vpnImport.review.hidden = true;
  vpnImport.review.textContent = '';
}

function vpnImportMessage(message) { vpnImport.message.textContent = message; }

function renderVPNImportVrfs() {
  const owner = Number(vpnImport.tenant.value);
  vpnImport.vrf.replaceChildren(new Option('Select a management VRF', ''),
    ...(vpnImport.options?.vrfs || []).filter((vrf) => vrf.tenant_id === owner)
      .map((vrf) => new Option(vrf.name, String(vrf.id))));
}

async function openVPNImport() {
  if (!requireOperatorToken()) return;
  vpnImport.dialog.showModal();
  vpnImportMessage('Loading customers and management VRFs…');
  try {
    vpnImport.options = await apiRequest('/api/v1/vpn/forticlient/options', { headers: operatorHeaders() });
    vpnImport.tenant.replaceChildren(new Option('Select a customer', ''),
      ...vpnImport.options.tenants.map((tenant) => new Option(tenant.name, String(tenant.id))));
    renderVPNImportVrfs();
    vpnImportMessage('Select a profile file. Import saves settings but does not connect the VPN.');
  } catch (error) {
    vpnImportMessage(error.safeMessage || 'Could not load customers.');
  }
}

async function inspectVPNImportFile() {
  resetVPNImportPlan();
  vpnImport.data = null;
  vpnImport.profile.replaceChildren(new Option('Choose a file first', ''));
  const file = vpnImport.file.files?.[0];
  const revision = vpnImport.revision;
  if (!file) return;
  if (file.size === 0 || file.size > 2 * 1024 * 1024) {
    vpnImportMessage('The file must be between 1 byte and 2 MiB.');
    return;
  }
  try {
    const encoded = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(',')[1]);
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
    const result = await apiRequest('/api/v1/vpn/forticlient/inspect', {
      method: 'POST', headers: operatorHeaders(),
      body: { format: vpnImport.format.value, profile_base64: encoded },
    });
    if (!vpnImport.dialog.open || vpnImport.revision !== revision || vpnImport.file.files?.[0] !== file) return;
    vpnImport.data = encoded;
    vpnImport.profile.replaceChildren(new Option('Select a VPN profile', ''),
      ...result.profiles.map((name) => new Option(name, name)));
    if (result.profiles.length === 1) vpnImport.profile.value = result.profiles[0];
    vpnImportMessage('Choose a customer and management VRF, enter allowed networks and VPN credentials.');
  } catch (error) {
    vpnImportMessage(error.safeMessage || 'Could not read this profile.');
  }
}

function vpnImportPayload() {
  return {
    format: vpnImport.format.value, profile_base64: vpnImport.data || '',
    profile_name: vpnImport.profile.value,
    export_password: vpnImport.exportPassword.value || null,
    username: vpnImport.username.value, password: vpnImport.password.value,
    connection_id: vpnImport.id.value.trim(), name: vpnImport.name.value.trim(),
    tenant_id: Number(vpnImport.tenant.value), management_vrf_id: Number(vpnImport.vrf.value),
    allowed_prefixes: vpnImport.prefixes.value.split(',').map((part) => part.trim()).filter(Boolean),
  };
}

async function planVPNImport() {
  if (vpnImport.busy || !requireOperatorToken()) return;
  resetVPNImportPlan();
  vpnImport.busy = true;
  vpnImport.planButton.disabled = true;
  const revision = vpnImport.revision;
  try {
    const result = await apiRequest('/api/v1/vpn/forticlient/plan', {
      method: 'POST', headers: operatorHeaders(), body: vpnImportPayload(),
    });
    if (!vpnImport.dialog.open || vpnImport.revision !== revision) return;
    vpnImport.plan = result;
    vpnImport.review.textContent = `Customer ID ${result.tenant_id}; VRF ${result.management_vrf_id}; gateway ${result.gateway}:${result.port}; Network ID ${result.network_id}; allowed networks ${result.allowed_prefixes.join(', ')}. Registry: ${result.registry_action}; OpenBao: ${result.secret_action}.`;
    vpnImport.review.hidden = false;
    vpnImport.apply.disabled = !result.apply_allowed;
    vpnImportMessage('Review the settings, then save the profile.');
  } catch (error) {
    const messages = {
      vpn_export_password_required: 'Enter the password used to export this profile.',
      encrypted_secret_integrity_failed: 'The export password or encrypted profile could not be verified.',
      vpn_connection_conflict: 'This connection ID already belongs to different settings.',
      vpn_secret_conflict: 'Credentials already exist for this connection and will not be overwritten.',
      vpn_secret_unavailable: 'VPN credential storage is unavailable. Check the VPN writer identity.',
      vpn_secret_format_unsupported: 'This encrypted secret format is not supported.',
      unsupported_encrypted_secret_format: 'This encrypted secret format is not supported.',
      unsupported_ipsec_profile: 'This IPsec configuration is not supported by the current import.',
      unsupported_proposal: 'The encryption proposal in this profile is not supported.',
      unsupported_username: 'The username contains unsupported characters.',
      unsupported_local_id: 'The local ID contains unsupported characters.',
      unsupported_auth_method: 'This authentication method is not supported.',
      invalid_encrypted_secret: 'The encrypted PSK is invalid or incomplete.',
      invalid_decrypted_secret: 'The decrypted PSK is invalid.',
    };
    vpnImportMessage(messages[error.code] || error.safeMessage || 'Could not prepare the import.');
  } finally {
    vpnImport.busy = false;
    vpnImport.planButton.disabled = false;
  }
}

async function applyVPNImport() {
  if (!vpnImport.plan || vpnImport.busy) return;
  vpnImport.busy = true;
  vpnImport.apply.disabled = true;
  try {
    const result = await apiRequest('/api/v1/vpn/forticlient/apply', {
      method: 'POST', headers: operatorHeaders(),
      body: { ...vpnImportPayload(), plan_hash: vpnImport.plan.plan_hash },
    });
    vpnImportMessage(`Saved ${result.connection_id}. VPN runtime is not deployed; assign the connection to sites separately.`);
    vpnImport.exportPassword.value = '';
    vpnImport.username.value = '';
    vpnImport.password.value = '';
    vpnImport.data = null;
    vpnImport.file.value = '';
    resetVPNImportPlan();
  } catch (error) {
    resetVPNImportPlan();
    vpnImportMessage(error.code === 'vpn_import_partial'
      ? 'The registry was created but OpenBao could not be saved. Review the same import again to retry.'
      : (error.safeMessage || 'Could not save this profile.'));
  } finally { vpnImport.busy = false; }
}

vpnImport.open.addEventListener('click', openVPNImport);
vpnImport.close.addEventListener('click', () => { if (!vpnImport.busy) vpnImport.dialog.close(); });
vpnImport.dialog.addEventListener('cancel', (event) => { if (vpnImport.busy) event.preventDefault(); });
vpnImport.dialog.addEventListener('close', () => {
  vpnImport.exportPassword.value = ''; vpnImport.username.value = ''; vpnImport.password.value = '';
  vpnImport.file.value = ''; vpnImport.data = null; resetVPNImportPlan();
});
vpnImport.file.addEventListener('change', inspectVPNImportFile);
vpnImport.format.addEventListener('change', inspectVPNImportFile);
vpnImport.tenant.addEventListener('change', renderVPNImportVrfs);
for (const field of [vpnImport.profile, vpnImport.exportPassword, vpnImport.tenant, vpnImport.vrf,
                     vpnImport.id, vpnImport.name, vpnImport.prefixes, vpnImport.username, vpnImport.password]) {
  field.addEventListener('input', resetVPNImportPlan);
  field.addEventListener('change', resetVPNImportPlan);
}
vpnImport.planButton.addEventListener('click', planVPNImport);
vpnImport.apply.addEventListener('click', applyVPNImport);

elements.vpnAssignmentOpen.addEventListener("click", openVPNAssignment);
elements.vpnAssignmentClose.addEventListener("click", () => {
  resetVPNAssignmentPlan();
  elements.vpnAssignmentDialog.close();
});
elements.vpnAssignmentTenant.addEventListener("change", loadVPNAssignmentOptions);
elements.vpnAssignmentConnection.addEventListener("change", () => {
  resetVPNAssignmentPlan();
  updateVPNAssignmentControls();
});
elements.vpnAssignmentTargetType.addEventListener("change", () => {
  state.vpnAssignmentSelectedIds.clear();
  elements.vpnAssignmentFilter.value = "";
  resetVPNAssignmentPlan();
  renderVPNAssignmentTargets();
});
elements.vpnAssignmentFilter.addEventListener("input", renderVPNAssignmentTargets);
elements.vpnAssignmentTargets.addEventListener("change", (event) => {
  if (!(event.target instanceof HTMLInputElement) || event.target.type !== "checkbox") return;
  const id = Number(event.target.value);
  if (event.target.checked) state.vpnAssignmentSelectedIds.add(id);
  else state.vpnAssignmentSelectedIds.delete(id);
  resetVPNAssignmentPlan();
  updateVPNAssignmentControls();
});
elements.vpnAssignmentSelectAll.addEventListener("click", () => {
  const visible = filteredVPNAssignmentTargets();
  const allSelected = visible.length > 0
    && visible.every((target) => state.vpnAssignmentSelectedIds.has(target.id));
  for (const target of visible) {
    if (allSelected) state.vpnAssignmentSelectedIds.delete(target.id);
    else state.vpnAssignmentSelectedIds.add(target.id);
  }
  resetVPNAssignmentPlan();
  renderVPNAssignmentTargets();
});
elements.vpnAssignmentPlanButton.addEventListener("click", planVPNAssignment);
elements.vpnAssignmentApply.addEventListener("click", applyVPNAssignment);
elements.vpnAssignmentConfirm.addEventListener("change", () => {
  elements.vpnAssignmentApply.disabled = state.vpnAssignmentBusy
    || !state.vpnAssignmentPlan
    || !elements.vpnAssignmentConfirm.checked;
});
for (const input of [
  elements.credentialProfile,
  elements.credentialProphylaxisEnabled,
  elements.credentialProphylaxisUsername,
  elements.credentialProphylaxisPassword,
  elements.credentialProphylaxisEnable,
  elements.credentialProphylaxisApiToken,
  elements.credentialFortiosApiPort,
  elements.credentialOxidizedEnabled,
  elements.credentialOxidizedUsername,
  elements.credentialOxidizedPassword,
  elements.credentialMonitoringEnabled,
  elements.credentialSnmpCommunity,
  elements.credentialReplace,
]) input.addEventListener("input", resetCredentialPlan);
elements.inventoryImportOpen.addEventListener("click", openInventoryImport);
elements.inventoryImportClose.addEventListener("click", () => {
  elements.inventoryImportDialog.close();
});
elements.inventoryExample.addEventListener("click", () => {
  elements.inventoryTable.value = [
    "Hostname\tHardware model\tManagement IP\tTenant\tSite",
    "BR-SW01\tC9500-48Y4C\t10.0.0.10\tacme-slovakia\tBratislava DC",
  ].join("\n");
  resetInventoryPlan();
  elements.inventoryTable.focus();
});
elements.inventoryClear.addEventListener("click", () => {
  elements.inventoryTable.value = "";
  elements.inventoryTenant.value = "";
  resetInventorySiteOptions();
  elements.inventoryDeviceTypeYaml.value = "";
  setInventoryDeviceTypeError();
  elements.inventoryDeviceTypeReplace.checked = false;
  resetInventoryPlan();
  elements.inventoryTable.focus();
});
elements.inventoryPlanButton.addEventListener("click", planInventory);
elements.inventoryRetry.addEventListener("click", planInventory);
elements.inventoryDeviceTypeUpload.addEventListener("click", uploadInventoryDeviceType);
elements.inventoryDeviceTypeModel.addEventListener("change", updateInventoryDeviceTypeFilename);
elements.inventoryDeviceTypeYaml.addEventListener("input", () => setInventoryDeviceTypeError());
elements.inventoryApplyConfirm.addEventListener("change", () => {
  elements.inventoryApply.disabled = state.inventoryBusy
    || !state.inventoryPlan?.apply_allowed
    || !elements.inventoryApplyConfirm.checked;
});
elements.inventoryApply.addEventListener("click", applyInventory);
elements.inventoryTable.addEventListener("input", resetInventoryPlan);
elements.inventoryTenant.addEventListener("change", async () => {
  resetInventoryPlan();
  try {
    await loadInventoryImportSites();
  } catch (error) {
    resetInventorySiteOptions("Site list unavailable");
    toast(error.safeMessage || "Site list is unavailable for this tenant.", "error");
  }
});
elements.inventorySite.addEventListener("change", resetInventoryPlan);

refreshAdminSession({ prompt: true }).finally(loadDashboard);
