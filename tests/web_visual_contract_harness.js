const childProcess = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const net = require("node:net");

function findChrome() {
  const candidates = [
    process.env.CHROME_BIN,
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "chromium",
    "chromium-browser",
  ].filter(Boolean);
  for (const candidate of candidates) {
    if (path.isAbsolute(candidate) && fs.existsSync(candidate)) {
      return candidate;
    }
    const result = childProcess.spawnSync("which", [candidate], { encoding: "utf8" });
    if (result.status === 0 && result.stdout.trim()) {
      return result.stdout.trim();
    }
  }
  return "";
}

function reservePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => resolve(address.port));
    });
  });
}

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve(server.address().port));
  });
}

function closeServer(server) {
  return new Promise((resolve) => server.close(resolve));
}

function wait(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function waitForPage(port) {
  let lastError;
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json`);
      const pages = await response.json();
      const page = pages.find((item) => item.type === "page");
      if (page?.webSocketDebuggerUrl) {
        return page.webSocketDebuggerUrl;
      }
    } catch (error) {
      lastError = error;
    }
    await wait(100);
  }
  throw lastError || new Error("Chrome DevTools page did not become available");
}

function evaluate(endpoint, expression) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(endpoint);
    socket.addEventListener("open", () => {
      socket.send(JSON.stringify({
        id: 1,
        method: "Runtime.evaluate",
        params: {
          expression,
          returnByValue: true,
          awaitPromise: true,
        },
      }));
    });
    socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.id !== 1) return;
      socket.close();
      if (message.error || message.result?.exceptionDetails) {
        reject(new Error(JSON.stringify(message.error || message.result.exceptionDetails)));
        return;
      }
      resolve(message.result.result.value);
    });
    socket.addEventListener("error", reject);
  });
}

async function stopChrome(processHandle) {
  if (processHandle.exitCode !== null) return;
  processHandle.kill("SIGTERM");
  await Promise.race([
    new Promise((resolve) => processHandle.once("exit", resolve)),
    wait(1200),
  ]);
  if (processHandle.exitCode === null) {
    processHandle.kill("SIGKILL");
  }
}

function assertVisualContract(result) {
  if (result.checkbox.width !== "22px" || result.checkbox.height !== "22px") {
    throw new Error(`Checkbox computed size regressed: ${JSON.stringify(result.checkbox)}`);
  }
  if (result.topbarOverflow !== "visible") {
    throw new Error(`Session help container still clips overflow: ${result.topbarOverflow}`);
  }
  if (result.popover.display !== "block" || !result.popover.escapesTopbar) {
    throw new Error(`Session help popover cannot escape the header: ${JSON.stringify(result.popover)}`);
  }
  const lowContrast = Object.entries(result.contrast).filter(([, value]) => value < 4.5);
  if (lowContrast.length) {
    throw new Error(`Filled-control contrast is below 4.5:1: ${JSON.stringify(lowContrast)}`);
  }
  const undersized = Object.entries(result.touchTargets).filter(([, value]) => value < 44);
  if (undersized.length) {
    throw new Error(`Interactive touch target is below 44px: ${JSON.stringify(undersized)}`);
  }
}

async function main() {
  if (typeof WebSocket === "undefined" || typeof fetch === "undefined") {
    console.log("SKIP: Node.js with built-in fetch and WebSocket is required");
    return;
  }
  const chrome = findChrome();
  if (!chrome) {
    console.log("SKIP: Chrome or Chromium is required");
    return;
  }

  const root = path.resolve(__dirname, "..");
  const css = fs.readFileSync(path.join(root, "web", "styles.css"), "utf8");
  const html = `<!doctype html>
    <html lang="zh-CN">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>${css}</style>
      </head>
      <body>
        <div class="shell">
          <main class="workspace">
            <header class="topbar" id="contractTopbar">
              <div class="status-help">
                <button class="status-pill status-help-trigger warn" id="contractHelp" type="button" aria-expanded="true">Session</button>
                <div class="status-popover" id="contractPopover"><strong>Session 状态指引</strong><p>帮助内容</p></div>
              </div>
              <div class="view-switcher">
                <button id="contractView" type="button" aria-pressed="true">默认</button>
                <button type="button">参数</button>
                <button type="button">行为</button>
              </div>
              <button class="button compact" id="contractCompact" type="button">刷新</button>
            </header>
            <form class="booking-form">
              <label class="checkline">
                <input id="contractCheckbox" type="checkbox">
                <span>立即执行</span>
              </label>
            </form>
            <button class="button primary" id="contractPrimary" type="button">开始预约</button>
            <button class="court-chip selectable selected" id="contractCourt" type="button">球 7</button>
            <button class="day-more" id="contractMore" type="button">更多</button>
            <button class="chip chip-button" id="contractChipButton" type="button">可取消</button>
            <button class="icon-button" id="contractIconButton" type="button" aria-label="关闭">×</button>
            <span class="chip success" id="contractSuccess">成功</span>
          </main>
        </div>
      </body>
    </html>`;
  const server = http.createServer((request, response) => {
    response.writeHead(200, {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-store",
    });
    response.end(html);
  });
  const webPort = await listen(server);
  const debugPort = await reservePort();
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "daydayup-visual-contract-"));
  const chromeProcess = childProcess.spawn(chrome, [
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    `--remote-debugging-port=${debugPort}`,
    `--user-data-dir=${profile}`,
    "--window-size=1440,900",
    `http://127.0.0.1:${webPort}/`,
  ], {
    stdio: ["ignore", "ignore", "pipe"],
  });

  try {
    const endpoint = await waitForPage(debugPort);
    await wait(300);
    const result = await evaluate(endpoint, `(() => {
      const style = (id) => getComputedStyle(document.getElementById(id));
      const pixels = (color) => {
        const canvas = document.createElement("canvas");
        canvas.width = 1;
        canvas.height = 1;
        const context = canvas.getContext("2d", { willReadFrequently: true });
        context.clearRect(0, 0, 1, 1);
        context.fillStyle = color;
        context.fillRect(0, 0, 1, 1);
        return [...context.getImageData(0, 0, 1, 1).data.slice(0, 3)];
      };
      const luminance = (color) => {
        const channels = pixels(color).map((value) => {
          const channel = value / 255;
          return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
        });
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
      };
      const contrast = (element) => {
        const computed = getComputedStyle(element);
        const values = [luminance(computed.color), luminance(computed.backgroundColor)].sort((a, b) => b - a);
        return (values[0] + 0.05) / (values[1] + 0.05);
      };
      const minimumHeight = (id) => parseFloat(style(id).minHeight);
      const checkbox = style("contractCheckbox");
      const topbar = document.getElementById("contractTopbar").getBoundingClientRect();
      const popover = document.getElementById("contractPopover");
      const popoverRect = popover.getBoundingClientRect();
      return {
        checkbox: { width: checkbox.width, height: checkbox.height },
        topbarOverflow: style("contractTopbar").overflowX,
        popover: {
          display: getComputedStyle(popover).display,
          escapesTopbar: popoverRect.bottom > topbar.bottom,
        },
        contrast: {
          primary: contrast(document.getElementById("contractPrimary")),
          selectedView: contrast(document.getElementById("contractView")),
          selectedCourt: contrast(document.getElementById("contractCourt")),
          successChip: contrast(document.getElementById("contractSuccess")),
        },
        touchTargets: {
          help: minimumHeight("contractHelp"),
          view: minimumHeight("contractView"),
          compact: minimumHeight("contractCompact"),
          court: minimumHeight("contractCourt"),
          more: minimumHeight("contractMore"),
          chipButton: minimumHeight("contractChipButton"),
          iconButton: document.getElementById("contractIconButton").getBoundingClientRect().height,
        },
      };
    })()`);
    assertVisualContract(result);
    console.log(JSON.stringify(result));
  } finally {
    await stopChrome(chromeProcess);
    await closeServer(server);
    fs.rmSync(profile, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
