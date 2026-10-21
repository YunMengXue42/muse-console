import { build } from "esbuild";
import { mkdir, copyFile, cp } from "node:fs/promises";
await mkdir("dist", { recursive: true });
await build({
  entryPoints: ["src/worker.ts", "src/options.ts", "src/popup.ts"],
  bundle: true,
  outdir: "dist",
  format: "esm",
  target: "chrome132",
  minify: false,
});
for (const file of ["manifest.json", "options.html", "popup.html", "ui.css"])
  await copyFile(file, `dist/${file}`);
await cp("fonts", "dist/fonts", { recursive: true });
