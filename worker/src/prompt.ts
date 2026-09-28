export type SolveMode = "solve" | "explain" | "check" | "fix";

export interface SolveInput {
  question: string;
  studentAnswer?: string;
  /** Fix mode: the script that failed and the error it produced. */
  code?: string;
  error?: string;
}

/**
 * Conventions taken from the course notebooks.
 * Keeping them explicit makes the solver compute results "the way it was taught".
 */
const COURSE_CONVENTIONS = `
Course conventions (follow these exactly unless the task says otherwise):
- Data matrices: rows are observations, columns are features. Indexing is 0-based.
- Covariance: np.cov(X_centered, rowvar=False) (sample covariance, ddof=1).
- Decorrelating / whitening transform ("korrelálatlanná tevő transzformáció"):
  Cholesky whitening, L = np.linalg.cholesky(np.linalg.pinv(cov), upper=False),
  whitened = X_centered @ L. Use pinv whenever the task mentions pseudo-inverse.
- PCA whitening: eigh(cov), X_centered @ eigvecs @ diag(1/sqrt(eigvals)); ZCA additionally @ eigvecs.T.
- Standardization: (X - mean) / std with np.std (ddof=0) unless stated otherwise.
- Distances: Minkowski/Manhattan/Euclidean/cosine/Mahalanobis via scipy.spatial.distance;
  Mahalanobis uses the inverse covariance of the data matrix.
- Jaccard similarity: |A ∩ B| / |A ∪ B| on sets.
- Random-walk matrix: row-normalize the adjacency matrix; sink rows stay zero.
- PageRank: beta = 0.85, uniform teleport vector v unless given, sink rows replaced by v,
  power iteration pi = beta * pi @ P + (1 - beta) * v from a uniform start, tol 1e-8, max 100 iterations.
- HITS: a = A.T @ h, h = A @ a, each normalized to unit L2 norm per iteration, starting from ones.
- Association rules: support = count / n, confidence = supp(X∪Y) / supp(X), lift = conf / supp(Y).
- k-means: Euclidean distance, centroids are cluster means.
`;

const OUTPUT_CONTRACT = `
Workflow:
1. Write a short, self-contained Python script using numpy (and scipy if useful) that solves the task.
   Keep it minimal: no comments, no docstrings, no helper functions unless needed, and only one print
   of the final result. It will also run in the student's Google Colab, so prefer widely compatible
   numpy/scipy APIs.
2. Execute it with the code execution tool and read the printed result. Never compute numbers in your head.
3. Reply with ONLY one JSON object, no markdown fences, with these keys:
   "answer": the final result as a string, rounded to 4 decimals if it is a float,
   "code": the exact Python script you executed,
   "explanation": see the mode-specific rule below.
`;

const MODE_RULES: Record<SolveMode, string> = {
  solve: `Mode: solve. "explanation" is one short sentence naming the method used.`,
  explain: `Mode: explain. "explanation" is a step-by-step walkthrough in Hungarian for a student:
what each step computes and why, including key intermediate results.`,
  check: `Mode: check. The student's own answer is given. Compare it with your computed result
(tolerance 1e-3). "explanation" is in Hungarian: if correct, confirm it; if wrong, give a hint where the
mistake is likely to be WITHOUT revealing the correct number. In check mode, "answer" must be
"correct" or "incorrect", and "code" must still contain the executed script.`,
  fix: `Mode: fix. The given script failed in the student's Colab environment with the given error.
Return a corrected script that avoids the error (the environment may have older or newer library
versions than yours, so use APIs that work across versions). Execute it before answering.
"explanation" is one short Hungarian sentence saying what was wrong.`,
};

export function buildSystemPrompt(mode: SolveMode): string {
  return [
    "You are an exercise solver for a university course.",
    COURSE_CONVENTIONS,
    OUTPUT_CONTRACT,
    MODE_RULES[mode],
  ].join("\n");
}

export function buildUserMessage(input: SolveInput): string {
  const parts = [`Task:\n${input.question.trim()}`];
  if (input.studentAnswer !== undefined) {
    parts.push(`Student's answer: ${input.studentAnswer.trim()}`);
  }
  if (input.code !== undefined) {
    parts.push(`Script that failed:\n${input.code}`);
  }
  if (input.error !== undefined) {
    parts.push(`Error:\n${input.error.trim()}`);
  }
  return parts.join("\n\n");
}
