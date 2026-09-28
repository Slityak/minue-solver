import { buildSystemPrompt, buildUserMessage, type SolveInput, type SolveMode } from "./prompt";

const API_URL = "https://api.anthropic.com/v1/messages";
const API_VERSION = "2023-06-01";
const CODE_EXECUTION_TOOL = { type: "code_execution_20250825", name: "code_execution" } as const;
/** Server tools may pause long turns; we resume a bounded number of times. */
const MAX_CONTINUATIONS = 4;

export interface ContentBlock {
  type: string;
  text?: string;
  content?: { type?: string; stdout?: string; stderr?: string; return_code?: number };
  [key: string]: unknown;
}

interface MessagesResponse {
  content: ContentBlock[];
  stop_reason: string;
}

interface Message {
  role: "user" | "assistant";
  content: string | ContentBlock[];
}

export interface SolveResult {
  answer: string;
  code: string;
  explanation: string;
  stdout: string;
}

export interface ClaudeConfig {
  apiKey: string;
  model: string;
  maxTokens: number;
  fetchImpl?: typeof fetch;
}

export class SolverError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "SolverError";
  }
}

export async function solveWithClaude(
  config: ClaudeConfig,
  input: SolveInput,
  mode: SolveMode,
): Promise<SolveResult> {
  const fetchImpl = config.fetchImpl ?? fetch;
  const messages: Message[] = [{ role: "user", content: buildUserMessage(input) }];
  const collected: ContentBlock[] = [];

  for (let turn = 0; turn <= MAX_CONTINUATIONS; turn++) {
    const response = await callMessagesApi(fetchImpl, config, mode, messages);
    collected.push(...response.content);
    if (response.stop_reason !== "pause_turn") {
      return parseResult(collected);
    }
    // Resume the paused server-tool turn by echoing the assistant content back.
    messages.push({ role: "assistant", content: response.content });
  }
  throw new SolverError("Claude did not finish within the continuation limit", 504);
}

async function callMessagesApi(
  fetchImpl: typeof fetch,
  config: ClaudeConfig,
  mode: SolveMode,
  messages: Message[],
): Promise<MessagesResponse> {
  const response = await fetchImpl(API_URL, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": config.apiKey,
      "anthropic-version": API_VERSION,
    },
    body: JSON.stringify({
      model: config.model,
      max_tokens: config.maxTokens,
      system: buildSystemPrompt(mode),
      messages,
      tools: [CODE_EXECUTION_TOOL],
    }),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new SolverError(`Claude API error ${response.status}: ${detail.slice(0, 500)}`, 502);
  }
  return (await response.json()) as MessagesResponse;
}

/** Extracts the final JSON object from Claude's last text block, plus all tool stdout. */
export function parseResult(blocks: ContentBlock[]): SolveResult {
  const stdout = blocks
    .filter((block) => block.type.endsWith("code_execution_tool_result"))
    .map((block) => block.content?.stdout ?? "")
    .filter(Boolean)
    .join("\n");

  const lastText = [...blocks].reverse().find((block) => block.type === "text" && block.text?.trim());
  if (!lastText?.text) {
    throw new SolverError("Claude returned no final answer", 502);
  }

  const parsed = extractJsonObject(lastText.text);
  if (typeof parsed.answer !== "string" && typeof parsed.answer !== "number") {
    throw new SolverError("Claude's answer is missing the 'answer' field", 502);
  }
  return {
    answer: String(parsed.answer),
    code: typeof parsed.code === "string" ? parsed.code : "",
    explanation: typeof parsed.explanation === "string" ? parsed.explanation : "",
    stdout,
  };
}

function extractJsonObject(text: string): Record<string, unknown> {
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start === -1 || end <= start) {
    throw new SolverError("Claude's final message contained no JSON object", 502);
  }
  try {
    return JSON.parse(text.slice(start, end + 1)) as Record<string, unknown>;
  } catch {
    throw new SolverError("Claude's final message was not valid JSON", 502);
  }
}
