const state = {
  bookings: [],
  bookingHistory: [],
  selectedBill: "",
  jobTimer: null,
  jobLineCounts: {},
  uiLogs: [],
  logWindowHours: 6,
  availabilityLoading: false,
  availabilityDays: [],
  selectedAvailabilitySlots: [],
  primaryCard: null,
  exactBookingLoading: false,
  availabilityWarningTimer: null,
  scanTasks: [],
  scanEvents: [],
  scanTargetCounter: 0,
  scanTaskLoading: false,
  cancelDialogBill: "",
  cancelDialogPreview: null,
  cancelDialogError: "",
  cancelDialogLoading: false,
  cards: [],
  startHour: 17,
  endHour: 21,
  priorityCourts: [6, 7, 8, 9],
  activeBookingsExpanded: false,
  availabilityExpandedDates: new Set(),
  alignedRefreshTimer: null,
  refreshInFlight: false,
  refreshQueued: false,
  refreshQueuedForce: false,
  refreshStamps: {},
  accessExpiryTimer: null,
  viewMode: "default",
  users: [],
  selectedUserKey: "",
  userManagementUnlocked: false,
  adminPassword: "",
};

const SAFE_COURTS = [1, 2, 3, 6, 7, 8, 9, 10, 11];
const WALL_COURTS = [4, 5, 12];
const ALL_COURTS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];
const VISIBLE_SCAN_TASK_STATUSES = new Set(["active", "paused"]);
const LOG_WINDOW_OPTIONS = [
  { hours: 6, label: "6 小时" },
  { hours: 12, label: "12 小时" },
  { hours: 24, label: "24 小时" },
  { hours: 168, label: "一周" },
];
const LOG_RETENTION_HOURS = 168;
const LOG_STORAGE_KEY = "daydayupLogWindowHours";
const ACCESS_KEY_STORAGE_KEY = "daydayupAccessKey";
const ACCESS_EXPIRES_STORAGE_KEY = "daydayupAccessExpiresAt";
const ACCESS_SESSION_TTL_MS = 60 * 60 * 1000;
const ALIGNED_REFRESH_MS = 5000;
const AVAILABILITY_REFRESH_TTL_MS = 60000;
const MIN_HOUR = 8;
const MAX_HOUR = 23;

state.logWindowHours = readStoredLogWindowHours();

const els = {
  authScreen: document.querySelector("#authScreen"),
  authForm: document.querySelector("#authForm"),
  authMessage: document.querySelector("#authMessage"),
  appShell: document.querySelector("#appShell"),
  userSelect: document.querySelector("#userSelect"),
  activeUserLabel: document.querySelector("#activeUserLabel"),
  userUnlockForm: document.querySelector("#userUnlockForm"),
  lockUserPanel: document.querySelector("#lockUserPanel"),
  userForm: document.querySelector("#userForm"),
  copyTokenAuthUrl: document.querySelector("#copyTokenAuthUrl"),
  exchangeToken: document.querySelector("#exchangeToken"),
  tokenHelperMessage: document.querySelector("#tokenHelperMessage"),
  refreshAll: document.querySelector("#refreshAll"),
  viewSwitcher: document.querySelector("#viewSwitcher"),
  viewModeDetails: document.querySelector("#viewModeDetails"),
  refreshCards: document.querySelector("#refreshCards"),
  tokenState: document.querySelector("#tokenState"),
  sessionState: document.querySelector("#sessionState"),
  sessionHelp: document.querySelector("#sessionHelp"),
  sessionHelpWrap: document.querySelector(".status-help"),
  sessionHelpTrigger: document.querySelector("#sessionHelpTrigger"),
  lastRefresh: document.querySelector("#lastRefresh"),
  primaryCard: document.querySelector("#primaryCard"),
  otherCards: document.querySelector("#otherCards"),
  bookingList: document.querySelector("#bookingList"),
  refreshBookings: document.querySelector("#refreshBookings"),
  scanAvailability: document.querySelector("#scanAvailability"),
  availabilityList: document.querySelector("#availabilityList"),
  availabilityWarning: document.querySelector("#availabilityWarning"),
  availabilityCombos: document.querySelector("#availabilityCombos"),
  availabilitySelection: document.querySelector("#availabilitySelection"),
  exactSelectionList: document.querySelector("#exactSelectionList"),
  exactSelectionTotal: document.querySelector("#exactSelectionTotal"),
  exactSelectionBalance: document.querySelector("#exactSelectionBalance"),
  exactSubmit: document.querySelector("#exactSubmit"),
  scanTaskForm: document.querySelector("#scanTaskForm"),
  scanTargetList: document.querySelector("#scanTargetList"),
  addScanTarget: document.querySelector("#addScanTarget"),
  refreshScanTasks: document.querySelector("#refreshScanTasks"),
  scanTaskList: document.querySelector("#scanTaskList"),
  scanEventList: document.querySelector("#scanEventList"),
  refreshBookingHistory: document.querySelector("#refreshBookingHistory"),
  bookingHistoryList: document.querySelector("#bookingHistoryList"),
  bookingForm: document.querySelector("#bookingForm"),
  timeStartValue: document.querySelector("#timeStartValue"),
  timeEndValue: document.querySelector("#timeEndValue"),
  timeRangeValue: document.querySelector("#timeRangeValue"),
  courtPicker: document.querySelector("#courtPicker"),
  priorityValue: document.querySelector("#priorityValue"),
  durationPicker: document.querySelector("#durationPicker"),
  windowSlider: document.querySelector("#windowSlider"),
  windowValue: document.querySelector("#windowValue"),
  pollPicker: document.querySelector("#pollPicker"),
  stopJob: document.querySelector("#stopJob"),
  jobState: document.querySelector("#jobState"),
  logStream: document.querySelector("#logStream"),
  detailEmpty: document.querySelector("#detailEmpty"),
  detailContent: document.querySelector("#detailContent"),
  cancelDialog: document.querySelector("#cancelDialog"),
  cancelDialogBody: document.querySelector("#cancelDialogBody"),
  closeCancelDialog: document.querySelector("#closeCancelDialog"),
  cancelDialogBack: document.querySelector("#cancelDialogBack"),
  cancelDialogSubmit: document.querySelector("#cancelDialogSubmit"),
};

function fmtTime(ts = Date.now()) {
  return new Date(ts).toLocaleTimeString("zh-CN", { hour12: false });
}

