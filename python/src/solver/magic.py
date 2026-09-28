"""The %%solve cell magic and the %fix line magic."""

from __future__ import annotations

import contextlib
import io
import sys
import traceback

from IPython.core.magic import Magics, cell_magic, line_magic, magics_class
from IPython.core.magic_arguments import argument, magic_arguments, parse_argstring
from IPython.display import Markdown, display

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
            display(Markdown(render(result, mode)))
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
        """Show the result, run it locally, repair it if needed; return the final code."""
        self._last_code = result.code
        display(Markdown(render(result, mode, collapse_code=self._write_into_cell)))
        if not run or not result.code:
            return result.code
        output, error = run_locally(result.code)
        if error is None:
            display(Markdown(render_output(output)))
            return result.code
        display(Markdown(render_failure(output, error)))
        return self._repair_and_run(question, result.code, error, fixes)

    def _repair_and_run(self, question: str, code: str, error: str, fixes: int) -> str:
        """Ask for fixes until the code runs locally or the attempts run out; return the last code."""
        for attempt in range(1, fixes + 1):
            display(Markdown(f"🔧 Javítás kérése ({attempt}/{fixes})…"))
            try:
                result = self._get_client().fix(question, code, error)
            except (SolverConfigError, SolverRequestError) as request_error:
                display(Markdown(f"❌ **Hiba:** {request_error}"))
                return code
            self._last_code = result.code
            display(Markdown(render(result, "fix", collapse_code=self._write_into_cell)))
            output, error = run_locally(result.code)
            if error is None:
                display(Markdown(render_output(output)))
                return result.code
            display(Markdown(render_failure(output, error)))
            code = result.code
        display(Markdown("❌ A javított kód is elhalt. Futtasd újra a `%fix`-et, vagy pontosítsd a feladatot."))
        return code


def render(result: SolveResult, mode: str, collapse_code: bool = False) -> str:
    """Build the Markdown shown under the cell."""
    if mode == "check":
        verdict = "✅ Helyes!" if result.answer.strip().lower() == "correct" else "❌ Nem egészen."
        return "\n\n".join(part for part in (f"### {verdict}", result.explanation) if part)

    sections = [f"### Eredmény: `{result.answer}`"]
    if mode in ("explain", "fix") and result.explanation:
        sections.append(result.explanation)
    if result.code:
        block = f"```python\n{result.code}\n```"
        if collapse_code:
            block = f"<details><summary>Kód (a cellába is beírva)</summary>\n\n{block}\n</details>"
        sections.append(block)
    return "\n\n".join(sections)


def cell_with_code(question: str, code: str) -> str:
    """The new cell source: the task as comments, then the code, without the %%solve line."""
    comments = [line if line.lstrip().startswith("#") or not line.strip() else f"# {line}" for line in question.splitlines()]
    return "\n".join(comments).rstrip() + "\n" + code.strip() + "\n"


def render_output(output: str) -> str:
    return f"**Helyi futtatás:**\n```\n{output or '(nincs kimenet)'}\n```"


def render_failure(output: str, error: str) -> str:
    printed = f"{output}\n" if output else ""
    return f"⚠️ **A kód helyben elhalt:**\n```\n{printed}{error}\n```"


def run_locally(code: str) -> tuple[str, str | None]:
    """Execute the solver's code in an isolated namespace; return (stdout, traceback or None).

    The code comes from our own Worker, which is the trust boundary that makes exec acceptable here.
    """
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            exec(compile(code, "<solver>", "exec"), {"__name__": "__solver__"})
    except Exception as error:  # Report any failure so it can be sent back for repair.
        frames = error.__traceback__.tb_next if error.__traceback__ else None
        text = "".join(traceback.format_exception(type(error), error, frames))
        return buffer.getvalue().strip(), text.strip()[-MAX_ERROR_LENGTH:]
    return buffer.getvalue().strip(), None


def last_error() -> str | None:
    """The traceback of the most recent failed cell, as IPython records it in sys.last_*."""
    error = getattr(sys, "last_value", None)
    if error is None:
        return None
    text = "".join(traceback.format_exception(type(error), error, getattr(sys, "last_traceback", None)))
    return text.strip()[-MAX_ERROR_LENGTH:]
