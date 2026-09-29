import { execFileSync } from "node:child_process";
import { copyFileSync, mkdirSync, rmSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = resolve(root, "src");
const output = resolve(root, "dist");

execFileSync(process.execPath, ["--check", resolve(source, "app.js")], {
  stdio: "inherit",
});
rmSync(output, { recursive: true, force: true });
mkdirSync(output, { recursive: true });
copyFileSync(resolve(source, "index.html"), resolve(output, "index.html"));
for (const asset of ["app.css", "app.js"]) {
  copyFileSync(resolve(source, asset), resolve(output, asset));
}
process.stdout.write(`Built ${output}\n`);