function fmtLogTime(ts = Date.now()) {
  return new Date(ts).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function readStoredLogWindowHours() {
  return normalizeLogWindowHours(localStorage.getItem(LOG_STORAGE_KEY));
}

function normalizeLogWindowHours(value) {
  const hours = Number(value);
  return LOG_WINDOW_OPTIONS.some((option) => option.hours === hours) ? hours : 6;
}

function logWindowLabel(hours = state.logWindowHours) {
  return LOG_WINDOW_OPTIONS.find((option) => option.hours === hours)?.label || "6 小时";
}

function logWindowQuery() {
  return `log_window_hours=${encodeURIComponent(state.logWindowHours)}`;
}

function logWindowCutoffMs(hours = state.logWindowHours) {
  return Date.now() - hours * 60 * 60 * 1000;
}

function dateInputValue(offsetDays = 0) {
  const date = new Date();
  date.setDate(date.getDate() + offsetDays);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function clearStoredAccess() {
  localStorage.removeItem(ACCESS_KEY_STORAGE_KEY);
  localStorage.removeItem(ACCESS_EXPIRES_STORAGE_KEY);
}

function storeAccessKey(accessKey) {
  localStorage.setItem(ACCESS_KEY_STORAGE_KEY, accessKey);
  localStorage.setItem(ACCESS_EXPIRES_STORAGE_KEY, String(Date.now() + ACCESS_SESSION_TTL_MS));
}

function readStoredAccessKey(now = Date.now()) {
  const accessKey = localStorage.getItem(ACCESS_KEY_STORAGE_KEY);
  if (!accessKey) {
    localStorage.removeItem(ACCESS_EXPIRES_STORAGE_KEY);
    return "";
  }

  const expiresAt = Number(localStorage.getItem(ACCESS_EXPIRES_STORAGE_KEY));
  if (!Number.isFinite(expiresAt) || expiresAt <= now) {
    clearStoredAccess();
    return "";
  }

  return accessKey;
}

function clearAccessExpiryTimer() {
  if (state.accessExpiryTimer) {
    window.clearTimeout(state.accessExpiryTimer);
    state.accessExpiryTimer = null;
  }
}

function scheduleAccessExpiry() {
  clearAccessExpiryTimer();
  const expiresAt = Number(localStorage.getItem(ACCESS_EXPIRES_STORAGE_KEY));
  if (!Number.isFinite(expiresAt)) {
    return;
  }

  state.accessExpiryTimer = window.setTimeout(() => {
    expireAccessSession("访问已过期，请重新输入密码。");
  }, Math.max(0, expiresAt - Date.now()));
}

function expireAccessSession(message = "访问已过期，请重新输入密码。") {
  clearStoredAccess();
  clearAlignedRefresh();
  clearAccessExpiryTimer();
  state.adminPassword = "";
  state.userManagementUnlocked = false;
  showLogin(message);
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json" };
  const accessKey = readStoredAccessKey();
  if (!accessKey) {
    expireAccessSession();
    throw new Error("access expired");
  }
  headers["X-Daydayup-Key"] = accessKey;

  let response = await fetch(path, {
    headers,
    ...options,
  });
  if (response.status === 401) {
    expireAccessSession("访问失败或已过期，请重新输入密码。");
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || "request failed");
  }
  return payload;
}

function userScopedPath(path) {
  if (!state.selectedUserKey) {
    return path;
  }
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}user_key=${encodeURIComponent(state.selectedUserKey)}`;
}

function msUntilNextRefreshBoundary(now = Date.now()) {
  const remainder = now % ALIGNED_REFRESH_MS;
  return remainder === 0 ? 0 : ALIGNED_REFRESH_MS - remainder;
}

function clearAlignedRefresh() {
  if (state.alignedRefreshTimer) {
    window.clearTimeout(state.alignedRefreshTimer);
    state.alignedRefreshTimer = null;
  }
}

function scheduleAlignedRefresh() {
  clearAlignedRefresh();
  state.alignedRefreshTimer = window.setTimeout(() => {
    runAlignedRefresh();
  }, msUntilNextRefreshBoundary());
}

function startAlignedRefresh() {
  scheduleAlignedRefresh();
}

async function runAlignedRefresh() {
  await refreshLiveData();
  scheduleAlignedRefresh();
}

function clearRefreshStamps() {
  state.refreshStamps = {};
}

function shouldRefreshAvailability(force = false) {
  if (force) {
    return true;
  }
  const lastRefresh = Number(state.refreshStamps.availability || 0);
  return !lastRefresh || Date.now() - lastRefresh >= AVAILABILITY_REFRESH_TTL_MS;
}

async function refreshAvailabilityIfDue({ force = false } = {}) {
  if (!shouldRefreshAvailability(force)) {
    return null;
  }
  return loadAvailabilitySnapshot({ silent: true, preserveSelection: true });
}

async function refreshLiveData({ force = false } = {}) {
  if (state.refreshInFlight) {
    state.refreshQueued = true;
    state.refreshQueuedForce = state.refreshQueuedForce || force;
    return;
  }
  state.refreshInFlight = true;
  try {
    await Promise.all([
      loadStatus(),
      loadCards(),
      loadBookings(),
      loadBookingHistory(),
      loadScanTasks(),
      loadJob(),
      refreshAvailabilityIfDue({ force }),
    ]);
    renderViewModeDetails();
  } catch (error) {
    markConnectivity(false);
    addUiLog(`刷新失败: ${error.message}`, true);
  } finally {
    state.refreshInFlight = false;
    if (state.refreshQueued) {
      const queuedForce = state.refreshQueuedForce;
      state.refreshQueued = false;
      state.refreshQueuedForce = false;
      await refreshLiveData({ force: queuedForce });
    }
  }
}

async function triggerRefresh({ includeUsers = false, force = false } = {}) {
  if (includeUsers) {
    await loadUsers();
  }
  await refreshLiveData({ force });
  scheduleAlignedRefresh();
}

function markConnectivity(ok) {
  setPill(els.lastRefresh, ok ? "活跃" : "离线", ok ? "ok" : "danger");
  els.lastRefresh.title = ok ? `最近刷新 ${fmtTime()}` : `刷新失败 ${fmtTime()}`;
}

async function login(event) {
  event.preventDefault();
  const password = new FormData(els.authForm).get("password") || "";
  try {
    await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    }).then(async (response) => {
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || "login failed");
      }
      return payload;
    });
    storeAccessKey(password);
    els.authForm.reset();
    await showApp();
  } catch (error) {
    els.authMessage.textContent = `访问失败: ${error.message}`;
  }
}

function showLogin(message = "") {
  clearAlignedRefresh();
  clearAccessExpiryTimer();
  els.authScreen.classList.remove("hidden");
  els.appShell.classList.add("hidden");
  if (message) {
    els.authMessage.textContent = message;
  }
}

async function showApp() {
  scheduleAccessExpiry();
  els.authScreen.classList.add("hidden");
  els.appShell.classList.remove("hidden");
  await loadUsers();
  startAlignedRefresh();
}

function setPill(el, text, tone = "") {
  el.className = `status-pill ${tone}`.trim();
  el.textContent = text;
}

function setChip(el, text, tone = "") {
  el.className = `chip ${tone}`.trim();
  el.textContent = text;
}

function renderLogWindowButtons() {
  return LOG_WINDOW_OPTIONS.map((option) => `
    <button
      type="button"
      data-log-window-hours="${escapeAttr(option.hours)}"
      aria-pressed="${option.hours === state.logWindowHours ? "true" : "false"}"
    >
      ${escapeHtml(option.label)}
    </button>
  `).join("");
}

function renderLogWindowControl(label) {
  return `
    <div class="log-window-control" data-log-window-control role="group" aria-label="${escapeAttr(label)}">
      ${renderLogWindowButtons()}
    </div>
  `;
}

function renderLogWindowControls() {
  document.querySelectorAll("[data-log-window-control]").forEach((control) => {
    control.setAttribute("role", "group");
    control.innerHTML = renderLogWindowButtons();
  });
}

function setupLogWindowControls() {
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-log-window-hours]");
    if (!button) {
      return;
    }
    setLogWindowHours(button.dataset.logWindowHours);
  });
  renderLogWindowControls();
}

function setLogWindowHours(value) {
  const next = normalizeLogWindowHours(value);
  if (state.logWindowHours === next) {
    return;
  }
  state.logWindowHours = next;
  localStorage.setItem(LOG_STORAGE_KEY, String(next));
  renderLogWindowControls();
  renderUiLogs();
  renderViewModeDetails();
  Promise.all([loadBookingHistory(), loadScanTasks()]).catch((error) => {
    addUiLog(`日志时间窗刷新失败: ${error.message}`, true);
  });
}

function pruneUiLogs() {
  const cutoff = Date.now() - LOG_RETENTION_HOURS * 60 * 60 * 1000;
  state.uiLogs = state.uiLogs.filter((item) => Number(item.ts) >= cutoff).slice(0, 500);
}

function visibleUiLogs() {
  const cutoff = logWindowCutoffMs();
  return state.uiLogs.filter((item) => Number(item.ts) >= cutoff);
}

function renderUiLogs() {
  pruneUiLogs();
  const logs = visibleUiLogs();
  if (!logs.length) {
    els.logStream.innerHTML = `<div class="empty-state compact">当前 ${escapeHtml(logWindowLabel())} 内没有页面日志。</div>`;
    return;
  }
  els.logStream.innerHTML = logs.map((item) => `
    <div class="${item.strong ? "log-line strong" : "log-line"}">
      [${escapeHtml(fmtLogTime(item.ts))}] ${escapeHtml(item.text)}
    </div>
  `).join("");
}

function addUiLog(text, strong = false) {
  state.uiLogs.unshift({ ts: Date.now(), text: String(text || ""), strong: Boolean(strong) });
  renderUiLogs();
  renderViewModeDetails();
}

async function loadUsers() {
  const data = await api("/api/users");
  state.users = data.users || [];
  const previousUserKey = state.selectedUserKey;
  if (!state.selectedUserKey || !state.users.some((user) => user.key === state.selectedUserKey)) {
    state.selectedUserKey = data.default_user_key || state.users[0]?.key || "";
  }
  if (previousUserKey !== state.selectedUserKey) {
    clearRefreshStamps();
  }
  renderUsers();
  return data;
}

function renderUsers() {
  els.userSelect.innerHTML = state.users.map((user) => `
    <option value="${escapeAttr(user.key)}" ${user.key === state.selectedUserKey ? "selected" : ""}>
      ${escapeHtml(user.label)}
    </option>
  `).join("");
  const current = currentUser();
  els.activeUserLabel.textContent = current ? current.label : "未选择";
  renderUserManagementLock();
}

function currentUser() {
  return state.users.find((user) => user.key === state.selectedUserKey) || null;
}

function renderUserManagementLock() {
  els.userUnlockForm.classList.toggle("hidden", state.userManagementUnlocked);
  els.userForm.classList.toggle("hidden", !state.userManagementUnlocked);
  els.lockUserPanel.classList.toggle("hidden", !state.userManagementUnlocked);
}

async function loadStatus() {
  const status = await api(userScopedPath("/api/status"));
  const credentials = status.user?.credential_status || status;
  const tokenState = credentials.token || {};
  const sessionState = credentials.jsessionid || {};
  const hasSession = Boolean(sessionState.present);
  setPill(els.tokenState, tokenState.present ? "Token ✅" : "Token ❌", tokenState.present ? "ok" : "danger");
  setPill(
    els.sessionState,
    hasSession ? "Session ✅" : "Session ❌",
    hasSession ? "ok" : "warn",
  );
  els.sessionHelpWrap.classList.toggle("session-ok", hasSession);
  els.sessionHelpTrigger.hidden = hasSession;
  els.sessionHelp.hidden = hasSession;
  els.sessionHelpTrigger.setAttribute("aria-expanded", "false");
  markConnectivity(true);
  return status;
}

async function loadCards() {
  const data = await api(userScopedPath("/api/cards"));
  renderCards(data.cards, data.primary_card);
  return data;
}

function renderCards(cards, primaryCard) {
  state.cards = cards || [];
  state.primaryCard = primaryCard || null;
  if (!primaryCard) {
    els.primaryCard.innerHTML = `<div class="empty-state">未查询到会员卡</div>`;
    els.otherCards.innerHTML = "";
    renderAvailabilityTools();
    return;
  }

  els.primaryCard.innerHTML = `
    <div class="balance-value">${escapeHtml(primaryCard.cash_balance)}</div>
  `;

  const others = cards.filter((card) => card.card_index !== primaryCard.card_index);
  els.otherCards.innerHTML = others.length
    ? others.map((card) => `
      <div class="mini-item">
        <span>${escapeHtml(card.card_name)} · ${escapeHtml(card.card_index)}</span>
        <span class="numeric">${escapeHtml(card.cash_balance)}</span>
      </div>
    `).join("")
    : `<div class="mini-item"><span>没有其他卡</span></div>`;
  renderAvailabilityTools();
}

async function loadBookings() {
  const data = await api(userScopedPath("/api/bookings?success=1&all=0"));
  state.bookings = data.bookings;
  renderBookings(data.bookings);
  renderViewModeDetails();
  return data;
}

function renderBookings(bookings) {
  const sortedBookings = sortBookingsByStart(bookings || []).filter(isUpcomingBooking);
  if (!sortedBookings.length) {
    els.bookingList.innerHTML = `<div class="empty-state compact">没有活跃预约</div>`;
    return;
  }

  const visibleBookings = state.activeBookingsExpanded ? sortedBookings : sortedBookings.slice(0, 3);
  const toggleMarkup = sortedBookings.length > 3
    ? `<button class="button secondary compact active-toggle" type="button" data-active-toggle>${state.activeBookingsExpanded ? "收起" : `展开 ${sortedBookings.length - 3}`}</button>`
    : "";

  els.bookingList.innerHTML = `
    <div class="active-booking-grid">
      ${visibleBookings.map((booking) => {
    const selected = booking.bill_num === state.selectedBill ? " selected" : "";
    const refundState = bookingRefundState(booking);
    return `
      <details class="booking-row${selected}" data-bill="${escapeAttr(booking.bill_num)}">
        <summary>
          <span class="booking-summary"><span class="booking-time">${escapeHtml(activeBookingLabel(booking))}</span></span>
          <span class="chip ${refundState.tone}">${escapeHtml(refundState.label)}</span>
        </summary>
        <div class="booking-detail-line">
          <span>bill <span class="numeric">${escapeHtml(booking.bill_num || "-")}</span></span>
          <span>${escapeHtml(booking.amount || "-")}</span>
          ${!booking.cancelled && !refundState.expired ? renderRefundAction(booking, refundState) : ""}
        </div>
      </details>
    `;
      }).join("")}
    </div>
    ${toggleMarkup}
  `;

  els.bookingList.querySelectorAll(".booking-row").forEach((row) => {
    row.addEventListener("toggle", () => {
      if (row.open) {
        selectBooking(row.dataset.bill, { scroll: false, renderList: false });
      }
    });
    row.addEventListener("keydown", (event) => {
      if (event.target.closest("[data-cancel-bill]")) {
        return;
      }
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectBooking(row.dataset.bill);
      }
    });
  });
  els.bookingList.querySelector("[data-active-toggle]")?.addEventListener("click", () => {
    state.activeBookingsExpanded = !state.activeBookingsExpanded;
    renderBookings(state.bookings);
  });
  els.bookingList.querySelectorAll("[data-cancel-bill]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      openCancelDialog(button.dataset.cancelBill);
    });
  });
}

function sortBookingsByStart(bookings) {
  return (bookings || []).slice().sort((a, b) => bookingStartMs(a) - bookingStartMs(b));
}

function bookingStartMs(booking) {
  const date = String(booking.date || "").trim();
  const range = String(booking.time_range || "").trim();
  const start = range.split("-")[0] || "00:00";
  const parsed = new Date(`${date}T${start}:00`);
  return Number.isNaN(parsed.getTime()) ? Number.MAX_SAFE_INTEGER : parsed.getTime();
}

function isUpcomingBooking(booking) {
  const startMs = bookingStartMs(booking);
  return startMs === Number.MAX_SAFE_INTEGER || startMs >= Date.now();
}

function activeBookingLabel(booking) {
  const dateText = String(booking.date || "").trim();
  const match = dateText.match(/(\d{4})[-/](\d{1,2})[-/](\d{1,2})/);
  const dateLabel = match ? `${Number(match[2])}.${Number(match[3])}` : dateText || "-";
  return `${dateLabel}-${courtNumberLabel(booking.court)}`;
}

function courtNumberLabel(value) {
  const text = String(value || "场地").trim();
  const match = text.match(/(\d+)/);
  return match ? `${match[1]} 号` : text;
}

function courtShortLabel(value) {
  const text = String(value || "场地").trim();
  const match = text.match(/(\d+)/);
  return match ? `球${match[1]}` : text;
}

function renderRefundAction(booking, refundState) {
  if (!booking.cancelled && !refundState.expired) {
    return `<button class="chip chip-button ${refundState.tone}" type="button" data-cancel-bill="${escapeAttr(booking.bill_num)}">${escapeHtml(refundState.label)}</button>`;
  }
  return `<span class="chip ${refundState.tone}">${escapeHtml(refundState.label)}</span>`;
}

function selectBooking(billNum, options = {}) {
  state.selectedBill = billNum;
  if (options.renderList !== false) {
    renderBookings(state.bookings);
  }
  const booking = state.bookings.find((item) => item.bill_num === billNum);
  renderDetail(booking);
  renderViewModeDetails();
  const detailPane = document.querySelector("#detailPane");
  if (options.scroll !== false && detailPane && getComputedStyle(detailPane).display !== "none") {
    detailPane.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function renderDetail(booking, preview = null, error = "") {
  if (!booking) {
    els.detailEmpty.classList.remove("hidden");
    els.detailContent.classList.add("hidden");
    return;
  }

  els.detailEmpty.classList.add("hidden");
  els.detailContent.classList.remove("hidden");
  const previewMarkup = preview ? renderPreview(preview) : "";
  const errorMarkup = error ? `<div class="refund-box"><strong>退款预览不可用</strong><p>${escapeHtml(error)}</p></div>` : "";
  const refundState = bookingRefundState(booking);
  const cancelDisabled = booking.cancelled || refundState.expired ? "disabled" : "";
  const previewLabel = refundState.expired ? "过期不可退" : "查看退款预览";

  els.detailContent.innerHTML = `
    <div>
      <p class="eyebrow">Selected</p>
      <h2>${escapeHtml(booking.date)} ${escapeHtml(booking.time_range)}</h2>
      <p class="muted">${escapeHtml(booking.court || "场地")} · bill <span class="numeric">${escapeHtml(booking.bill_num)}</span></p>
    </div>
    <div class="detail-grid">
      ${dataRow("状态", refundState.label)}
      ${dataRow("金额", booking.amount || "-")}
      ${dataRow("支付", booking.pay_type || "-")}
      ${dataRow("创建", booking.created_at || "-")}
    </div>
    <button class="button secondary" id="previewCancel" type="button" ${cancelDisabled}>${previewLabel}</button>
    ${errorMarkup}
    ${previewMarkup}
  `;

  const previewButton = document.querySelector("#previewCancel");
  if (previewButton) {
    previewButton.addEventListener("click", () => loadCancelPreview(booking.bill_num));
  }

  const cancelButton = document.querySelector("#confirmCancel");
  if (cancelButton) {
    cancelButton.addEventListener("click", () => cancelBooking(booking.bill_num));
  }
}

function renderPreview(preview) {
  const refund = preview.refund || {};
  const rule = preview.rule || {};
  return `
    <div class="refund-box">
      <strong>退款预览</strong>
      <div class="refund-grid">
        ${dataRow("支付金额", refund.pay_money || "-")}
        ${dataRow("场地金额", refund.place_money || "-")}
        ${dataRow("预计退款", refund.refund_money || "-")}
        ${dataRow("退款比例", rule.refund_percentage === undefined ? "-" : `${rule.refund_percentage}%`)}
      </div>
    </div>
    <div class="confirm-zone">
      <strong>二次确认</strong>
      <p>输入 CANCEL 后才能取消该预约。取消后会刷新预约和余额。</p>
      <input class="confirm-input" id="confirmText" type="text" autocomplete="off" inputmode="latin" placeholder="CANCEL" />
      <button class="button danger" id="confirmCancel" type="button">确认取消该预约</button>
    </div>
  `;
}

async function loadCancelPreview(billNum) {
  try {
    addUiLog(`读取退款预览 ${billNum}`);
    const preview = await api("/api/cancel/preview", {
      method: "POST",
      body: JSON.stringify({ bill_num: billNum, user_key: state.selectedUserKey }),
    });
    const booking = state.bookings.find((item) => item.bill_num === billNum);
    renderDetail(booking, preview);
  } catch (error) {
    const booking = state.bookings.find((item) => item.bill_num === billNum);
    renderDetail(booking, null, error.message);
    addUiLog(`退款预览失败: ${error.message}`, true);
  }
}

async function openCancelDialog(billNum) {
  const booking = state.bookings.find((item) => item.bill_num === billNum);
  if (!booking) {
    addUiLog(`退订失败: 未找到 bill ${billNum}`, true);
    return;
  }
  state.selectedBill = billNum;
  state.cancelDialogBill = billNum;
  state.cancelDialogPreview = null;
  state.cancelDialogError = "";
  state.cancelDialogLoading = true;
  renderBookings(state.bookings);
  renderDetail(booking);
  showCancelDialog();
  renderCancelDialog();
  try {
    addUiLog(`读取退款预览 ${billNum}`);
    state.cancelDialogPreview = await api("/api/cancel/preview", {
      method: "POST",
      body: JSON.stringify({ bill_num: billNum, user_key: state.selectedUserKey }),
    });
  } catch (error) {
    state.cancelDialogError = error.message;
    addUiLog(`退款预览失败: ${error.message}`, true);
  } finally {
    state.cancelDialogLoading = false;
    renderCancelDialog();
  }
}

function showCancelDialog() {
  els.cancelDialog.classList.remove("hidden");
  document.body.classList.add("modal-open");
}

function closeCancelDialog() {
  els.cancelDialog.classList.add("hidden");
  document.body.classList.remove("modal-open");
  state.cancelDialogBill = "";
  state.cancelDialogPreview = null;
  state.cancelDialogError = "";
  state.cancelDialogLoading = false;
}

function renderCancelDialog() {
  const billNum = state.cancelDialogBill;
  const booking = state.bookings.find((item) => item.bill_num === billNum);
  const preview = state.cancelDialogPreview || {};
  const refund = preview.refund || {};
  const rule = preview.rule || {};
  const loadingMarkup = state.cancelDialogLoading ? `<div class="refund-box">正在读取退款预览...</div>` : "";
  const errorMarkup = state.cancelDialogError ? `<div class="refund-box danger"><strong>退款预览不可用</strong><p>${escapeHtml(state.cancelDialogError)}</p></div>` : "";
  const previewMarkup = preview.refund
    ? `
      <div class="refund-box">
        <strong>退款预览</strong>
        <div class="refund-grid">
          ${dataRow("支付金额", refund.pay_money || "-")}
          ${dataRow("场地金额", refund.place_money || "-")}
          ${dataRow("预计退款", refund.refund_money || "-")}
          ${dataRow("退款比例", rule.refund_percentage === undefined ? "-" : `${rule.refund_percentage}%`)}
        </div>
      </div>
    `
    : "";

  els.cancelDialogBody.innerHTML = `
    <div class="warning-box">
      <strong>退订会立即提交到预约系统</strong>
      <p>${booking ? `${escapeHtml(booking.date)} ${escapeHtml(booking.time_range)} · ${escapeHtml(booking.court || "场地")}` : "未找到预约详情"}</p>
      <p>bill <span class="numeric">${escapeHtml(billNum)}</span></p>
    </div>
    ${loadingMarkup}
    ${errorMarkup}
    ${previewMarkup}
    <label class="confirm-field">
      <span>输入 CANCEL 确认退订</span>
      <input class="confirm-input" id="cancelDialogText" type="text" autocomplete="off" inputmode="latin" placeholder="CANCEL" />
    </label>
  `;
  const input = document.querySelector("#cancelDialogText");
  const updateSubmit = () => {
    els.cancelDialogSubmit.disabled = input.value.trim() !== "CANCEL" || state.cancelDialogLoading;
  };
  input.addEventListener("input", updateSubmit);
  updateSubmit();
}

async function cancelBooking(billNum, confirmationValue = null) {
  const confirmation = confirmationValue === null ? document.querySelector("#confirmText")?.value.trim() || "" : confirmationValue;
  if (confirmation !== "CANCEL") {
    addUiLog("取消被阻止: 二次确认文本不匹配", true);
    return;
  }

  try {
    addUiLog(`开始取消 ${billNum}`, true);
    const result = await api("/api/cancel", {
      method: "POST",
      body: JSON.stringify({ bill_num: billNum, confirmation, reason: "天气原因", user_key: state.selectedUserKey }),
    });
    addUiLog(result.confirmed ? "取消已确认，余额已刷新" : "取消接口返回后仍未确认状态", true);
    renderCards(result.cards || [], result.primary_card || null);
    await loadBookings();
    const latest = state.bookings.find((item) => item.bill_num === billNum) || result.booking;
    renderDetail(latest || null);
    if (state.cancelDialogBill === billNum) {
      closeCancelDialog();
    }
  } catch (error) {
    addUiLog(`取消失败: ${error.message}`, true);
  }
}

async function startBooking(event) {
  event.preventDefault();
  const form = new FormData(els.bookingForm);
  const targetDate = form.get("date") || dateInputValue(4);
  const payload = {
    date: targetDate,
    time: form.get("time"),
    duration: form.get("duration"),
    priority: form.get("priority"),
    backup: form.get("backup"),
    booking_mode: form.get("booking_mode"),
    window_seconds: form.get("window_seconds"),
    poll_interval: form.get("poll_interval"),
    force: form.get("force") === "on",
    dry_run: form.get("dry_run") === "on",
    all_court: form.get("all_court") === "on",
    user_key: state.selectedUserKey,
  };

  try {
    const result = await api("/api/booking/start", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state.jobLineCounts[result.job.id] = 0;
    setChip(els.jobState, "运行中", "warning");
    renderViewModeDetails();
    addUiLog(`预约任务启动: ${result.job.command_label}`, true);
    await Promise.all([loadJob(), loadBookingHistory()]);
    startJobPolling();
  } catch (error) {
    addUiLog(`预约任务启动失败: ${error.message}`, true);
  }
}

async function loadBookingHistory() {
  const data = await api(userScopedPath(`/api/booking/history?${logWindowQuery()}`));
  state.bookingHistory = data.history || [];
  renderBookingHistory(state.bookingHistory);
  return data;
}

function renderBookingHistory(history) {
  if (!history.length) {
    els.bookingHistoryList.innerHTML = `<div class="empty-state compact">当前 ${escapeHtml(logWindowLabel())} 内没有历史预约。</div>`;
    return;
  }

  els.bookingHistoryList.innerHTML = history.slice(0, 5).map((item) => {
    const tone = historyResultTone(item.result);
    const summary = compactHistorySummary(item);
    return `
      <div class="history-row">
        <div class="history-main">
          <span class="history-title">${escapeHtml(shortDateLabel(item.target_date || ""))}</span>
          <span class="history-time">${escapeHtml(summary.time)}</span>
          <span class="history-court">${escapeHtml(summary.court)}</span>
        </div>
        <span class="chip ${tone}">${escapeHtml(item.result || "未知")}</span>
      </div>
    `;
  }).join("");
}

function compactHistorySummary(item) {
  const source = item.success_target || item.target_time || "";
  const timeMatch = String(source).match(/(\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2})/);
  const courtMatch = String(source).match(/(?:羽毛球|球|场地)\s*(\d{1,2})\b/);
  return {
    time: timeMatch ? timeMatch[1].replace(/\s+/g, "") : "-",
    court: item.result && !String(item.result).includes("成功") ? "失败" : courtMatch ? `球${courtMatch[1]}` : "-",
  };
}

function historyResultTone(result) {
  const text = String(result || "");
  if (text.includes("成功")) {
    return "success";
  }
  if (text.includes("运行") || text.includes("演练") || text.includes("未抢到")) {
    return "warning";
  }
  return text ? "danger" : "";
}

async function scanAvailability() {
  await loadAvailabilitySnapshot({ silent: false, preserveSelection: false });
}

async function loadAvailabilitySnapshot({ silent = false, preserveSelection = true } = {}) {
  if (state.availabilityLoading) {
    return;
  }
  state.availabilityLoading = true;
  if (!silent) {
    els.scanAvailability.disabled = true;
    els.scanAvailability.textContent = "查询中";
    els.availabilityList.innerHTML = `
      <div class="availability-day compact-day">
        <div class="availability-head">
          <strong>刷新中</strong>
          <span class="chip warning">5 天</span>
        </div>
        <div class="availability-hours">
          <span class="skeleton wide"></span>
        </div>
      </div>
    `;
  }

  try {
    const data = await api(userScopedPath("/api/availability?days=5"));
    state.availabilityDays = data.days || [];
    if (!preserveSelection) {
      state.selectedAvailabilitySlots = [];
    }
    renderAvailability(data.days || []);
    renderAvailabilityTools();
    state.refreshStamps.availability = Date.now();
    if (!silent) {
      addUiLog("可约分布查询完成");
    }
    return data;
  } catch (error) {
    if (!silent) {
      els.availabilityList.innerHTML = `<div class="empty-state compact">可约分布查询失败：${escapeHtml(error.message)}</div>`;
      addUiLog(`可约分布查询失败: ${error.message}`, true);
    }
    throw error;
  } finally {
    state.availabilityLoading = false;
    els.scanAvailability.disabled = false;
    els.scanAvailability.textContent = "查询 5 天分布";
  }
}

function renderAvailability(days) {
  if (!days.length) {
    els.availabilityList.innerHTML = `<div class="empty-state compact">没有返回可约分布</div>`;
    renderAvailabilityTools();
    return;
  }

  els.availabilityList.innerHTML = days.map((day) => {
    if (day.error) {
      return `
        <div class="availability-day compact-day">
          <div class="availability-head">
            <strong>${escapeHtml(shortDateLabel(day.date))}</strong>
            <span class="chip danger">失败</span>
          </div>
          <p class="availability-error">${escapeHtml(day.error)}</p>
        </div>
      `;
    }
    const hours = day.hours || [];
    const expanded = state.availabilityExpandedDates.has(day.date);
    const visibleHours = expanded ? hours : hours.slice(0, 2);
    const hourMarkup = hours.length
      ? visibleHours.map((hour) => renderAvailabilityHour(day, hour)).join("")
      : `<div class="availability-empty">无可约</div>`;
    const total = Number(day.total || 0);
    const moreMarkup = hours.length > 2
      ? `<button class="day-more" type="button" data-availability-more="${escapeAttr(day.date)}">${expanded ? "收起" : `更多 ${hours.length - 2}`}</button>`
      : "";
    return `
      <div class="availability-day compact-day" data-date="${escapeAttr(day.date)}">
        <div class="availability-head">
          <strong>${escapeHtml(shortDateLabel(day.date))}</strong>
          <span class="chip ${total > 0 ? "success" : ""}">${escapeHtml(total)}组</span>
        </div>
        <div class="availability-hours">${hourMarkup}</div>
        ${moreMarkup}
      </div>
    `;
  }).join("");

  els.availabilityList.querySelectorAll("[data-availability-slot]").forEach((button) => {
    button.addEventListener("click", () => toggleAvailabilitySlot(button));
  });
  els.availabilityList.querySelectorAll("[data-availability-more]").forEach((button) => {
    button.addEventListener("click", () => {
      const dateValue = button.dataset.availabilityMore;
      if (state.availabilityExpandedDates.has(dateValue)) {
        state.availabilityExpandedDates.delete(dateValue);
      } else {
        state.availabilityExpandedDates.add(dateValue);
      }
      renderAvailability(state.availabilityDays);
    });
  });
}

function renderAvailabilityHour(day, hour) {
  const courts = (hour.courts || []).map((court) => `
    <button
      class="court-chip selectable ${court.wall ? "wall" : ""} ${isAvailabilitySlotSelected(day.date, hour, court) ? "selected" : ""}"
      type="button"
      data-availability-slot="1"
      data-date="${escapeAttr(day.date)}"
      data-time="${escapeAttr(hour.time)}"
      data-court-id="${escapeAttr(court.id)}"
      aria-pressed="${isAvailabilitySlotSelected(day.date, hour, court) ? "true" : "false"}"
      title="${court.wall ? "靠墙场地" : "普通场地"}"
    >
      <span>${escapeHtml(courtShortLabel(court.name))}</span>
    </button>
  `).join("");
  return `
    <div class="availability-hour">
      <span class="availability-time">${escapeHtml(hour.time)}</span>
      <span class="availability-courts">${courts}</span>
    </div>
  `;
}

function shortDateLabel(value) {
  const parts = String(value || "").split("-");
  return parts.length === 3 ? `${parts[1]}/${parts[2]}` : String(value || "-");
}

function toggleAvailabilitySlot(button) {
  const slot = availabilitySlotFromDataset(button.dataset);
  if (!slot) {
    return;
  }
  const selectedKey = exactSlotKey(slot);
  if (state.selectedAvailabilitySlots.some((item) => exactSlotKey(item) === selectedKey)) {
    state.selectedAvailabilitySlots = state.selectedAvailabilitySlots.filter((item) => exactSlotKey(item) !== selectedKey);
    renderAvailability(state.availabilityDays);
    renderAvailabilityTools();
    return;
  }

  let next = [...state.selectedAvailabilitySlots];
  if (next.length && next[0].date !== slot.date) {
    next = [];
  }
  const hourKey = exactHourKey(slot);
  const existingHourIndex = next.findIndex((item) => exactHourKey(item) === hourKey);
  if (existingHourIndex >= 0) {
    next.splice(existingHourIndex, 1, slot);
  } else {
    const distinctHours = new Set(next.map(exactHourKey));
    if (distinctHours.size >= 2) {
      showAvailabilityWarning("同一天最多选择两个时间段");
      return;
    }
    next.push(slot);
  }
  next.sort(sortExactSlots);
  if (!selectionWithinBalance(next)) {
    showAvailabilityWarning("已选时间段总实扣超过卡余额");
    return;
  }
  state.selectedAvailabilitySlots = next;
  renderAvailability(state.availabilityDays);
  renderAvailabilityTools();
}

function renderAvailabilityTools() {
  renderAvailabilityCombos();
  renderExactSelection();
  renderViewModeDetails();
}

function availabilityHasSelectableSlots() {
  return (state.availabilityDays || []).some((day) => (
    (day.hours || []).some((hour) => (hour.courts || []).length > 0)
  ));
}

function renderAvailabilityCombos() {
  if (!els.availabilityCombos) {
    return;
  }
  const combos = buildAvailabilityCombos(state.availabilityDays);
  if (!combos.length) {
    els.availabilityCombos.innerHTML = "";
    return;
  }
  els.availabilityCombos.innerHTML = `
    <div class="availability-tool-head">
      <strong>自动组合</strong>
      <span class="booking-meta">连续两小时推荐</span>
    </div>
    <div class="combo-list">
      ${combos.map((combo, index) => renderAvailabilityCombo(combo, index)).join("")}
    </div>
  `;
  els.availabilityCombos.querySelectorAll("[data-combo-index]").forEach((button) => {
    button.addEventListener("click", () => selectAvailabilityCombo(combos[Number(button.dataset.comboIndex)]));
  });
}

function renderAvailabilityCombo(combo, index) {
  const total = exactSlotsTotal(combo.slots);
  const disabledClass = selectionWithinBalance(combo.slots) ? "" : " over-limit";
  const courtText = combo.slots.map((slot) => `${slot.name}`).join(" + ");
  return `
    <button class="combo-option${disabledClass}" type="button" data-combo-index="${index}">
      <span>
        <strong>${escapeHtml(combo.date)} ${escapeHtml(combo.time)}</strong>
        <span class="booking-meta">${escapeHtml(courtText)}</span>
      </span>
      <span class="numeric">实扣 ${formatMoney(total)}</span>
    </button>
  `;
}

function selectAvailabilityCombo(combo) {
  if (!combo || !combo.slots) {
    return;
  }
  const slots = combo.slots.slice().sort(sortExactSlots);
  if (!selectionWithinBalance(slots)) {
    showAvailabilityWarning("自动组合总实扣超过卡余额");
    return;
  }
  state.selectedAvailabilitySlots = slots;
  renderAvailability(state.availabilityDays);
  renderAvailabilityTools();
}

function renderExactSelection() {
  if (!els.availabilitySelection) {
    return;
  }
  const slots = state.selectedAvailabilitySlots;
  els.availabilitySelection.hidden = !slots.length;
  if (els.availabilitySelection.hidden) {
    return;
  }
  const total = exactSlotsTotal(slots);
  const balance = currentBalanceValue();
  els.availabilitySelection.classList.toggle("has-selection", slots.length > 0);
  els.exactSelectionList.innerHTML = slots.length
    ? slots.map((slot) => `
      <div class="exact-slot-row">
        <span>
          <strong>${escapeHtml(slot.time)}</strong>
          <span class="booking-meta">${escapeHtml(slot.date)} · ${escapeHtml(slot.name)}</span>
        </span>
        <span class="numeric">实扣 ${formatMoney(slot.pay_value)}</span>
      </div>
    `).join("")
    : `<div class="empty-state compact">从可约分布里选择 1 到 2 个时间段。</div>`;
  els.exactSelectionTotal.textContent = `合计 ${formatMoney(total)}`;
  els.exactSelectionBalance.textContent = `余额 ${formatMoney(balance)}`;
  els.exactSubmit.disabled = !slots.length || state.exactBookingLoading;
  els.exactSubmit.textContent = state.exactBookingLoading ? "提交中" : "提交预约";
}

function availabilitySlotFromDataset(dataset) {
  const day = state.availabilityDays.find((item) => item.date === dataset.date);
  const hour = day?.hours?.find((item) => item.time === dataset.time);
  const court = hour?.courts?.find((item) => item.id === dataset.courtId);
  if (!day || !hour || !court) {
    return null;
  }
  return exactSlotFromParts(day, hour, court);
}

function exactSlotFromParts(day, hour, court) {
  const startTime = court.start_time || hour.start_time || String(hour.time || "").split("-")[0];
  const endTime = court.end_time || hour.end_time || String(hour.time || "").split("-")[1];
  return {
    date: day.date,
    label: day.label,
    time: hour.time,
    start_time: startTime,
    end_time: endTime,
    id: court.id,
    name: court.name,
    number: court.number,
    wall: Boolean(court.wall),
    price_value: Number(court.price_value || 0),
    pay_value: Number(court.pay_value || 0),
  };
}

function buildAvailabilityCombos(days) {
  const combos = [];
  (days || []).forEach((day) => {
    const hours = (day.hours || []).slice().sort((a, b) => String(a.start_time || a.time).localeCompare(String(b.start_time || b.time)));
    for (let index = 0; index < hours.length - 1; index += 1) {
      const current = hours[index];
      const next = hours[index + 1];
      if (!current.end_time || !next.start_time || current.end_time !== next.start_time) {
        continue;
      }
      const firstCourt = bestComboCourt(current.courts || [], next.courts || null);
      const secondCourt = bestComboCourt(next.courts || [], [firstCourt].filter(Boolean));
      if (!firstCourt || !secondCourt) {
        continue;
      }
      const slots = [
        exactSlotFromParts(day, current, firstCourt),
        exactSlotFromParts(day, next, secondCourt),
      ];
      combos.push({ date: day.date, time: `${current.start_time}-${next.end_time}`, slots });
    }
  });
  return combos;
}

function bestComboCourt(courts, preferredMatches) {
  const list = (courts || []).slice();
  if (preferredMatches?.length) {
    const match = list.find((court) => preferredMatches.some((item) => item.id === court.id));
    if (match) {
      return match;
    }
  }
  list.sort((a, b) => courtScore(a) - courtScore(b));
  return list[0] || null;
}

function courtScore(court) {
  const number = Number(court.number);
  const priorityIndex = state.priorityCourts.indexOf(number);
  if (priorityIndex >= 0) {
    return priorityIndex;
  }
  const safeIndex = SAFE_COURTS.indexOf(number);
  if (safeIndex >= 0) {
    return 100 + safeIndex;
  }
  const allIndex = ALL_COURTS.indexOf(number);
  return allIndex >= 0 ? 200 + allIndex : 999;
}

function isAvailabilitySlotSelected(dateValue, hour, court) {
  const startTime = court.start_time || hour.start_time || String(hour.time || "").split("-")[0];
  const endTime = court.end_time || hour.end_time || String(hour.time || "").split("-")[1];
  return state.selectedAvailabilitySlots.some((slot) => (
    slot.date === dateValue
    && slot.start_time === startTime
    && slot.end_time === endTime
    && slot.id === court.id
  ));
}

function exactSlotKey(slot) {
  return `${slot.date}|${slot.start_time}|${slot.end_time}|${slot.id}`;
}

function exactHourKey(slot) {
  return `${slot.date}|${slot.start_time}`;
}

function sortExactSlots(a, b) {
  return `${a.date} ${a.start_time} ${a.id}`.localeCompare(`${b.date} ${b.start_time} ${b.id}`);
}

function exactSlotsTotal(slots) {
  return roundMoney((slots || []).reduce((sum, slot) => sum + Number(slot.pay_value || 0), 0));
}

function currentBalanceValue() {
  return Number(state.primaryCard?.cash_balance_value || 0);
}

function selectionWithinBalance(slots) {
  return exactSlotsTotal(slots) <= currentBalanceValue();
}

function formatMoney(value) {
  return Number(value || 0).toFixed(2);
}

function roundMoney(value) {
  return Math.round(Number(value || 0) * 100) / 100;
}

function showAvailabilityWarning(message) {
  if (!els.availabilityWarning) {
    return;
  }
  els.availabilityWarning.textContent = message;
  els.availabilityWarning.hidden = false;
  if (state.availabilityWarningTimer) {
    window.clearTimeout(state.availabilityWarningTimer);
  }
  state.availabilityWarningTimer = window.setTimeout(() => {
    els.availabilityWarning.hidden = true;
    state.availabilityWarningTimer = null;
  }, 3000);
}

async function submitExactBooking() {
  if (state.exactBookingLoading || !state.selectedAvailabilitySlots.length) {
    return;
  }
  if (!selectionWithinBalance(state.selectedAvailabilitySlots)) {
    showAvailabilityWarning("已选时间段总实扣超过卡余额");
    return;
  }
  state.exactBookingLoading = true;
  renderExactSelection();
  const payload = {
    user_key: state.selectedUserKey,
    dry_run: els.bookingForm.elements.dry_run.checked,
    slots: state.selectedAvailabilitySlots.map((slot) => ({
      date: slot.date,
      start_time: slot.start_time,
      end_time: slot.end_time,
      id: slot.id,
      name: slot.name,
      price_value: slot.price_value,
    })),
  };
  try {
    const result = await api("/api/booking/exact", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const successCount = result.successes?.length || 0;
    const failureCount = result.failures?.length || 0;
    addUiLog(`精确预约${result.result_label}: 成功 ${successCount}，失败 ${failureCount}`, true);
    if (failureCount) {
      result.failures.forEach((item) => addUiLog(`精确预约失败: ${item.error}`, true));
    }
    state.selectedAvailabilitySlots = [];
    await Promise.all([loadBookings(), loadCards(), loadBookingHistory()]);
    renderAvailability(state.availabilityDays);
    renderAvailabilityTools();
  } catch (error) {
    showAvailabilityWarning(error.message);
    addUiLog(`精确预约失败: ${error.message}`, true);
  } finally {
    state.exactBookingLoading = false;
    renderExactSelection();
  }
}

async function loadScanTasks() {
  const data = await api(userScopedPath(`/api/scan/tasks?${logWindowQuery()}`));
  state.scanTasks = data.tasks || [];
  state.scanEvents = data.events || [];
  renderScanTasks();
  renderScanEvents();
  renderViewModeDetails();
  return data;
}

function renderScanTasks() {
  const scanTasks = state.scanTasks || [];
  const visibleTasks = visibleScanTasks(scanTasks);
  if (!visibleTasks.length) {
    const emptyMessage = scanTasks.length ? "当前没有扫描中的任务。" : "还没有扫描任务。";
    els.scanTaskList.innerHTML = `<div class="empty-state">${escapeHtml(emptyMessage)}</div>`;
    return;
  }
  els.scanTaskList.innerHTML = visibleTasks.map((task) => {
    const tone = scanTaskTone(task.status);
    const targets = task.targets || [];
    const done = targets.filter((target) => target.status === "booked").length;
    const nextScan = task.next_scan_at || "-";
    return `
      <div class="scan-task-row">
        <div class="scan-task-main">
          <span>
            <strong>${escapeHtml(task.name || task.id)}</strong>
            <span class="chip ${tone}">${escapeHtml(scanTaskStatusLabel(task.status))}</span>
          </span>
          <span class="booking-meta">${escapeHtml(done)}/${escapeHtml(targets.length)} 个目标 · 下次 ${escapeHtml(nextScan)}</span>
          <span class="booking-meta">${escapeHtml(scanTaskOptionsLabel(task))}</span>
          <div class="scan-target-summary">
            ${targets.map(renderScanTargetSummary).join("")}
          </div>
        </div>
        <div class="scan-task-actions">
          ${renderScanTaskActions(task)}
        </div>
      </div>
    `;
  }).join("");
  els.scanTaskList.querySelectorAll("[data-scan-copy]").forEach((button) => {
    button.addEventListener("click", () => copyScanTaskToForm(button.dataset.scanCopy));
  });
  els.scanTaskList.querySelectorAll("[data-scan-action]").forEach((button) => {
    button.addEventListener("click", () => updateScanTask(button.dataset.scanId, button.dataset.scanAction));
  });
}

function visibleScanTasks(tasks) {
  return (tasks || []).filter((task) => VISIBLE_SCAN_TASK_STATUSES.has(task.status));
}

function renderScanEvents() {
  if (!els.scanEventList) {
    return;
  }
  const events = compactScanEvents((state.scanEvents || []).filter((event) => event.important)).slice(0, 8);
  if (!events.length) {
    els.scanEventList.innerHTML = `
      <div class="availability-tool-head">
        <strong>最近重要决策</strong>
        ${renderLogWindowControl("扫描事件时间窗")}
      </div>
      <div class="empty-state compact">当前 ${escapeHtml(logWindowLabel())} 内暂无重要决策。</div>
    `;
    return;
  }
  els.scanEventList.innerHTML = `
    <div class="availability-tool-head">
      <strong>最近重要决策</strong>
      ${renderLogWindowControl("扫描事件时间窗")}
    </div>
    <span class="booking-meta">预约、取消、重约、完成、过期</span>
    ${events.map((event) => `
      <div class="scan-event-row">
        ${event.folded_count ? `<span class="scan-event-badge">+${escapeHtml(event.folded_count)}</span>` : ""}
        <span>
          <strong>${escapeHtml(event.title || event.type)}</strong>
          <span class="booking-meta">${escapeHtml(event.task_name || "系统")} · ${escapeHtml(event.created_at || "-")}</span>
        </span>
        <span class="scan-event-message">${escapeHtml(event.message || "")}</span>
      </div>
    `).join("")}
  `;
}

function compactScanEvents(events) {
  const groups = new Map();
  events.forEach((event) => {
    const key = scanEventGroupKey(event);
    const group = groups.get(key);
    if (group) {
      group.folded_count += 1;
      return;
    }
    groups.set(key, { ...event, folded_count: 0 });
  });
  return Array.from(groups.values());
}

function scanEventGroupKey(event) {
  const type = event.type || "";
  const title = event.title || "";
  if (event.task_id) {
    return `task:${event.task_id}:${type}:${title}`;
  }
  return `system:${type}:${title}:${normalizeScanEventMessage(event.message || "")}`;
}

function normalizeScanEventMessage(message) {
  return String(message)
    .replace(/0x[0-9a-f]+/gi, "0x")
    .replace(/\s+/g, " ")
    .trim();
}

function renderScanTargetSummary(target) {
  const slots = target.booked_slots || [];
  const booked = slots.length ? ` · ${slots.map((slot) => slot.name || slot.id).join(" + ")}` : "";
  return `
    <span class="scan-target-chip">
      <span class="numeric">${escapeHtml(target.date)} ${escapeHtml(target.start_time)}-${escapeHtml(target.end_time)}</span>
      <span>${escapeHtml(scanTargetStatusLabel(target.status))}${escapeHtml(booked)}</span>
    </span>
  `;
}

function renderScanTaskActions(task) {
  const copyButton = `<button class="button secondary compact" type="button" data-scan-copy="${escapeAttr(task.id)}">复制</button>`;
  if (task.status === "active") {
    return `
      ${copyButton}
      <button class="button secondary compact" type="button" data-scan-id="${escapeAttr(task.id)}" data-scan-action="pause">暂停</button>
      <button class="button secondary compact" type="button" data-scan-id="${escapeAttr(task.id)}" data-scan-action="stop">停止</button>
    `;
  }
  if (task.status === "paused") {
    return `
      ${copyButton}
      <button class="button secondary compact" type="button" data-scan-id="${escapeAttr(task.id)}" data-scan-action="resume">恢复</button>
      <button class="button secondary compact" type="button" data-scan-id="${escapeAttr(task.id)}" data-scan-action="stop">停止</button>
    `;
  }
  return copyButton;
}

function scanTaskOptionsLabel(task) {
  const mode = task.success_mode === "all" ? "全部目标成功" : "任一目标成功";
  const courts = task.court_mode === "all" ? "全部场地" : `部分场地 ${task.selected_courts?.join(" ") || ""}`;
  const same = task.same_court_required ? "同场地" : "可跨场地";
  const optimize = task.iterative_optimization ? "自动优化" : "不优化";
  return `${mode} · ${courts} · ${same} · ${optimize}`;
}

function scanTaskTone(status) {
  if (status === "completed") {
    return "success";
  }
  if (status === "active" || status === "paused") {
    return "warning";
  }
  if (status === "expired" || status === "stopped") {
    return "";
  }
  return "danger";
}

function scanTaskStatusLabel(status) {
  return {
    active: "扫描中",
    paused: "已暂停",
    stopped: "已停止",
    completed: "已完成",
    expired: "已退出",
  }[status] || "异常";
}

function scanTargetStatusLabel(status) {
  return {
    pending: "等待扫描",
    booked: "已预约",
    partial: "部分成功",
    failed: "失败",
    expired: "已过期",
  }[status] || "等待扫描";
}

function hourOptions(selectedValue, { start = MIN_HOUR, end = MAX_HOUR } = {}) {
  const selected = normalizeHourValue(selectedValue);
  const options = [];
  for (let hour = start; hour <= end; hour += 1) {
    const value = `${String(hour).padStart(2, "0")}:00`;
    options.push(`<option value="${value}" ${value === selected ? "selected" : ""}>${String(hour).padStart(2, "0")}:00</option>`);
  }
  return options.join("");
}

function normalizeHourValue(value) {
  const text = String(value || "").trim();
  const match = text.match(/^(\d{1,2})/);
  if (!match) {
    return "";
  }
  return `${String(Math.min(MAX_HOUR, Math.max(MIN_HOUR, Number(match[1])))).padStart(2, "0")}:00`;
}

function addScanTargetRow(values = {}) {
  state.scanTargetCounter += 1;
  const id = `scanTarget${state.scanTargetCounter}`;
  const row = document.createElement("div");
  row.className = "scan-target-row";
  row.dataset.scanTargetRow = "1";
  row.innerHTML = `
    <label>
      <span>日期</span>
      <input name="target_date" type="date" value="${escapeAttr(values.date || dateInputValue(5))}" required />
    </label>
    <label>
      <span>开始</span>
      <select name="target_start" required>${hourOptions(values.start_time || "18:00", { end: MAX_HOUR - 1 })}</select>
    </label>
    <label>
      <span>结束</span>
      <select name="target_end" required>${hourOptions(values.end_time || "20:00", { start: MIN_HOUR + 1 })}</select>
    </label>
    <button class="button secondary compact" type="button" aria-label="删除目标" data-remove-target="${escapeAttr(id)}">删除</button>
  `;
  els.scanTargetList.append(row);
  row.addEventListener("input", renderViewModeDetails);
  row.addEventListener("change", () => {
    normalizeScanTargetRow(row);
    renderViewModeDetails();
  });
  row.querySelector("[data-remove-target]").addEventListener("click", () => {
    if (els.scanTargetList.querySelectorAll("[data-scan-target-row]").length <= 1) {
      addUiLog("至少保留一个扫描目标", true);
      return;
    }
    row.remove();
    renderViewModeDetails();
  });
  renderViewModeDetails();
}

function normalizeScanTargetRow(row) {
  const start = row.querySelector('[name="target_start"]');
  const end = row.querySelector('[name="target_end"]');
  if (!start || !end || end.value > start.value) {
    return;
  }
  const nextHour = Math.min(MAX_HOUR, Number(start.value.slice(0, 2)) + 2);
  end.value = `${String(nextHour).padStart(2, "0")}:00`;
  if (end.value <= start.value) {
    end.value = `${String(Math.min(MAX_HOUR, Number(start.value.slice(0, 2)) + 1)).padStart(2, "0")}:00`;
  }
}

function scanTargetsFromForm() {
  return Array.from(els.scanTargetList.querySelectorAll("[data-scan-target-row]")).map((row) => ({
    date: row.querySelector('input[name="target_date"]').value,
    start_time: row.querySelector('[name="target_start"]').value,
    end_time: row.querySelector('[name="target_end"]').value,
  }));
}

function copyScanTaskToForm(id) {
  const task = (state.scanTasks || []).find((item) => item.id === id);
  if (!task) {
    addUiLog("未找到扫描任务参数", true);
    return;
  }
  const form = els.scanTaskForm;
  form.elements.name.value = task.name || "";
  form.elements.scan_interval_minutes.value = task.scan_interval_minutes || "30";
  form.elements.success_mode.value = task.success_mode || "any";
  form.elements.court_mode.value = task.court_mode || "selected";
  form.elements.selected_courts.value = Array.isArray(task.selected_courts) ? task.selected_courts.join(" ") : "";
  form.elements.same_court_required.checked = Boolean(task.same_court_required);
  form.elements.iterative_optimization.checked = Boolean(task.iterative_optimization);
  els.scanTargetList.innerHTML = "";
  const targets = Array.isArray(task.targets) && task.targets.length ? task.targets : [{}];
  targets.forEach((target) => addScanTargetRow({
    date: target.date,
    start_time: target.start_time,
    end_time: target.end_time,
  }));
  renderViewModeDetails();
  form.scrollIntoView({ behavior: "smooth", block: "start" });
  addUiLog("扫描任务参数已复制到扫描预约", true);
}

async function createScanTask(event) {
  event.preventDefault();
  if (state.scanTaskLoading) {
    return;
  }
  const form = new FormData(els.scanTaskForm);
  const payload = {
    user_key: state.selectedUserKey,
    name: form.get("name"),
    targets: scanTargetsFromForm(),
    scan_interval_minutes: form.get("scan_interval_minutes"),
    success_mode: form.get("success_mode"),
    court_mode: form.get("court_mode"),
    selected_courts: form.get("selected_courts"),
    same_court_required: form.get("same_court_required") === "on",
    iterative_optimization: form.get("iterative_optimization") === "on",
  };
  state.scanTaskLoading = true;
  try {
    await api("/api/scan/tasks", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    addUiLog("扫描任务已发布", true);
    els.scanTaskForm.reset();
    els.scanTargetList.innerHTML = "";
    addScanTargetRow();
    await loadScanTasks();
  } catch (error) {
    addUiLog(`扫描任务发布失败: ${error.message}`, true);
  } finally {
    state.scanTaskLoading = false;
    renderViewModeDetails();
  }
}

async function updateScanTask(id, action) {
  try {
    await api("/api/scan/tasks/update", {
      method: "POST",
      body: JSON.stringify({ id, action }),
    });
    addUiLog(`扫描任务已${scanTaskActionText(action)}`, true);
    await loadScanTasks();
  } catch (error) {
    addUiLog(`扫描任务更新失败: ${error.message}`, true);
  }
}

function scanTaskActionText(action) {
  return { pause: "暂停", resume: "恢复", stop: "停止" }[action] || "更新";
}

async function unlockUserManagement(event) {
  event.preventDefault();
  const adminPassword = new FormData(els.userUnlockForm).get("admin_password") || "";
  try {
    await api("/api/users/unlock", {
      method: "POST",
      body: JSON.stringify({ admin_password: adminPassword }),
    });
    state.adminPassword = String(adminPassword);
    state.userManagementUnlocked = true;
    els.userUnlockForm.reset();
    resetUserForm();
    renderUserManagementLock();
    addUiLog("用户管理已解锁", true);
  } catch (error) {
    addUiLog(`用户管理解锁失败: ${error.message}`, true);
  }
}

function resetUserForm() {
  els.userForm.reset();
  els.userForm.elements.enabled.checked = true;
  setTokenHelperMessage("不会保存账号密码。");
}

function lockUserManagement() {
  state.adminPassword = "";
  state.userManagementUnlocked = false;
  resetUserForm();
  renderUserManagementLock();
  addUiLog("用户管理已锁定");
}

async function saveUser(event) {
  event.preventDefault();
  if (!state.userManagementUnlocked || !state.adminPassword) {
    addUiLog("用户保存失败: 请先输入管理密码", true);
    return;
  }
  const form = new FormData(els.userForm);
  const payload = {
    admin_password: state.adminPassword,
    key: form.get("key"),
    label: form.get("label"),
    token: form.get("token"),
    jsessionid: form.get("jsessionid"),
    card_name: form.get("card_name") || "学生球类卡",
    enabled: form.get("enabled") === "on",
  };
  try {
    const result = await api("/api/users", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state.users = result.users || [];
    state.selectedUserKey = result.user?.key || state.selectedUserKey;
    resetUserForm();
    renderUsers();
    addUiLog(`用户已保存: ${result.user.label}`, true);
    await refreshAll();
  } catch (error) {
    addUiLog(`用户保存失败: ${error.message}`, true);
  }
}

function setTokenHelperMessage(text, tone = "") {
  els.tokenHelperMessage.textContent = text;
  els.tokenHelperMessage.className = `form-note ${tone}`.trim();
}

async function copyTokenAuthUrl() {
  if (!state.userManagementUnlocked || !state.adminPassword) {
    setTokenHelperMessage("请先输入管理密码。", "danger-text");
    return;
  }
  try {
    const result = await api("/api/token/auth-url", {
      method: "POST",
      body: JSON.stringify({ admin_password: state.adminPassword }),
    });
    await copyText(result.auth_url);
    setTokenHelperMessage("授权链接已复制，在电脑微信内打开。", "success-text");
    addUiLog("Token 授权链接已复制");
  } catch (error) {
    setTokenHelperMessage(`复制失败: ${error.message}`, "danger-text");
  }
}

async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "readonly");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.append(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  if (!copied) {
    throw new Error("clipboard unavailable");
  }
}

async function exchangeUserToken() {
  if (!state.userManagementUnlocked || !state.adminPassword) {
    setTokenHelperMessage("请先输入管理密码。", "danger-text");
    return;
  }
  const form = new FormData(els.userForm);
  const payload = {
    admin_password: state.adminPassword,
    username: form.get("token_username"),
    password: form.get("token_password"),
    redirect_url: form.get("token_redirect_url"),
  };
  try {
    els.exchangeToken.disabled = true;
    setTokenHelperMessage("正在兑换并校验账号。", "");
    const result = await api("/api/token/exchange", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    els.userForm.elements.token.value = result.token || "";
    els.userForm.elements.token_password.value = "";
    setTokenHelperMessage("Token 已填入，保存用户即可。", "success-text");
    addUiLog("Token 已兑换并填入用户表单", true);
  } catch (error) {
    setTokenHelperMessage(`兑换失败: ${error.message}`, "danger-text");
    addUiLog(`Token 兑换失败: ${error.message}`, true);
  } finally {
    els.exchangeToken.disabled = false;
  }
}

async function changeUser() {
  state.selectedUserKey = els.userSelect.value;
  state.selectedBill = "";
  clearRefreshStamps();
  renderDetail(null);
  renderUsers();
  addUiLog(`切换用户: ${currentUser()?.label || state.selectedUserKey}`, true);
  await refreshAll();
}

async function stopJob() {
  try {
    const result = await api("/api/booking/stop", { method: "POST", body: "{}" });
    const stoppedCount = Number(result.stopped_count || 0);
    if (result.stopped) {
      addUiLog(stoppedCount > 1 ? `已发送停止 ${stoppedCount} 个任务的请求` : "已发送停止任务请求", true);
    } else {
      addUiLog(result.message === "no running job" ? "没有可停止的运行任务，正在刷新状态" : `停止失败: ${result.message || "未知状态"}`, true);
    }
    await loadJob();
    await loadBookingHistory();
  } catch (error) {
    addUiLog(`停止失败: ${error.message}`, true);
  }
}

function startJobPolling() {
  if (state.jobTimer) {
    window.clearInterval(state.jobTimer);
    state.jobTimer = null;
  }
  loadJob();
}

async function loadJob() {
  try {
    const snapshot = await api("/api/booking/job");
    const jobs = Array.isArray(snapshot.jobs) ? snapshot.jobs : snapshot.job ? [snapshot.job] : [];
    if (!jobs.length) {
      setChip(els.jobState, "空闲");
      state.jobLineCounts = {};
      renderViewModeDetails();
      return;
    }
    const activeJobs = jobs.filter((job) => job.status === "running" || job.status === "stopping");
    const job = snapshot.job || activeJobs[activeJobs.length - 1] || jobs[jobs.length - 1];
    const activeCount = Number(snapshot.active_count || activeJobs.length);
    setChip(
      els.jobState,
      activeCount > 0 ? `${activeCount} 个任务运行中` : job.status,
      activeCount > 0 ? "warning" : job.status === "completed" ? "success" : "danger",
    );
    jobs.forEach((item) => {
      const knownCount = state.jobLineCounts[item.id] || 0;
      const newLines = item.lines.slice(knownCount);
      newLines
        .map(summarizeJobLine)
        .filter(Boolean)
        .forEach((line) => addUiLog(`#${item.id} ${line}`));
      state.jobLineCounts[item.id] = item.lines.length;
    });
    if (jobs.length > 30) {
      const liveIds = new Set(jobs.map((item) => String(item.id)));
      Object.keys(state.jobLineCounts).forEach((id) => {
        if (!liveIds.has(id)) {
          delete state.jobLineCounts[id];
        }
      });
    }
    if (activeCount === 0 && state.jobTimer) {
      window.clearInterval(state.jobTimer);
      state.jobTimer = null;
      await loadBookings();
      await loadCards();
      await loadBookingHistory();
    }
    renderViewModeDetails();
  } catch (error) {
    addUiLog(`日志读取失败: ${error.message}`, true);
  }
}

