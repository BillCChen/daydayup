const state = {
  bookings: [],
  bookingHistory: [],
  selectedBill: "",
  jobTimer: null,
  lastJobLineCount: 0,
  availabilityLoading: false,
  startHour: 17,
  endHour: 21,
  priorityCourts: [7, 8, 9, 6],
  users: [],
  selectedUserKey: "",
  userManagementUnlocked: false,
  adminPassword: "",
};

const SAFE_COURTS = [2, 3, 4, 6, 7, 8, 9, 10, 11];
const WALL_COURTS = [1, 5, 12];
const ALL_COURTS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];
const MIN_HOUR = 8;
const MAX_HOUR = 23;

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
  refreshAll: document.querySelector("#refreshAll"),
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
  refreshBookingHistory: document.querySelector("#refreshBookingHistory"),
  bookingHistoryList: document.querySelector("#bookingHistoryList"),
  bookingForm: document.querySelector("#bookingForm"),
  timeStartValue: document.querySelector("#timeStartValue"),
  timeEndValue: document.querySelector("#timeEndValue"),
  timeRangeValue: document.querySelector("#timeRangeValue"),
  courtPicker: document.querySelector("#courtPicker"),
  priorityValue: document.querySelector("#priorityValue"),
  durationPicker: document.querySelector("#durationPicker"),
  windowPicker: document.querySelector("#windowPicker"),
  pollPicker: document.querySelector("#pollPicker"),
  stopJob: document.querySelector("#stopJob"),
  jobState: document.querySelector("#jobState"),
  logStream: document.querySelector("#logStream"),
  detailEmpty: document.querySelector("#detailEmpty"),
  detailContent: document.querySelector("#detailContent"),
};

function fmtTime(ts = Date.now()) {
  return new Date(ts).toLocaleTimeString("zh-CN", { hour12: false });
}

function dateInputValue(offsetDays = 0) {
  const date = new Date();
  date.setDate(date.getDate() + offsetDays);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json" };
  const accessKey = localStorage.getItem("daydayupAccessKey");
  if (accessKey) {
    headers["X-Daydayup-Key"] = accessKey;
  }

  let response = await fetch(path, {
    headers,
    ...options,
  });
  if (response.status === 401) {
    localStorage.removeItem("daydayupAccessKey");
    showLogin();
  }
  const payload = await response.json();
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
    localStorage.setItem("daydayupAccessKey", password);
    els.authForm.reset();
    await showApp();
  } catch (error) {
    els.authMessage.textContent = `访问失败: ${error.message}`;
  }
}

function showLogin() {
  els.authScreen.classList.remove("hidden");
  els.appShell.classList.add("hidden");
}

async function showApp() {
  els.authScreen.classList.add("hidden");
  els.appShell.classList.remove("hidden");
  await loadUsers();
  await refreshAll();
}

function setPill(el, text, tone = "") {
  el.className = `status-pill ${tone}`.trim();
  el.textContent = text;
}

function setChip(el, text, tone = "") {
  el.className = `chip ${tone}`.trim();
  el.textContent = text;
}

function addUiLog(text, strong = false) {
  const line = document.createElement("div");
  line.className = strong ? "log-line strong" : "log-line";
  line.textContent = `[${fmtTime()}] ${text}`;
  els.logStream.prepend(line);
  while (els.logStream.children.length > 180) {
    els.logStream.lastElementChild.remove();
  }
}

