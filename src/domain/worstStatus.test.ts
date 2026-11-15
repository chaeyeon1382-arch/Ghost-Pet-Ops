import { describe, expect, it } from "vitest";
import { SEVERITY_ORDER, worstStatus } from "./worstStatus";
import type { Status } from "./types";

describe("SEVERITY_ORDER", () => {
  it("is down, slow, recovering, unknown, healthy", () => {
    expect([...SEVERITY_ORDER]).toEqual(["down", "slow", "recovering", "unknown", "healthy"]);
  });

  it("lists each of the five statuses exactly once", () => {
    const all: Status[] = ["unknown", "healthy", "slow", "down", "recovering"];
    expect(SEVERITY_ORDER).toHaveLength(all.length);
    for (const status of all) {
      expect(SEVERITY_ORDER).toContain(status);
    }
    expect(new Set(SEVERITY_ORDER).size).toBe(all.length);
  });
});

describe("worstStatus", () => {
  it("returns the status of a single service", () => {
    for (const status of SEVERITY_ORDER) {
      expect(worstStatus([status])).toBe(status);
    }
  });

  it("lets each status win over every status after it in the order", () => {
    for (let worse = 0; worse < SEVERITY_ORDER.length; worse++) {
      for (let better = worse + 1; better < SEVERITY_ORDER.length; better++) {
        const a = SEVERITY_ORDER[worse];
        const b = SEVERITY_ORDER[better];
        expect(worstStatus([a, b])).toBe(a);
        expect(worstStatus([b, a])).toBe(a);
      }
    }
  });

  it("does not depend on the position in the list", () => {
    expect(worstStatus(["healthy", "healthy", "down"])).toBe("down");
    expect(worstStatus(["down", "healthy", "healthy"])).toBe("down");
    expect(worstStatus(["healthy", "down", "healthy"])).toBe("down");
  });

  it("returns that status when all services have it", () => {
    expect(worstStatus(["slow", "slow", "slow"])).toBe("slow");
    expect(worstStatus(["healthy", "healthy"])).toBe("healthy");
  });

  it("picks the worst of a mixed list", () => {
    expect(worstStatus(["healthy", "recovering", "unknown", "slow"])).toBe("slow");
    expect(worstStatus(["healthy", "recovering", "unknown"])).toBe("recovering");
    expect(worstStatus(["healthy", "unknown"])).toBe("unknown");
    expect(worstStatus(["slow", "down", "recovering"])).toBe("down");
  });

  it("keeps the pet waiting when one service is unknown among healthy ones", () => {
    expect(worstStatus(["healthy", "healthy", "unknown", "healthy"])).toBe("unknown");
  });

  it("returns unknown for an empty list", () => {
    expect(worstStatus([])).toBe("unknown");
  });

  it("does not change the list it is given", () => {
    const input: Status[] = ["healthy", "down", "slow"];
    worstStatus(input);
    expect(input).toEqual(["healthy", "down", "slow"]);
  });
});