function summarizeJobLine(line) {
  const text = String(line || "");
  if (text.includes("[dry-run]")) {
    return text.replace(/^.*\\|\\s*/, "");
  }
  if (text.includes("[汇总]")) {
    return text.replace(/^.*\\|\\s*/, "");
  }
  if (text.includes("[成功]")) {
    return text.replace(/^.*\\|\\s*/, "");
  }
  if (text.includes("[失败]")) {
    return text.replace(/^.*\\|\\s*/, "");
  }
  if (text.includes("[get_places] 成功")) {
    return "场地数据已刷新";
  }
  if (text.includes("预约脚本启动")) {
    return "预约任务已启动";
  }
  if (text.includes("[配置] 日期=")) {
    return text.replace(/^.*\\|\\s*\\[配置\\]\\s*/, "配置: ");
  }
  return "";
}

async function refreshAll() {
  try {
    await triggerRefresh({ includeUsers: true, force: true });
    addUiLog("刷新完成");
    renderViewModeDetails();
  } catch (error) {
    addUiLog(`刷新失败: ${error.message}`, true);
  }
}

function setViewMode(mode) {
  const normalized = ["default", "numbers", "behavior"].includes(mode) ? mode : "default";
  state.viewMode = normalized;
  document.body.dataset.viewMode = normalized;
  els.viewSwitcher?.querySelectorAll("[data-view-mode]").forEach((button) => {
    button.setAttribute("aria-pressed", button.dataset.viewMode === normalized ? "true" : "false");
  });
  renderViewModeDetails();
}