async function loadUsers() {
  const data = await api("/api/users");
  state.users = data.users || [];
  if (!state.selectedUserKey || !state.users.some((user) => user.key === state.selectedUserKey)) {
    state.selectedUserKey = data.default_user_key || state.users[0]?.key || "";
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
  if (current) {
    els.userForm.elements.key.value = current.key;
    els.userForm.elements.label.value = current.label;
    els.userForm.elements.token.value = "";
    els.userForm.elements.jsessionid.value = "";
    els.userForm.elements.card_name.value = current.card_name || "学生球类卡";
    els.userForm.elements.enabled.checked = current.enabled !== false;
  }
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
  const hasSession = status.jsessionid.present;
  setPill(els.tokenState, status.token.present ? "Token ✅" : "Token ❌", status.token.present ? "ok" : "danger");
  setPill(
    els.sessionState,
    hasSession ? "Session ✅" : "Session ❌",
    hasSession ? "ok" : "warn",
  );
  els.sessionHelpWrap.classList.toggle("session-ok", hasSession);
  els.sessionHelpTrigger.hidden = hasSession;
  els.sessionHelp.hidden = hasSession;
  els.sessionHelpTrigger.setAttribute("aria-expanded", "false");
}

async function loadCards() {
  const data = await api(userScopedPath("/api/cards"));
  renderCards(data.cards, data.primary_card);
  els.lastRefresh.textContent = `余额 ${fmtTime()}`;
  els.lastRefresh.className = "status-pill ok";
  return data;
}

function renderCards(cards, primaryCard) {
  if (!primaryCard) {
    els.primaryCard.innerHTML = `<div class="empty-state">未查询到会员卡</div>`;
    els.otherCards.innerHTML = "";
    return;
  }

  els.primaryCard.innerHTML = `
    <div class="balance-value">${escapeHtml(primaryCard.cash_balance)}</div>
    <div><strong>${escapeHtml(primaryCard.card_name)}</strong> <span class="chip success">${escapeHtml(primaryCard.status)}</span></div>
    <div class="balance-meta">卡号 ${escapeHtml(primaryCard.card_index)} · 有效期 ${escapeHtml(primaryCard.end_date || "-")}</div>
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
}

async function loadBookings() {
  const data = await api(userScopedPath("/api/bookings?success=1&all=0"));
  state.bookings = data.bookings;
  renderBookings(data.bookings);
  els.lastRefresh.textContent = `活跃 ${fmtTime()}`;
  els.lastRefresh.className = "status-pill ok";
  return data;
}

function renderBookings(bookings) {
  if (!bookings.length) {
    els.bookingList.innerHTML = `<div class="empty-state">没有活跃预约</div>`;
    return;
  }

  els.bookingList.innerHTML = bookings.map((booking) => {
    const selected = booking.bill_num === state.selectedBill ? " selected" : "";
    const refundState = bookingRefundState(booking);
    return `
      <button class="booking-row${selected}" type="button" data-bill="${escapeAttr(booking.bill_num)}">
        <span class="booking-main">
          <span><span class="booking-time">${escapeHtml(booking.date)} ${escapeHtml(booking.time_range)}</span> · ${escapeHtml(booking.court || "场地")}</span>
          <span class="booking-meta">bill ${escapeHtml(booking.bill_num)} · ${escapeHtml(booking.pay_type || "-")} · ${escapeHtml(booking.created_at || "-")}</span>
        </span>
        <span class="chip ${refundState.tone}">${escapeHtml(refundState.label)}</span>
      </button>
    `;
  }).join("");

  els.bookingList.querySelectorAll(".booking-row").forEach((row) => {
    row.addEventListener("click", () => selectBooking(row.dataset.bill));
  });
}

function selectBooking(billNum) {
  state.selectedBill = billNum;
  renderBookings(state.bookings);
  const booking = state.bookings.find((item) => item.bill_num === billNum);
  renderDetail(booking);
  document.querySelector("#detailPane").scrollIntoView({ behavior: "smooth", block: "start" });
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

async function cancelBooking(billNum) {
  const confirmation = document.querySelector("#confirmText")?.value.trim() || "";
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
    state.lastJobLineCount = 0;
    setChip(els.jobState, "运行中", "warning");
    addUiLog(`预约任务启动: ${result.job.command_label}`, true);
    await loadBookingHistory();
    startJobPolling();
  } catch (error) {
    addUiLog(`预约任务启动失败: ${error.message}`, true);
  }
}

async function loadBookingHistory() {
  const data = await api(userScopedPath("/api/booking/history"));
  state.bookingHistory = data.history || [];
  renderBookingHistory(state.bookingHistory);
  return data;
}

function renderBookingHistory(history) {
  if (!history.length) {
    els.bookingHistoryList.innerHTML = `<div class="empty-state">还没有通过操作台发起过预约</div>`;
    return;
  }

  els.bookingHistoryList.innerHTML = history.map((item) => {
    const tone = historyResultTone(item.result);
    return `
      <div class="history-row">
        <div class="history-main">
          <span class="history-title">${escapeHtml(item.target_date || "-")} · ${escapeHtml(item.target_time || "-")}</span>
          <span class="booking-meta">${escapeHtml(item.user_label || "用户")} · 发起 ${escapeHtml(item.requested_at || "-")}</span>
          <span class="booking-meta">成功目标 ${escapeHtml(item.success_target || "-")}</span>
        </div>
        <span class="chip ${tone}">${escapeHtml(item.result || "未知")}</span>
      </div>
    `;
  }).join("");
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
  if (state.availabilityLoading) {
    return;
  }
  state.availabilityLoading = true;
  els.scanAvailability.disabled = true;
  els.scanAvailability.textContent = "查询中";
  els.availabilityList.innerHTML = `
    <div class="availability-day">
      <div class="availability-head">
        <strong>正在查询</strong>
        <span class="chip warning">今天到 4 天后</span>
      </div>
      <div class="availability-hours">
        <span class="skeleton wide"></span>
        <span class="skeleton"></span>
      </div>
    </div>
  `;

  try {
    const data = await api(userScopedPath("/api/availability?days=5"));
    renderAvailability(data.days || []);
    els.lastRefresh.textContent = `分布 ${fmtTime()}`;
    els.lastRefresh.className = "status-pill ok";
    addUiLog("可约分布查询完成");
  } catch (error) {
    els.availabilityList.innerHTML = `<div class="empty-state">可约分布查询失败：${escapeHtml(error.message)}</div>`;
    addUiLog(`可约分布查询失败: ${error.message}`, true);
  } finally {
    state.availabilityLoading = false;
    els.scanAvailability.disabled = false;
    els.scanAvailability.textContent = "查询 5 天分布";
  }
}

function renderAvailability(days) {
  if (!days.length) {
    els.availabilityList.innerHTML = `<div class="empty-state">没有返回可约分布</div>`;
    return;
  }

  els.availabilityList.innerHTML = days.map((day) => {
    if (day.error) {
      return `
        <div class="availability-day">
          <div class="availability-head">
            <strong>${escapeHtml(day.label)} ${escapeHtml(day.date)}</strong>
            <span class="chip danger">查询失败</span>
          </div>
          <p class="availability-error">${escapeHtml(day.error)}</p>
        </div>
      `;
    }
    const hours = day.hours || [];
    const hourMarkup = hours.length
      ? hours.map(renderAvailabilityHour).join("")
      : `<div class="availability-empty">没有可约场地</div>`;
    return `
      <div class="availability-day">
        <div class="availability-head">
          <strong>${escapeHtml(day.label)} ${escapeHtml(day.date)}</strong>
          <span class="chip ${day.total ? "success" : ""}">${escapeHtml(day.total)} 个可约时段</span>
        </div>
        <div class="availability-hours">${hourMarkup}</div>
      </div>
    `;
  }).join("");
}

function renderAvailabilityHour(hour) {
  const courts = (hour.courts || []).map((court) => `
    <span class="court-chip ${court.wall ? "wall" : ""}" title="${court.wall ? "靠墙场地" : "普通场地"}">
      ${escapeHtml(court.name)}${court.price ? ` · ${escapeHtml(court.price)}` : ""}
    </span>
  `).join("");
  return `
    <div class="availability-hour">
      <span class="availability-time">${escapeHtml(hour.time)}</span>
      <span class="availability-count">${escapeHtml(hour.count)} 场</span>
      <span class="availability-courts">${courts}</span>
    </div>
  `;
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
    renderUserManagementLock();
    addUiLog("用户管理已解锁", true);
  } catch (error) {
    addUiLog(`用户管理解锁失败: ${error.message}`, true);
  }
}

function lockUserManagement() {
  state.adminPassword = "";
  state.userManagementUnlocked = false;
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
    els.userForm.elements.token.value = "";
    els.userForm.elements.jsessionid.value = "";
    renderUsers();
    addUiLog(`用户已保存: ${result.user.label}`, true);
    await refreshAll();
  } catch (error) {
    addUiLog(`用户保存失败: ${error.message}`, true);
  }
}

async function changeUser() {
  state.selectedUserKey = els.userSelect.value;
  state.selectedBill = "";
  renderDetail(null);
  renderUsers();
  addUiLog(`切换用户: ${currentUser()?.label || state.selectedUserKey}`, true);
  await refreshAll();
}

async function stopJob() {
  try {
    await api("/api/booking/stop", { method: "POST", body: "{}" });
    addUiLog("已发送停止任务请求", true);
  } catch (error) {
    addUiLog(`停止失败: ${error.message}`, true);
  }
}

function startJobPolling() {
  if (state.jobTimer) {
    window.clearInterval(state.jobTimer);
  }
  state.jobTimer = window.setInterval(loadJob, 1200);
  loadJob();
}

async function loadJob() {
  try {
    const snapshot = await api("/api/booking/job");
    if (!snapshot.job) {
      setChip(els.jobState, "空闲");
      return;
    }
    const job = snapshot.job;
    setChip(
      els.jobState,
      job.status,
      job.status === "running" || job.status === "stopping" ? "warning" : job.status === "completed" ? "success" : "danger",
    );
    const newLines = job.lines.slice(state.lastJobLineCount);
    newLines.map(summarizeJobLine).filter(Boolean).forEach((line) => addUiLog(line));
    state.lastJobLineCount = job.lines.length;
    if (job.status !== "running" && job.status !== "stopping" && state.jobTimer) {
      window.clearInterval(state.jobTimer);
      state.jobTimer = null;
      await loadBookings();
      await loadCards();
      await loadBookingHistory();
    }
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
    await loadUsers();
    await Promise.all([loadStatus(), loadCards(), loadBookings(), loadBookingHistory(), loadJob()]);
    addUiLog("刷新完成");
  } catch (error) {
    addUiLog(`刷新失败: ${error.message}`, true);
  }
}

function setupBookingDate() {
  const input = els.bookingForm.querySelector('input[name="date"]');
  input.min = dateInputValue(0);
  input.value = dateInputValue(4);
}

function setupTouchControls() {
  setupTimeControl();
  setupSegmentedControl(els.durationPicker, 'input[name="duration"]', "2");
  setupSegmentedControl(els.windowPicker, 'input[name="window_seconds"]', "60");
  setupSegmentedControl(els.pollPicker, 'input[name="poll_interval"]', "0.08");
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
  els.timeStartValue.textContent = start;
  els.timeEndValue.textContent = end;
  els.timeRangeValue.textContent = `${start}-${end}`;
  els.bookingForm.elements.time.value = `${state.startHour}-${state.endHour}`;

  const disabled = {
    "start-down": state.startHour <= MIN_HOUR,
    "start-up": state.startHour >= state.endHour - 1,
    "end-down": state.endHour <= state.startHour + 1,
    "end-up": state.endHour >= MAX_HOUR,
  };
  els.bookingForm.querySelectorAll(".time-control [data-action]").forEach((button) => {
    button.disabled = Boolean(disabled[button.dataset.action]);
  });
}

function setupSegmentedControl(container, inputSelector, initialValue) {
  const input = els.bookingForm.querySelector(inputSelector);
  const selectValue = (value) => {
    input.value = value;
    container.querySelectorAll("button[data-value]").forEach((button) => {
      button.setAttribute("aria-pressed", button.dataset.value === value ? "true" : "false");
    });
  };
  container.querySelectorAll("button[data-value]").forEach((button) => {
    button.addEventListener("click", () => selectValue(button.dataset.value));
  });
  selectValue(initialValue);
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
els.refreshAll.addEventListener("click", refreshAll);
els.refreshCards.addEventListener("click", () => loadCards().catch((error) => addUiLog(`余额刷新失败: ${error.message}`, true)));
els.refreshBookings.addEventListener("click", () => loadBookings().catch((error) => addUiLog(`活跃预约刷新失败: ${error.message}`, true)));
els.scanAvailability.addEventListener("click", scanAvailability);
els.refreshBookingHistory.addEventListener("click", () => loadBookingHistory().catch((error) => addUiLog(`历史预约刷新失败: ${error.message}`, true)));
els.bookingForm.addEventListener("submit", startBooking);
els.stopJob.addEventListener("click", stopJob);

setupBookingDate();
setupSessionHelp();
setupTouchControls();
if (localStorage.getItem("daydayupAccessKey")) {
  showApp().catch(() => showLogin());
} else {
  showLogin();
}
