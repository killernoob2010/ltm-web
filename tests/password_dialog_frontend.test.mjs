import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const appSource = fs.readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8");
const html = fs.readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");

function extractFunction(name) {
  const start = appSource.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, `${name} must exist`);
  let depth = 0;
  let opened = false;
  for (let index = start; index < appSource.length; index += 1) {
    if (appSource[index] === "{") {
      depth += 1;
      opened = true;
    } else if (appSource[index] === "}") {
      depth -= 1;
      if (opened && depth === 0) return appSource.slice(start, index + 1);
    }
  }
  throw new Error(`unable to extract ${name}`);
}

test("password dialog opens without relying on browser-created id globals", () => {
  const selectors = [
    "#currentPassword",
    "#newPassword",
    "#confirmNewPassword",
    "#changePasswordError",
    "#changePasswordDialog",
  ];
  const elements = Object.fromEntries(selectors.map((selector) => [selector, {
    value: "existing",
    textContent: "existing error",
    opened: false,
    showModal() { this.opened = true; },
  }]));
  const declarations = selectors.map((selector) => {
    const variable = selector.slice(1);
    const pattern = new RegExp(`const ${variable} = document\\.querySelector\\(\\"${selector}\\"\\);`);
    return appSource.match(pattern)?.[0] || "";
  }).join("\n");
  const context = {
    document: { querySelector: (selector) => elements[selector] },
  };

  vm.runInNewContext(`${declarations}\n${extractFunction("openChangePasswordDialog")}\nopenChangePasswordDialog();`, context);

  assert.equal(elements["#changePasswordDialog"].opened, true);
  assert.equal(elements["#currentPassword"].value, "");
  assert.equal(elements["#newPassword"].value, "");
  assert.equal(elements["#confirmNewPassword"].value, "");
  assert.equal(elements["#changePasswordError"].textContent, "");
});

test("password dialog fix is served with a new application script URL", () => {
  assert.match(html, /\/static\/app\.js\?[^"\n]*password-dialog=20260904a/);
});