function setupViewModeControl() {
  if (!els.viewSwitcher) {
    return;
  }
  els.viewSwitcher.querySelectorAll("[data-view-mode]").forEach((button) => {
    button.addEventListener("click", () => setViewMode(button.dataset.viewMode));
  });
  setViewMode("default");
}

function renderViewModeDetails() {
  if (!els.viewModeDetails) {
    return;
  }
  if (state.viewMode === "default") {
    els.viewModeDetails.hidden = true;
    els.viewModeDetails.innerHTML = "";
    return;
  }

  els.viewModeDetails.hidden = false;
  const groups = buildViewModeDetailGroups();
  els.viewModeDetails.innerHTML = `
    <div class="view-detail-grid">
      ${groups.map((group) => viewDetailGroup(group.title, group.rows, group.behavior)).join("")}
    </div>
  `;
}

function buildViewModeDetailGroups() {
  const primaryCard = state.primaryCard || {};
  const cards = state.cards || [];
  const bookings = state.bookings || [];
  const refundableCount = bookings.filter((booking) => !booking.cancelled && !bookingRefundState(booking).expired).length;
  const bookingForm = els.bookingForm;
  const scanForm = els.scanTaskForm;
  const scanTargets = scanTargetsFromForm();
  const successSelect = scanForm.elements.success_mode;
  const modeValue = formValue(bookingForm, "booking_mode", "balanced");
  const windowSeconds = formValue(bookingForm, "window_seconds", "30");
  const pollInterval = formValue(bookingForm, "poll_interval", "0.05");
  const priority = formValue(bookingForm, "priority", "").trim();
  const backup = formValue(bookingForm, "backup", "").trim();
  const courtPool = [priority, backup].filter(Boolean).join(" + ") || "-";
  const logCount = visibleUiLogs().length;

  return [
    {
      title: "余额",
      rows: [
        ["卡数量", `${cards.length}`],
        ["主卡余额", primaryCard.cash_balance || "-"],
        ["主卡有效期", primaryCard.end_date || "-"],
      ],
      behavior: "选卡优先读取接口返回的主卡；后续精确预约和余额展示都以主卡余额作为提交前校验基准。",
    },
    {
      title: "活跃预约",
      rows: [
        ["活跃数", `${bookings.length}`],
        ["可退数", `${refundableCount}`],
        ["选中 bill", state.selectedBill || "-"],
      ],
      behavior: "点击活跃预约会打开右侧详情；未过期且未取消的记录才显示退订入口，退订仍需要二次确认文本。",
    },
    {
      title: "可约分布",
      rows: [
        ["查询范围", "5 天"],
        ["精确预约", `${state.selectedAvailabilitySlots.length}/2 个时间段`],
        ["提交校验", "提交前重新查询"],
      ],
      behavior: "分布查询覆盖今天到 4 天后；精确预约提交前会重新查询并按最新可约数据确认目标仍可提交。",
    },
    {
      title: "扫描预约",
      rows: [
        ["间隔范围", "5-1440 分钟"],
        ["当前间隔", `${formValue(scanForm, "scan_interval_minutes", "30")} 分钟`],
        ["默认间隔", "30 分钟"],
        ["目标数量", `${scanTargets.length}`],
        ["达成条件", successSelect.selectedOptions[0]?.textContent || "-"],
      ],
      behavior: "扫描任务按目标约束生成候选并排序；允许迭代优化时，发现更优目标后会走取消和重约流程。",
    },
    {
      title: "提交预约",
      rows: [
        ["booking_mode", modeValue],
        ["window_seconds", `${windowSeconds}s`],
        ["poll_interval", secondsToMsText(pollInterval)],
        ["step_sleep", "30ms"],
        ["guide_interval", modeValue === "guided-fast" ? "500ms" : "-"],
        ["guide_max_inflight", modeValue === "guided-fast" ? "4" : "-"],
        ["场地池", courtPool],
      ],
      behavior: "balanced 先查询再排序下单；direct-fast 跳过 get_places 连续直抢；guided-fast 用直抢 worker 和 collector 探测共同更新排序。",
    },
    {
      title: "日志",
      rows: [
        ["时间窗", logWindowLabel()],
        ["保留上限", "一周"],
        ["当前行数", `${logCount}`],
        ["任务状态", els.jobState.textContent || "空闲"],
      ],
      behavior: "任务输出被轮询读取后会压缩成页面日志；页面按当前时间窗展示，超过一周的日志会被裁剪。",
    },
  ];
}

