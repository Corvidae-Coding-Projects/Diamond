#!/usr/bin/env node
"use strict";

// Actual Chromium, Firefox, and WebKit qualification for compiler-generated UI.
const { chromium, firefox, webkit } = require("playwright");
const { spawn } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");
const BINARY = path.resolve(process.argv[2] || path.join(ROOT, "target/debug/diamond"));
const REPORT = path.resolve(process.argv[3] || path.join(ROOT, "verification/lp8/browser-engines.json"));
const SUMMARY = "9007199254740993|3|-2|-0.000002|😀|·a·b·";
const LP1_PORT = 39173;
const COMPONENT_PORT = 39174;

function sha256(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function server(project, port) {
  const child = spawn(BINARY, ["serve", project, "--listen", `127.0.0.1:${port}`], {
    cwd: ROOT,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "";
  child.stdout.on("data", value => { output += value; });
  child.stderr.on("data", value => { output += value; });
  child.capturedOutput = () => output;
  return child;
}

async function ready(port, child) {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null)
      throw new Error(`server ${port} exited: ${child.capturedOutput()}`);
    try {
      const response = await fetch(`http://127.0.0.1:${port}/`);
      if (response.status === 200)
        return;
    } catch (_) {
      // The owned server is still starting.
    }
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  throw new Error(`server ${port} readiness deadline`);
}

async function stop(child) {
  if (child.exitCode !== null)
    return child.exitCode;
  child.kill("SIGINT");
  await Promise.race([
    new Promise(resolve => child.once("exit", resolve)),
    new Promise((_, reject) => setTimeout(() => reject(new Error("server shutdown deadline")), 5_000)),
  ]);
  return child.exitCode;
}

async function qualify(name, launcher, options) {
  const observation = { name, pass: false, checks: {}, timingsMs: {}, consoleErrors: [] };
  let browser;
  try {
    browser = await launcher.launch(options);
    observation.version = browser.version();
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const page = await context.newPage();
    page.on("console", message => {
      if (message.type() === "error" &&
          !message.text().includes("Diamond component update failed") &&
          !message.text().includes("Failed to load resource"))
        observation.consoleErrors.push(message.text());
    });

    let started = Date.now();
    await page.goto(`http://127.0.0.1:${LP1_PORT}/`, { waitUntil: "networkidle" });
    observation.timingsMs.initialNavigation = Date.now() - started;
    observation.checks.initial =
      await page.locator("#initial").innerText() === SUMMARY &&
      await page.locator("#number").innerText() === "7" &&
      await page.locator("#hostile").innerText() === "<script>lp1()</script>" &&
      await page.locator("#hostile script").count() === 0;

    started = Date.now();
    await page.getByRole("button", { name: "Evaluate shared semantics" }).click();
    observation.timingsMs.sharedEvent = Date.now() - started;
    observation.checks.sharedSemantics =
      await page.locator("#result").innerText() === SUMMARY &&
      await page.locator("#number").innerText() === "9";
    await page.getByRole("button", { name: "Fail arithmetic" }).click();
    observation.checks.mappedFailureAtomic =
      (await page.locator("#diamond-error").innerText()).includes("Contract.fail") &&
      await page.locator("#number").innerText() === "9";
    await page.getByRole("button", { name: "Recover valid state" }).click();
    observation.checks.recovery =
      await page.locator("#diamond-error").isHidden() &&
      await page.locator("#number").innerText() === "7";

    await page.reload({ waitUntil: "networkidle" });
    await page.keyboard.press("Tab");
    const keyboardTarget = await page.evaluate(() => document.activeElement?.textContent?.trim());
    await page.keyboard.press("Enter");
    observation.checks.keyboard =
      keyboardTarget === "Evaluate shared semantics" &&
      await page.locator("#result").innerText() === SUMMARY;
    await page.setViewportSize({ width: 360, height: 640 });
    observation.checks.lp1Narrow = await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    );

    started = Date.now();
    await page.goto(`http://127.0.0.1:${COMPONENT_PORT}/`, { waitUntil: "networkidle" });
    observation.timingsMs.componentNavigation = Date.now() - started;
    const title = page.getByLabel("Title").first();
    const titleId = await title.getAttribute("id");
    const describedBy = await title.getAttribute("aria-describedby");
    const describedIds = (describedBy || "").split(/\s+/).filter(Boolean);
    const statusCount = await page.getByRole("status").count();
    observation.checks.accessibleRelationships =
      Boolean(titleId) &&
      describedIds.length === 2 &&
      await page.locator(`label[for="${titleId}"]`).innerText() === "Title" &&
      (await page.locator(`[id="${describedIds[0]}"]`).innerText()).includes("1 to 20") &&
      statusCount >= 3;
    await title.fill("");
    started = Date.now();
    await page.getByRole("button", { name: "Save details" }).first().click();
    observation.timingsMs.validationEvent = Date.now() - started;
    observation.checks.structuredError =
      await title.getAttribute("aria-invalid") === "true" &&
      (await page.locator(`[id="${describedIds[1]}"]`).innerText()).length > 0;

    await page.reload({ waitUntil: "networkidle" });
    await page.keyboard.press("Tab");
    const componentKeyboardTarget = await page.evaluate(
      () => document.activeElement?.textContent?.trim()
    );
    await page.keyboard.press("Enter");
    observation.checks.componentKeyboard = componentKeyboardTarget === "Reverse editors";
    await page.setViewportSize({ width: 360, height: 640 });
    observation.checks.componentNarrow = await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    );

    await context.close();
    observation.pass =
      Object.values(observation.checks).every(Boolean) && observation.consoleErrors.length === 0;
  } catch (error) {
    observation.error = String(error && error.stack || error);
  } finally {
    if (browser)
      await browser.close();
  }
  return observation;
}

