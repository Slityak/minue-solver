import { describe, expect, it, vi, afterEach } from "vitest";
import worker, { type Env } from "../src/index";
import { parseResult, solveWithClaude, type ContentBlock } from "../src/claude";

const ENV: Env = { ANTHROPIC_API_KEY: "test-key", SOLVER_TOKEN: "secret-token" };

const FINAL_JSON = JSON.stringify({
  answer: "28",
  code: "import numpy as np\nprint(28)",
  explanation: "Manhattan distance via scipy cityblock.",
});

function apiResponse(content: ContentBlock[], stopReason = "end_turn"): Response {
  return new Response(JSON.stringify({ content, stop_reason: stopReason }), { status: 200 });
}

const COMPLETE_TURN: ContentBlock[] = [
  { type: "server_tool_use", id: "srvtoolu_1", name: "bash_code_execution" },
  {
    type: "bash_code_execution_tool_result",
    content: { type: "bash_code_execution_result", stdout: "28\n", stderr: "", return_code: 0 },
  },
  { type: "text", text: FINAL_JSON },
];

function solveRequest(body: unknown, token = "secret-token"): Request {
  return new Request("https://solver.test/solve", {
    method: "POST",
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("routing and validation", () => {
  it("answers the health check without auth", async () => {
    const response = await worker.fetch(new Request("https://solver.test/health"), ENV);
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ status: "ok" });
  });

  it("rejects a wrong token", async () => {
    const response = await worker.fetch(solveRequest({ question: "x" }, "wrong"), ENV);
    expect(response.status).toBe(401);
  });

  it("rejects an empty question", async () => {
    const response = await worker.fetch(solveRequest({ question: "  " }), ENV);
    expect(response.status).toBe(400);
  });

  it("requires a student answer in check mode", async () => {
    const response = await worker.fetch(solveRequest({ question: "q", mode: "check" }), ENV);
    expect(response.status).toBe(400);
  });

  it("requires the failing code and error in fix mode", async () => {
    const response = await worker.fetch(solveRequest({ question: "q", mode: "fix", code: "x" }), ENV);
    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ error: "'error' is required in fix mode" });
  });

  it("returns 429 when a rate limiter refuses", async () => {
    const limiter = (success: boolean) => ({ limit: vi.fn().mockResolvedValue({ success }) });
    const perIp = limiter(false);
    const env: Env = { ...ENV, PER_IP_LIMITER: perIp, GLOBAL_LIMITER: limiter(true) };

    const request = solveRequest({ question: "q" });
    request.headers.set("cf-connecting-ip", "1.2.3.4");
    const response = await worker.fetch(request, env);

    expect(response.status).toBe(429);
    expect(perIp.limit).toHaveBeenCalledWith({ key: "1.2.3.4" });
  });

  it("returns 404 for unknown paths", async () => {
    const response = await worker.fetch(new Request("https://solver.test/nope"), ENV);
    expect(response.status).toBe(404);
  });
});

describe("solve flow", () => {
  it("returns answer, code and tool stdout", async () => {
    const fetchMock = vi.fn().mockResolvedValue(apiResponse(COMPLETE_TURN));
    vi.stubGlobal("fetch", fetchMock);

    const response = await worker.fetch(solveRequest({ question: "Manhattan distance..." }), ENV);

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      answer: "28",
      code: "import numpy as np\nprint(28)",
      explanation: "Manhattan distance via scipy cityblock.",
      stdout: "28\n",
    });
    const sentBody = JSON.parse(fetchMock.mock.calls[0]![1].body as string);
    expect(sentBody.tools).toEqual([{ type: "code_execution_20250825", name: "code_execution" }]);
    expect(fetchMock.mock.calls[0]![1].headers["x-api-key"]).toBe("test-key");
  });

  it("resumes a paused turn", async () => {
    const paused: ContentBlock[] = [{ type: "server_tool_use", id: "srvtoolu_1" }];
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(apiResponse(paused, "pause_turn"))
      .mockResolvedValueOnce(apiResponse(COMPLETE_TURN));

    const result = await solveWithClaude(
      { apiKey: "k", model: "m", maxTokens: 100, fetchImpl: fetchMock },
      { question: "q" },
      "solve",
    );

    expect(result.answer).toBe("28");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const secondBody = JSON.parse(fetchMock.mock.calls[1]![1].body as string);
    expect(secondBody.messages.at(-1)).toEqual({ role: "assistant", content: paused });
  });

  it("sends the failing code and error to Claude in fix mode", async () => {
    const fetchMock = vi.fn().mockResolvedValue(apiResponse(COMPLETE_TURN));
    vi.stubGlobal("fetch", fetchMock);

    const response = await worker.fetch(
      solveRequest({ question: "q", mode: "fix", code: "print(np.x)", error: "NameError: np" }),
      ENV,
    );

    expect(response.status).toBe(200);
    const sentBody = JSON.parse(fetchMock.mock.calls[0]![1].body as string);
    expect(sentBody.system).toContain("Mode: fix");
    expect(sentBody.messages[0].content).toContain("Script that failed:\nprint(np.x)");
    expect(sentBody.messages[0].content).toContain("Error:\nNameError: np");
  });

  it("maps Claude API failures to 502", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("overloaded", { status: 529 })));
    const response = await worker.fetch(solveRequest({ question: "q" }), ENV);
    expect(response.status).toBe(502);
  });
});

describe("parseResult", () => {
  it("tolerates markdown fences around the JSON", () => {
    const result = parseResult([{ type: "text", text: "```json\n" + FINAL_JSON + "\n```" }]);
    expect(result.answer).toBe("28");
  });

  it("stringifies numeric answers", () => {
    const result = parseResult([{ type: "text", text: '{"answer": 0.5663, "code": ""}' }]);
    expect(result.answer).toBe("0.5663");
  });

  it("fails clearly when there is no JSON", () => {
    expect(() => parseResult([{ type: "text", text: "The answer is 28." }])).toThrow(/no JSON/);
  });
});