function viewDetailGroup(title, rows, behavior) {
  const behaviorMarkup = state.viewMode === "behavior" ? `<p class="behavior-detail">${escapeHtml(behavior)}</p>` : "";
  return `
    <section class="view-detail-group">
      <h3>${escapeHtml(title)}</h3>
      <div class="view-detail-values">
        ${rows.map(([label, value]) => viewDetailRow(label, value)).join("")}
      </div>
      ${behaviorMarkup}
    </section>
  `;
}

function viewDetailRow(label, value) {
  return `
    <div class="view-detail-item">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `;
}

function formValue(form, name, fallback = "-") {
  const value = form?.elements?.[name]?.value;
  return value === undefined || value === "" ? fallback : String(value);
}

function secondsToMsText(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds)) {
    return "-";
  }
  return `${Math.round(seconds * 1000)}ms`;
}

function setupBookingDate() {
  const input = els.bookingForm.querySelector('input[name="date"]');
  input.min = dateInputValue(0);
  input.value = dateInputValue(4);
}

function setupTouchControls() {
  setupTimeControl();
  setupSegmentedControl(els.durationPicker, 'input[name="duration"]', "2");
  setupWindowSlider();
  setupPollControl();
  setupCourtPicker();
}

function setupTimeControl() {
  els.bookingForm.querySelectorAll(".time-control [data-action]").forEach((button) => {
    button.addEventListener("click", () => {
      const action = button.dataset.action;
      if (action === "start-down") {
        state.startHour = Math.max(MIN_HOUR, state.startHour - 1);
      } else if (action === "start-up") {
        state.startHour = Math.min(state.endHour - 1, state.startHour + 1);
      } else if (action === "end-down") {
        state.endHour = Math.max(state.startHour + 1, state.endHour - 1);
      } else if (action === "end-up") {
        state.endHour = Math.min(MAX_HOUR, state.endHour + 1);
      }
      renderTimeControl();
    });
  });
  renderTimeControl();
}

