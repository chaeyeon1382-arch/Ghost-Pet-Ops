import { describe, expect, it } from "vitest";
import { STATUS_PATH, fetchStatus } from "./fetchStatus";

// No network: every test passes its own fake fetch. Fixtures are fixed.
const VALID_BODY = {
  services: [{ id: "site-1", name: "Example", status: "healthy", responseMs: 84, lastChecked: "2026-11-15T02:00:00Z" }],
  events: [{ serviceId: "site-1", from: "unknown", to: "healthy", at: "2026-11-15T02:00:00Z", note: "first check" }],
};

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), { status: 200, ...init });
}

/** A fake fetch that always resolves with the given response. */
function respondWith(res: Response): typeof fetch {
  return async () => res;
}

/** A fake fetch that always rejects with the given error. */
function rejectWith(error: unknown): typeof fetch {
  return async () => {
    throw error;
  };
}

/** Builds a response whose body must not be read. Reading it records the fact. */
function unreadableBodyResponse(status: number, headers: Record<string, string>) {
  const state = { bodyRead: false };
  const res = {
    ok: false,
    status,
    headers: new Headers(headers),
    json: async () => {
      state.bodyRead = true;
      return {};
    },
    text: async () => {
      state.bodyRead = true;
      return "";
    },
  } as unknown as Response;
  return { res, state };
}

const live = () => new AbortController().signal;

describe("fetchStatus: request", () => {
  it("requests the relative path /status with the signal and an Accept header", async () => {
    const seen: { url: unknown; init: RequestInit | undefined }[] = [];
    const fake: typeof fetch = async (input, init) => {
      seen.push({ url: input, init });
      return jsonResponse(VALID_BODY);
    };
    const controller = new AbortController();
    await fetchStatus(controller.signal, fake);

    expect(STATUS_PATH).toBe("/status");
    expect(seen).toHaveLength(1);
    expect(seen[0].url).toBe("/status");
    expect(seen[0].init?.signal).toBe(controller.signal);
    expect(seen[0].init?.headers).toEqual({ Accept: "application/json" });
  });
});

describe("fetchStatus: success", () => {
  it("returns validated data", async () => {
    const outcome = await fetchStatus(live(), respondWith(jsonResponse(VALID_BODY)));
    expect(outcome).toEqual({ kind: "success", data: VALID_BODY });
  });

  it("drops extra fields from the body", async () => {
    const body = { ...VALID_BODY, internal: "x", services: [{ ...VALID_BODY.services[0], consecutiveFailures: 3 }] };
    const outcome = await fetchStatus(live(), respondWith(jsonResponse(body)));
    expect(outcome).toEqual({ kind: "success", data: VALID_BODY });
  });
});

