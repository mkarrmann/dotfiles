// Drives a server through T3 Code's own OpenCode 2 client, so every response and
// event is decoded exactly as T3 Code decodes it.
//
//   node t3-client.mjs BASE_URL PASSWORD
//
// It must run from a directory whose node_modules resolves T3 Code's packages
// (tests/conftest.py links it). Requests are JSON lines on stdin:
//   {"id": 1, "call": "session.create", "args": {...}}
// Output is JSON lines on stdout:
//   {"kind": "result", "id": 1, "ok": ...} | {"kind": "result", "id": 1, "error": {...}}
//   {"kind": "event", "event": ...}        (decoded, or T3 Code's unreadable.* stand-in)
//   {"kind": "stream", "state": "open" | "closed", "error"?: ...}
import * as readline from "node:readline"
import * as Effect from "effect/Effect"
import * as Stream from "effect/Stream"
import * as FetchHttpClient from "effect/http/FetchHttpClient"

const T3 = process.env.T3CODE_REPO ?? `${process.env.HOME}/repos/t3code`
const { make } = await import(`${T3}/packages/provider-opencode/src/server/v2/OpenCode2Client.ts`)

const [baseUrl, password] = process.argv.slice(2)
const write = (value) => process.stdout.write(JSON.stringify(value) + "\n")

const run = (effect) => Effect.runPromise(effect.pipe(Effect.provide(FetchHttpClient.layer)))

const api = await run(Effect.flatMap(make, (service) => service.connect({ baseUrl, password })))

const errorOf = (cause) => {
  const error = cause?.cause ?? cause
  const plain = {}
  for (const key of Object.keys(error ?? {})) {
    if (["issue", "cause"].includes(key)) continue
    plain[key] = error[key]
  }
  return { ...plain, tag: error?._tag ?? error?.name ?? "Error", message: String(error?.message ?? error).slice(0, 2000) }
}

const events = api.events.pipe(
  Effect.tap(() => Effect.sync(() => write({ kind: "stream", state: "open" }))),
  Effect.flatMap((stream) => Stream.runForEach(stream, (event) => Effect.sync(() => write({ kind: "event", event })))),
  Effect.matchCause({
    onSuccess: () => write({ kind: "stream", state: "closed" }),
    onFailure: (cause) => write({ kind: "stream", state: "closed", error: String(cause) }),
  }),
)
run(events)

for await (const line of readline.createInterface({ input: process.stdin })) {
  if (!line.trim()) continue
  const request = JSON.parse(line)
  let target = api.client
  for (const part of request.call.split(".")) target = target[part]
  run(target(request.args ?? {})).then(
    (ok) => write({ kind: "result", id: request.id, ok: ok ?? null }),
    (cause) => write({ kind: "result", id: request.id, error: errorOf(cause) }),
  )
}
process.exit(0)