function renderTimeControl() {
  const start = `${String(state.startHour).padStart(2, "0")}:00`;
  const end = `${String(state.endHour).padStart(2, "0")}:00`;
  const durationHours = Math.max(1, Math.min(2, state.endHour - state.startHour));
  els.timeStartValue.textContent = start;
  els.timeEndValue.textContent = end;
  els.timeRangeValue.textContent = `${start}-${end}`;
  els.bookingForm.elements.time.value = `${state.startHour}-${state.endHour}`;
  els.bookingForm.elements.duration.value = String(durationHours);

  const disabled = {
    "start-down": state.startHour <= MIN_HOUR,
    "start-up": state.startHour >= state.endHour - 1,
    "end-down": state.endHour <= state.startHour + 1,
    "end-up": state.endHour >= MAX_HOUR,
  };
  els.bookingForm.querySelectorAll(".time-control [data-action]").forEach((button) => {
    button.disabled = Boolean(disabled[button.dataset.action]);
  });
  renderViewModeDetails();
}

function setupSegmentedControl(container, inputSelector, initialValue) {
  if (!container) {
    return;
  }
  const input = els.bookingForm.querySelector(inputSelector);
  if (!input) {
    return;
  }
  const selectValue = (value) => {
    input.value = value;
    container.querySelectorAll("button[data-value]").forEach((button) => {
      button.setAttribute("aria-pressed", button.dataset.value === value ? "true" : "false");
    });
    renderViewModeDetails();
  };
  container.querySelectorAll("button[data-value]").forEach((button) => {
    button.addEventListener("click", () => selectValue(button.dataset.value));
  });
  selectValue(initialValue);
}

