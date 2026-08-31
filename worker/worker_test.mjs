import assert from "node:assert/strict";
import fs from "node:fs/promises";

const source = await fs.readFile(new URL("./worker.js", import.meta.url), "utf8");
const { default: worker } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

const calls = [];
globalThis.fetch = async (url, init) => {
  calls.push({ url, init });
  return new Response(null, { status: 204 });
};

const env = { GH_REPO: "owner/repo", GH_TOKEN: "secret" };
await worker.scheduled({}, env, { waitUntil: (p) => p });
assert.equal(calls.length, 1);
assert.equal(calls[0].url, "https://api.github.com/repos/owner/repo/dispatches");
assert.deepEqual(JSON.parse(calls[0].init.body), { event_type: "scalp_tick" });
assert.match(calls[0].init.headers.Authorization, /^Bearer /);

console.log("worker scheduled dispatch OK");
