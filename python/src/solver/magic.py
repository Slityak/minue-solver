"""The %%solve cell magic and the %fix line magic."""

from __future__ import annotations

import ast
import sys
import traceback

from IPython.core.magic import Magics, cell_magic, line_magic, magics_class
from IPython.core.magic_arguments import argument, magic_arguments, parse_argstring
from IPython.display import Markdown, display
from IPython.utils.capture import CapturedIO, capture_output

from .client import SolveResult, SolverClient, SolverConfigError, SolverRequestError

DEFAULT_FIX_ATTEMPTS = 2
MAX_ERROR_LENGTH = 3000
UNKNOWN_TASK = "Nincs külön feladatleírás; a feladat a kódból és a kommentjeiből derül ki."


@magics_class
class SolverMagics(Magics):
    """Provides %%solve (task in, short code out) and %fix (repair code that just failed)."""

    def __init__(self, shell, client: SolverClient | None = None) -> None:
        super().__init__(shell)
        self._client = client
        self._last_question: str | None = None
        self._last_code: str | None = None
        self._write_into_cell = True

    def _get_client(self) -> SolverClient:
        if self._client is None:
            self._client = SolverClient.from_settings()
        return self._client

    @magic_arguments()
    @argument("--explain", action="store_true", help="Step-by-step explanation in Hungarian.")
    @argument("--check", metavar="ANSWER", help="Check your own answer and get a hint if it is wrong.")
    @argument("--no-run", action="store_true", help="Do not run the returned code locally.")
    @argument("--keep", action="store_true", help="Keep the cell as is instead of writing the code into it.")
    @argument(
        "--fixes",
        type=int,
        default=DEFAULT_FIX_ATTEMPTS,
        help="How many times a locally failing script is sent back for repair.",
    )
    @cell_magic
    def solve(self, line: str, cell: str) -> None:
        """%%solve [--explain] [--check ANSWER] [--no-run] [--keep] [--fixes N]"""
        args = parse_argstring(self.solve, line)
        question = cell.strip()
        if not question:
            display(Markdown("⚠️ Az üres cellához nincs mit megoldani: írd be a feladatot kommentben a `%%solve` alá."))
            return

        mode = "check" if args.check else "explain" if args.explain else "solve"
        try:
            result = self._get_client().solve(question, mode=mode, student_answer=args.check)
        except (SolverConfigError, SolverRequestError) as error:
            display(Markdown(f"❌ **Hiba:** {error}"))
            return

        if mode == "check":
            display(Markdown(render_check(result)))
            return
        self._last_question = question
        self._write_into_cell = not args.keep
        code = self._show_and_run(question, result, mode, run=not args.no_run, fixes=args.fixes)
        if code and self._write_into_cell:
            self.shell.set_next_input(cell_with_code(question, code), replace=True)

    @line_magic
    def fix(self, line: str) -> None:
        """%fix: send the code of the previous cell (or the last %%solve result) and its error for repair."""
        error = last_error()
        code = self._code_to_fix()
        if error is None or not code:
            display(Markdown("⚠️ Nincs mit javítani: előbb futtass egy cellát, ami hibával elhal, és utána a `%fix`-et."))
            return
        question = line.strip() or self._last_question or UNKNOWN_TASK
        self._last_question = question
        self._write_into_cell = True
        fixed = self._repair_and_run(question, code, error, fixes=DEFAULT_FIX_ATTEMPTS)
        if fixed and fixed != code:
            self.shell.set_next_input(fixed, replace=True)

    def _code_to_fix(self) -> str | None:
        """The previous cell's source, or the last solver script if that cell was a %%solve."""
        history = self.shell.user_ns.get("In", [])
        previous = history[-2].strip() if len(history) >= 2 else ""
        if not previous or previous.startswith(("%%solve", "get_ipython().run_cell_magic('solve'")):
            return self._last_code
        return previous

    def _show_and_run(self, question: str, result: SolveResult, mode: str, run: bool, fixes: int) -> str:
        """Run the code as if it were the cell, repairing it quietly if needed; return the final code.

        Only what the cell itself would print is shown, plus the explanation in explain mode and
        the code when it is not written into the cell.
        """
        self._last_code = result.code
        if mode == "explain" and result.explanation:
            display(Markdown(result.explanation))
        if not self._write_into_cell and result.code:
            display(Markdown(f"```python\n{result.code}\n```"))
        if not run or not result.code:
            return result.code
        captured, error = run_locally(result.code, self.shell.user_ns)
        if error is None:
            captured.show()
            return result.code
        return self._repair_and_run(question, result.code, error, fixes)

    def _repair_and_run(self, question: str, code: str, error: str, fixes: int) -> str:
        """Ask for fixes until the code runs or the attempts run out; return the last code.

        Failed attempts stay silent; if every attempt fails, the last traceback is shown
        the way a failing cell would show it.
        """
        for _ in range(fixes):
            try:
                result = self._get_client().fix(question, code, error)
            except (SolverConfigError, SolverRequestError) as request_error:
                display(Markdown(f"❌ **Hiba:** {request_error}"))
                return code
            self._last_code = code = result.code
            captured, error = run_locally(code, self.shell.user_ns)
            if error is None:
                captured.show()
                return code
        print(error, file=sys.stderr)
        return code


def render_check(result: SolveResult) -> str:
    """The verdict shown for --check; the code is never shown so the answer is not leaked."""
    verdict = "✅ Helyes!" if result.answer.strip().lower() == "correct" else "❌ Nem egészen."
    return "\n\n".join(part for part in (f"### {verdict}", result.explanation) if part)


def cell_with_code(question: str, code: str) -> str:
    """The new cell source: the task as comments, then the code, without the %%solve line."""
    comments = [line if line.lstrip().startswith("#") or not line.strip() else f"# {line}" for line in question.splitlines()]
    return "\n".join(comments).rstrip() + "\n\n" + code.strip() + "\n"


def run_locally(code: str, namespace: dict) -> tuple[CapturedIO, str | None]:
    """Run code like a notebook cell in `namespace`; return its captured output and traceback or None.

    Output is captured so a failed attempt shows nothing; a trailing expression is displayed
    like a cell's Out value. The code comes from our own Worker, which is the trust boundary
    that makes exec acceptable here.
    """
    with capture_output() as captured:
        try:
            tree = ast.parse(code)
            last = tree.body.pop() if tree.body and isinstance(tree.body[-1], ast.Expr) else None
            exec(compile(tree, "<solver>", "exec"), namespace)
            if last is not None:
                value = eval(compile(ast.Expression(last.value), "<solver>", "eval"), namespace)
                if value is not None:
                    display(value)
        except Exception as error:  # Report any failure so it can be sent back for repair.
            frames = error.__traceback__.tb_next if error.__traceback__ else None
            text = "".join(traceback.format_exception(type(error), error, frames))
            return captured, text.strip()[-MAX_ERROR_LENGTH:]
    return captured, None


def last_error() -> str | None:
    """The traceback of the most recent failed cell, as IPython records it in sys.last_*."""
    error = getattr(sys, "last_value", None)
    if error is None:
        return None
    text = "".join(traceback.format_exception(type(error), error, getattr(sys, "last_traceback", None)))
    return text.strip()[-MAX_ERROR_LENGTH:]