function setupWindowSlider() {
  if (!els.windowSlider) {
    return;
  }
  const sync = () => {
    if (els.windowValue) {
      els.windowValue.textContent = `${els.windowSlider.min || "10"}秒`;
    }
    renderViewModeDetails();
  };
  els.windowSlider.addEventListener("input", sync);
  els.windowSlider.value = "30";
  sync();
}

function setupPollControl() {
  const pollInput = els.bookingForm.querySelector('input[name="poll_interval"]');
  const modeInput = els.bookingForm.querySelector('input[name="booking_mode"]');
  const selectButton = (selectedButton) => {
    pollInput.value = selectedButton.dataset.value;
    modeInput.value = selectedButton.dataset.mode || "balanced";
    els.pollPicker.querySelectorAll("button[data-value]").forEach((button) => {
      button.setAttribute("aria-pressed", button === selectedButton ? "true" : "false");
    });
    renderViewModeDetails();
  };
  els.pollPicker.querySelectorAll("button[data-value]").forEach((button) => {
    button.addEventListener("click", () => selectButton(button));
    if (button.getAttribute("aria-pressed") === "true") {
      selectButton(button);
    }
  });
}

function setupCourtPicker() {
  const allCourtInput = els.bookingForm.querySelector('input[name="all_court"]');
  allCourtInput.addEventListener("change", () => {
    if (!allCourtInput.checked) {
      state.priorityCourts = state.priorityCourts.filter((court) => !WALL_COURTS.includes(court));
    }
    renderCourtPicker();
  });
  renderCourtPicker();
}

