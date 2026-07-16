const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function makeClassList() {
  const values = new Set();
  return {
    add: (...items) => items.forEach((item) => values.add(item)),
    remove: (...items) => items.forEach((item) => values.delete(item)),
    toggle: (item, force) => {
      if (force === true) {
        values.add(item);
        return true;
      }
      if (force === false) {
        values.delete(item);
        return false;
      }
      if (values.has(item)) {
        values.delete(item);
        return false;
      }
      values.add(item);
      return true;
    },
    contains: (item) => values.has(item),
  };
}

function makeElement() {
  const attributes = new Map();
  return {
    className: "",
    classList: makeClassList(),
    dataset: {},
    disabled: false,
    elements: {},
    hidden: false,
    inert: false,
    innerHTML: "",
    textContent: "",
    title: "",
    value: "",
    addEventListener() {},
    append() {},
    appendChild() {},
    close() {},
    focus() {},
    getAttribute(name) { return attributes.get(name) ?? null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    remove() {},
    reset() {},
    select() {},
    setAttribute(name, value) { attributes.set(name, String(value)); },
    showModal() {},
  };
}

async function main() {
  const elements = new Map();
  const elementFor = (selector) => {
    if (!elements.has(selector)) {
      elements.set(selector, makeElement());
    }
    return elements.get(selector);
  };
  const storage = new Map([
    ["daydayupAccessKey", "test-access"],
    ["daydayupAccessExpiresAt", String(Date.now() + 60_000)],
  ]);
  const document = {
    body: makeElement(),
    createElement: () => makeElement(),
    execCommand: () => true,
    querySelector: elementFor,
    querySelectorAll: () => [],
  };
  const context = {
    console,
    document,
    FormData: class {},
    localStorage: {
      getItem: (key) => storage.get(key) ?? null,
      removeItem: (key) => storage.delete(key),
      setItem: (key, value) => storage.set(key, String(value)),
    },
    navigator: {},
    setTimeout,
    clearTimeout,
    URLSearchParams,
    window: {
      clearInterval,
      clearTimeout,
      setInterval,
      setTimeout,
    },
  };
  const appPath = path.join(__dirname, "..", "web", "app.js");
  const source = fs.readFileSync(appPath, "utf8").split("els.authForm.addEventListener", 1)[0];
  const testProgram = `
    (async () => {
      els.sessionState.classList.add("status-help-trigger");
      setPill(els.sessionState, "Session ready", "ok");
      if (!els.sessionState.classList.contains("status-help-trigger")) {
        throw new Error("session status refresh removed the help trigger class");
      }

      state.multiPoolMode = "live";
      state.users = [
        {
          key: "shared_1",
          label: "Shared 1",
          enabled: true,
          credential_conflict: true,
          credential_conflicts_with: ["shared_2"],
        },
        {
          key: "shared_2",
          label: "Shared 2",
          enabled: true,
          credential_conflict: true,
          credential_conflicts_with: ["shared_1"],
        },
      ];
      state.selectedUserKey = "shared_1";
      renderUsers();
      renderBookings([{
        bill_num: "synthetic-bill",
        date: "2099-07-20",
        time_range: "18:00-19:00",
        court: "Court 7",
      }]);
      if (!els.userSelect.innerHTML.includes("共享授权") || !els.activeUserLabel.textContent.includes("授权冲突")) {
        throw new Error("shared-credential account marker was not rendered");
      }
      if (!els.bookingList.innerHTML.includes("共享授权数据") || !els.bookingList.innerHTML.includes("无法按页面用户拆分")) {
        throw new Error("shared active-booking warning was not rendered");
      }
      if (!els.multiPoolEnabled.disabled || !els.multiPoolSecondaryUser.innerHTML.includes("共享授权，不可用")) {
        throw new Error("shared-credential secondary account remained available for multi-pool");
      }

      state.users = [
        { key: "old_user", label: "Old", enabled: true },
        { key: "new_user", label: "New", enabled: true },
      ];
      state.selectedUserKey = "old_user";
      let resolveOldCards;
      const oldCards = new Promise((resolve) => { resolveOldCards = resolve; });
      api = async (requestPath) => requestPath.includes("old_user")
        ? oldCards
        : {
          cards: [{ card_index: "new-card", cash_balance: "88.00" }],
          primary_card: { card_index: "new-card", cash_balance: "88.00" },
        };

      const staleRequest = loadCards();
      state.selectedUserKey = "new_user";
      state.cards = [];
      state.primaryCard = null;
      resolveOldCards({
        cards: [{ card_index: "old-card", cash_balance: "11.00" }],
        primary_card: { card_index: "old-card", cash_balance: "11.00" },
      });
      await staleRequest;
      if (state.cards.length !== 0 || state.primaryCard !== null) {
        throw new Error("stale card response replaced the selected user's cleared state");
      }

      await loadCards();
      if (state.primaryCard?.cash_balance !== "88.00") {
        throw new Error("current user card response did not render");
      }

      api = async () => ({ days: [] });
      state.selectedAvailabilitySlots = [{
        date: "2026-07-20",
        start_time: "18:00",
        end_time: "19:00",
        id: "ymq7",
      }];
      await loadAvailabilitySnapshot({ silent: true, preserveSelection: true });
      if (!els.availabilityRefreshState.textContent.includes("已更新")) {
        throw new Error("availability freshness timestamp was not rendered");
      }
      if (!els.availabilityRefreshState.className.includes("availability-refresh-state")) {
        throw new Error("availability freshness layout class was lost");
      }
      if (state.selectedAvailabilitySlots.length !== 0 || els.availabilityWarning.hidden) {
        throw new Error("expired availability selection was not removed and announced");
      }
      if (els.availabilitySection.getAttribute?.("aria-busy") === "true") {
        throw new Error("availability remained busy after refresh completion");
      }

      const currentSlot = {
        date: "2026-07-21",
        label: "Tuesday",
        time: "18:00-19:00",
        start_time: "18:00",
        end_time: "19:00",
        id: "ymq8",
        name: "Court 8",
      };
      state.selectedAvailabilitySlots = [currentSlot];
      api = async () => ({
        days: [{
          date: currentSlot.date,
          label: currentSlot.label,
          hours: [{
            time: currentSlot.time,
            start_time: currentSlot.start_time,
            end_time: currentSlot.end_time,
            courts: [{ id: currentSlot.id, name: currentSlot.name }],
          }],
        }],
      });
      await loadAvailabilitySnapshot({ silent: true, preserveSelection: true });
      if (state.selectedAvailabilitySlots.length !== 1) {
        throw new Error("current availability selection was not preserved");
      }

      let releaseFirstRefresh;
      let statusCalls = 0;
      const firstRefreshGate = new Promise((resolve) => { releaseFirstRefresh = resolve; });
      loadStatus = async () => {
        statusCalls += 1;
        if (statusCalls === 1) {
          await firstRefreshGate;
        }
      };
      loadCards = async () => {};
      loadBookings = async () => {};
      loadBookingHistory = async () => {};
      loadScanTasks = async () => {};
      loadJob = async () => {};
      refreshAvailabilityIfDue = async () => {};
      renderViewModeDetails = () => {};

      const initialRefresh = refreshLiveData();
      let queuedResolved = false;
      const queuedRefresh = refreshLiveData({ force: true }).then(() => { queuedResolved = true; });
      await Promise.resolve();
      if (queuedResolved) {
        throw new Error("queued refresh resolved before the active refresh drained");
      }
      releaseFirstRefresh();
      await Promise.all([initialRefresh, queuedRefresh]);
      if (statusCalls !== 2 || !queuedResolved) {
        throw new Error("queued refresh did not run to completion");
      }

      let releaseFailureRefresh;
      statusCalls = 0;
      const failureRefreshGate = new Promise((resolve) => { releaseFailureRefresh = resolve; });
      loadStatus = async () => {
        statusCalls += 1;
        if (statusCalls === 1) {
          await failureRefreshGate;
          return;
        }
        throw new Error("status unavailable");
      };
      const activeFailureRefresh = refreshLiveData();
      const queuedFailureRefresh = refreshLiveData({ force: true });
      releaseFailureRefresh();
      const [, queuedFailureResult] = await Promise.all([activeFailureRefresh, queuedFailureRefresh]);
      if (queuedFailureResult.ok || !queuedFailureResult.failures.includes("status")) {
        throw new Error("queued refresh failure result was not propagated");
      }
    })();
  `;
  await vm.runInNewContext(`${source}\n${testProgram}`, context, { filename: "app-account-state-test.js" });
}

main().catch((error) => {
  console.error(error.stack || error.message || String(error));
  process.exitCode = 1;
});