async function main() {
  const lp1 = server(path.join(ROOT, "verification/lp1/browser/diamond.toml"), LP1_PORT);
  const components = server(path.join(ROOT, "examples/components/diamond.toml"), COMPONENT_PORT);
  const shutdowns = {};
  let observations = [];
  let fatal = null;
  try {
    await Promise.all([ready(LP1_PORT, lp1), ready(COMPONENT_PORT, components)]);
    const webkitBundle = process.env.DIAMOND_LP8_WEBKIT_BUNDLE;
    const webkitDeps = process.env.DIAMOND_LP8_WEBKIT_DEPS;
    observations = [
      await qualify("Chromium", chromium, {
        headless: true,
        executablePath: "/usr/bin/google-chrome",
        args: ["--no-sandbox"],
      }),
      await qualify("Firefox", firefox, { headless: true }),
      await qualify("WebKit", webkit, {
        headless: true,
        executablePath: path.join(ROOT, "verification/lp8_webkit_runner.sh"),
        env: {
          ...process.env,
          DIAMOND_LP8_WEBKIT_BUNDLE: webkitBundle,
          DIAMOND_LP8_WEBKIT_DEPS: webkitDeps,
        },
      }),
    ];
  } catch (error) {
    fatal = String(error && error.stack || error);
  } finally {
    try { shutdowns.lp1 = await stop(lp1); } catch (error) { shutdowns.lp1 = String(error); }
    try { shutdowns.components = await stop(components); } catch (error) { shutdowns.components = String(error); }
  }
  const report = {
    status: !fatal && observations.length === 3 && observations.every(row => row.pass) &&
      shutdowns.lp1 === 0 && shutdowns.components === 0 ? "PASS" : "FAIL",
    binary_sha256: sha256(BINARY),
    driver_sha256: sha256(__filename),
    runner_sha256: sha256(path.join(ROOT, "verification/lp8_webkit_runner.sh")),
    compatibilityLibraries: {
      icu76: "c1bf762996de9ecba9b9d871e4928a8090f023a3e9e7fa3240b3d90f892a01dc",
      woff1: "628a2a993556027adaa4e93a675fee341e3d47d306db7f3811ff56ea84a42022",
    },
    fatal,
    observations,
    shutdowns,
  };
  fs.mkdirSync(path.dirname(REPORT), { recursive: true });
  fs.writeFileSync(REPORT, JSON.stringify(report, null, 2) + "\n");
  console.log(JSON.stringify(report, null, 2));
  process.exitCode = report.status === "PASS" ? 0 : 1;
}

main();