function renderCourtPicker() {
  const allowWall = els.bookingForm.elements.all_court.checked;
  els.courtPicker.innerHTML = ALL_COURTS.map((court) => {
    const isWall = WALL_COURTS.includes(court);
    const disabled = isWall && !allowWall;
    const order = state.priorityCourts.indexOf(court) + 1;
    const selected = order > 0;
    return `
      <button class="court-option ${isWall ? "wall" : ""} ${selected ? "selected" : ""}" type="button" data-court="${court}" ${disabled ? "disabled" : ""} aria-pressed="${selected ? "true" : "false"}">
        ${court}${selected ? `<span class="court-order">${order}</span>` : ""}
      </button>
    `;
  }).join("");

  els.courtPicker.querySelectorAll(".court-option").forEach((button) => {
    button.addEventListener("click", () => {
      const court = Number(button.dataset.court);
      if (button.disabled || Number.isNaN(court)) {
        return;
      }
      if (state.priorityCourts.includes(court)) {
        state.priorityCourts = state.priorityCourts.filter((item) => item !== court);
      } else {
        state.priorityCourts.push(court);
      }
      renderCourtPicker();
    });
  });

  updateCourtInputs();
}

function updateCourtInputs() {
  const allowWall = els.bookingForm.elements.all_court.checked;
  const pool = allowWall ? ALL_COURTS : SAFE_COURTS;
  const priority = state.priorityCourts.filter((court) => pool.includes(court));
  const backup = pool.filter((court) => !priority.includes(court));
  els.bookingForm.elements.priority.value = priority.join(" ");
  els.bookingForm.elements.backup.value = backup.join(" ");
  els.priorityValue.textContent = priority.length ? priority.join(" ") : "默认";
  renderViewModeDetails();
}

function setupSessionHelp() {
  els.sessionHelpTrigger.addEventListener("click", () => {
    const expanded = els.sessionHelpTrigger.getAttribute("aria-expanded") === "true";
    els.sessionHelpTrigger.setAttribute("aria-expanded", expanded ? "false" : "true");
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".status-help")) {
      els.sessionHelpTrigger.setAttribute("aria-expanded", "false");
    }
  });
}

function bookingRefundState(booking) {
  const end = parseBookingEnd(booking);
  if (end && end.getTime() <= Date.now()) {
    return { label: "过期不可退", tone: "warning", expired: true };
  }
  return { label: "可退", tone: "success", expired: false };
}

function parseBookingEnd(booking) {
  const date = String(booking.date || "").trim();
  const range = String(booking.time_range || "").trim();
  const match = range.match(/-(\d{1,2}):(\d{2})/);
  if (!date || !match) {
    return null;
  }
  const hour = match[1].padStart(2, "0");
  const minute = match[2];
  const parsed = new Date(`${date}T${hour}:${minute}:00`);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function dataRow(label, value) {
  return `<div class="data-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("`", "&#096;");
}

els.authForm.addEventListener("submit", login);
els.userSelect.addEventListener("change", () => changeUser().catch((error) => addUiLog(`用户切换失败: ${error.message}`, true)));
els.userUnlockForm.addEventListener("submit", unlockUserManagement);
els.lockUserPanel.addEventListener("click", lockUserManagement);
els.userForm.addEventListener("submit", saveUser);
els.copyTokenAuthUrl.addEventListener("click", copyTokenAuthUrl);
els.exchangeToken.addEventListener("click", exchangeUserToken);
els.refreshAll.addEventListener("click", refreshAll);
els.refreshCards.addEventListener("click", () => loadCards().catch((error) => addUiLog(`余额刷新失败: ${error.message}`, true)));
els.refreshBookings.addEventListener("click", () => loadBookings().catch((error) => addUiLog(`活跃预约刷新失败: ${error.message}`, true)));
els.scanAvailability.addEventListener("click", () => scanAvailability().catch((error) => addUiLog(`可约分布查询失败: ${error.message}`, true)));
els.exactSubmit.addEventListener("click", () => submitExactBooking());
els.refreshScanTasks.addEventListener("click", () => loadScanTasks().catch((error) => addUiLog(`扫描任务刷新失败: ${error.message}`, true)));
els.addScanTarget.addEventListener("click", () => addScanTargetRow());
els.scanTaskForm.addEventListener("submit", createScanTask);
els.scanTaskForm.addEventListener("input", renderViewModeDetails);
els.scanTaskForm.addEventListener("change", renderViewModeDetails);
els.refreshBookingHistory.addEventListener("click", () => loadBookingHistory().catch((error) => addUiLog(`历史预约刷新失败: ${error.message}`, true)));
els.bookingForm.addEventListener("submit", startBooking);
els.bookingForm.addEventListener("input", renderViewModeDetails);
els.bookingForm.addEventListener("change", renderViewModeDetails);
els.stopJob.addEventListener("click", stopJob);
els.closeCancelDialog.addEventListener("click", closeCancelDialog);
els.cancelDialogBack.addEventListener("click", closeCancelDialog);
els.cancelDialog.addEventListener("click", (event) => {
  if (event.target === els.cancelDialog) {
    closeCancelDialog();
  }
});
els.cancelDialogSubmit.addEventListener("click", () => {
  const confirmation = document.querySelector("#cancelDialogText")?.value.trim() || "";
  if (!state.cancelDialogBill) {
    return;
  }
  cancelBooking(state.cancelDialogBill, confirmation);
});
window.addEventListener("beforeunload", () => {
  clearAlignedRefresh();
  clearAccessExpiryTimer();
});

setupBookingDate();
setupSessionHelp();
setupViewModeControl();
setupLogWindowControls();
setupTouchControls();
renderUiLogs();
addScanTargetRow();
if (readStoredAccessKey()) {
  showApp().catch(() => expireAccessSession("访问失败，请重新输入密码。"));
} else {
  showLogin();
}