describe("fetchStatus: failures", () => {
  it("returns a network failure when fetch rejects", async () => {
    const outcome = await fetchStatus(live(), rejectWith(new TypeError("Failed to fetch")));
    expect(outcome).toEqual({ kind: "failure", reason: { kind: "network" }, retryAfter: null });
  });

  it("returns a network failure when fetch throws before returning a promise", async () => {
    const throwing: typeof fetch = () => {
      throw new Error("boom");
    };
    const outcome = await fetchStatus(live(), throwing);
    expect(outcome).toEqual({ kind: "failure", reason: { kind: "network" }, retryAfter: null });
  });

  it("returns the status code for non-2xx responses", async () => {
    for (const status of [400, 403, 404, 500, 502, 503]) {
      const outcome = await fetchStatus(live(), respondWith(new Response("x", { status })));
      expect(outcome).toEqual({ kind: "failure", reason: { kind: "http", status }, retryAfter: null });
    }
  });

  it("returns a 429 without Retry-After as retryAfter null", async () => {
    const outcome = await fetchStatus(live(), respondWith(new Response("Too Many Requests", { status: 429 })));
    expect(outcome).toEqual({ kind: "failure", reason: { kind: "http", status: 429 }, retryAfter: null });
  });

  it("returns the raw Retry-After header in seconds", async () => {
    const res = new Response("x", { status: 429, headers: { "Retry-After": "30" } });
    const outcome = await fetchStatus(live(), respondWith(res));
    expect(outcome).toEqual({ kind: "failure", reason: { kind: "http", status: 429 }, retryAfter: "30" });
  });

  it("returns the raw Retry-After header as an HTTP date, without parsing it", async () => {
    const date = "Sun, 15 Nov 2026 02:00:30 GMT";
    const res = new Response("x", { status: 429, headers: { "Retry-After": date } });
    const outcome = await fetchStatus(live(), respondWith(res));
    expect(outcome).toEqual({ kind: "failure", reason: { kind: "http", status: 429 }, retryAfter: date });
  });

  it("never reads the body of a non-2xx response", async () => {
    for (const status of [429, 500, 403]) {
      const { res, state } = unreadableBodyResponse(status, { "Retry-After": "5" });
      const outcome = await fetchStatus(live(), respondWith(res));
      expect(outcome.kind).toBe("failure");
      expect(state.bodyRead).toBe(false);
    }
  });

  it("returns invalid_json for a 200 whose body is not JSON", async () => {
    const outcome = await fetchStatus(live(), respondWith(new Response("<html>oops</html>", { status: 200 })));
    expect(outcome).toEqual({ kind: "failure", reason: { kind: "invalid_json" }, retryAfter: null });
  });

  it("returns invalid_json for an empty 200 body", async () => {
    const outcome = await fetchStatus(live(), respondWith(new Response("", { status: 200 })));
    expect(outcome).toEqual({ kind: "failure", reason: { kind: "invalid_json" }, retryAfter: null });
  });

  it("returns invalid_shape with the field path for a 200 that fails validation", async () => {
    const body = { ...VALID_BODY, services: [{ ...VALID_BODY.services[0], status: "Healthy" }] };
    const outcome = await fetchStatus(live(), respondWith(jsonResponse(body)));
    expect(outcome).toEqual({
      kind: "failure",
      reason: { kind: "invalid_shape", field: "services[0].status" },
      retryAfter: null,
    });
  });

  it("returns invalid_shape for JSON that is not an object", async () => {
    const outcome = await fetchStatus(live(), respondWith(jsonResponse(null)));
    expect(outcome).toEqual({
      kind: "failure",
      reason: { kind: "invalid_shape", field: "body" },
      retryAfter: null,
    });
  });

  it("does not put the response body into the failure value", async () => {
    const body = { ...VALID_BODY, services: [{ ...VALID_BODY.services[0], status: "SECRET-TOKEN-VALUE" }] };
    const outcome = await fetchStatus(live(), respondWith(jsonResponse(body)));
    expect(JSON.stringify(outcome)).not.toContain("SECRET-TOKEN-VALUE");
  });
});

describe("fetchStatus: abort", () => {
  it("returns aborted, not a failure, when the signal is aborted while fetch is pending", async () => {
    const controller = new AbortController();
    const pending: typeof fetch = (_input, init) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      });
    const result = fetchStatus(controller.signal, pending);
    controller.abort();
    expect(await result).toEqual({ kind: "aborted" });
  });

  it("returns aborted when the signal is already aborted and fetch rejects", async () => {
    const controller = new AbortController();
    controller.abort();
    const outcome = await fetchStatus(controller.signal, rejectWith(new DOMException("aborted", "AbortError")));
    expect(outcome).toEqual({ kind: "aborted" });
  });

  it("returns aborted when the signal is aborted while the body is being read", async () => {
    const controller = new AbortController();
    const res = {
      ok: true,
      status: 200,
      headers: new Headers(),
      json: async () => {
        controller.abort();
        throw new DOMException("aborted", "AbortError");
      },
    } as unknown as Response;
    const outcome = await fetchStatus(controller.signal, respondWith(res));
    expect(outcome).toEqual({ kind: "aborted" });
  });

  it("returns a network failure, not aborted, when fetch rejects and the signal is not aborted", async () => {
    const outcome = await fetchStatus(live(), rejectWith(new DOMException("aborted", "AbortError")));
    expect(outcome.kind).toBe("failure");
  });
});
