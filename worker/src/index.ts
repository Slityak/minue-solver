import { solveWithClaude, SolverError } from "./claude";
import type { SolveInput, SolveMode } from "./prompt";

export interface Env {
  ANTHROPIC_API_KEY: string;
  SOLVER_TOKEN: string;
  MODEL?: string;
  PER_IP_LIMITER?: RateLimit;
  GLOBAL_LIMITER?: RateLimit;
}

const DEFAULT_MODEL = "claude-opus-5-5";
const MAX_TOKENS = 4096;
const MAX_QUESTION_LENGTH = 5000;
const MAX_FIX_FIELD_LENGTH = 10000;
const MODES: readonly SolveMode[] = ["solve", "explain", "check", "fix"];

interface SolveRequest {
  input: SolveInput;
  mode: SolveMode;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const { pathname } = new URL(request.url);

    if (request.method === "GET" && pathname === "/health") {
      return json({ status: "ok", model: env.MODEL ?? DEFAULT_MODEL });
    }
    if (pathname !== "/solve") {
      return json({ error: "Not found" }, 404);
    }
    if (request.method !== "POST") {
      return json({ error: "Method not allowed" }, 405);
    }
    if (!isAuthorized(request, env.SOLVER_TOKEN)) {
      return json({ error: "Unauthorized" }, 401);
    }
    if (await isRateLimited(request, env)) {
      return json({ error: "Too many requests, try again in a minute" }, 429);
    }

    let payload: SolveRequest;
    try {
      payload = validate(await request.json());
    } catch (error) {
      return json({ error: error instanceof Error ? error.message : "Invalid request" }, 400);
    }

    try {
      const result = await solveWithClaude(
        { apiKey: env.ANTHROPIC_API_KEY, model: env.MODEL ?? DEFAULT_MODEL, maxTokens: MAX_TOKENS },
        payload.input,
        payload.mode,
      );
      return json(result);
    } catch (error) {
      const status = error instanceof SolverError ? error.status : 500;
      const message = error instanceof Error ? error.message : "Unexpected error";
      return json({ error: message }, status);
    }
  },
} satisfies ExportedHandler<Env>;

function validate(body: unknown): SolveRequest {
  if (typeof body !== "object" || body === null) {
    throw new Error("Body must be a JSON object");
  }
  const { question, mode = "solve", studentAnswer, code, error } = body as Record<string, unknown>;
  if (typeof question !== "string" || question.trim() === "") {
    throw new Error("'question' must be a non-empty string");
  }
  if (question.length > MAX_QUESTION_LENGTH) {
    throw new Error(`'question' must be at most ${MAX_QUESTION_LENGTH} characters`);
  }
  if (!MODES.includes(mode as SolveMode)) {
    throw new Error(`'mode' must be one of: ${MODES.join(", ")}`);
  }
  if (mode === "check" && (typeof studentAnswer !== "string" || studentAnswer.trim() === "")) {
    throw new Error("'studentAnswer' is required in check mode");
  }
  if (mode === "fix") {
    for (const [name, value] of [["code", code], ["error", error]] as const) {
      if (typeof value !== "string" || value.trim() === "") {
        throw new Error(`'${name}' is required in fix mode`);
      }
      if (value.length > MAX_FIX_FIELD_LENGTH) {
        throw new Error(`'${name}' must be at most ${MAX_FIX_FIELD_LENGTH} characters`);
      }
    }
  }
  return {
    mode: mode as SolveMode,
    input: {
      question,
      studentAnswer: mode === "check" ? (studentAnswer as string) : undefined,
      code: mode === "fix" ? (code as string) : undefined,
      error: mode === "fix" ? (error as string) : undefined,
    },
  };
}

/** The token is public (it ships in the pip package), so rate limits cap what a stranger can spend. */
async function isRateLimited(request: Request, env: Env): Promise<boolean> {
  const ip = request.headers.get("cf-connecting-ip") ?? "unknown";
  const checks = [env.PER_IP_LIMITER?.limit({ key: ip }), env.GLOBAL_LIMITER?.limit({ key: "global" })];
  const outcomes = await Promise.all(checks);
  return outcomes.some((outcome) => outcome !== undefined && !outcome.success);
}

function isAuthorized(request: Request, expectedToken: string): boolean {
  const header = request.headers.get("authorization") ?? "";
  const token = header.startsWith("Bearer ") ? header.slice("Bearer ".length) : "";
  return Boolean(expectedToken) && timingSafeEqual(token, expectedToken);
}

/** Constant-time string comparison so the token cannot be guessed via response timing. */
function timingSafeEqual(a: string, b: string): boolean {
  const encoder = new TextEncoder();
  const left = encoder.encode(a);
  const right = encoder.encode(b);
  let diff = left.length ^ right.length;
  for (let i = 0; i < Math.max(left.length, right.length); i++) {
    diff |= (left[i] ?? 0) ^ (right[i] ?? 0);
  }
  return diff === 0;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}
