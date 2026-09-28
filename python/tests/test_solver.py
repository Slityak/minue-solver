from unittest.mock import MagicMock

import pytest
import requests
from IPython.core.interactiveshell import InteractiveShell

from solver import SolverClient, SolverRequestError, load_ipython_extension
from solver.client import SolveResult, SolverConfigError, read_setting
from solver.magic import SolverMagics, cell_with_code, render, run_locally

MANHATTAN_CODE = (
    "import numpy as np\n"
    "X = np.array([[-7,-7,7,-1],[5,-5,5,7],[9,-4,7,-10]])\n"
    "print(int(np.abs(X[2] - X[0]).sum()))"
)


def fake_response(status: int, body: dict) -> MagicMock:
    response = MagicMock(spec=requests.Response)
    response.status_code = status
    response.ok = status < 400
    response.json.return_value = body
    response.text = str(body)
    return response


def client_returning(status: int, *bodies: dict) -> tuple[SolverClient, MagicMock]:
    session = MagicMock(spec=requests.Session)
    session.post.side_effect = [fake_response(status, body) for body in bodies]
    return SolverClient("https://solver.test/", "tok", session=session), session


class TestClient:
    def test_sends_question_mode_and_bearer_token(self):
        client, session = client_returning(200, {"answer": "28", "code": "c", "explanation": "e", "stdout": "28"})

        result = client.solve("feladat", mode="check", student_answer="27")

        assert result == SolveResult(answer="28", code="c", explanation="e", stdout="28")
        url = session.post.call_args.args[0]
        kwargs = session.post.call_args.kwargs
        assert url == "https://solver.test/solve"
        assert kwargs["json"] == {"question": "feladat", "mode": "check", "studentAnswer": "27"}
        assert kwargs["headers"] == {"Authorization": "Bearer tok"}

    def test_fix_sends_code_and_error(self):
        client, session = client_returning(200, {"answer": "28", "code": "c2"})

        client.fix("feladat", "c1", "NameError")

        assert session.post.call_args.kwargs["json"] == {
            "question": "feladat",
            "mode": "fix",
            "code": "c1",
            "error": "NameError",
        }

    def test_raises_with_worker_error_message(self):
        client, _ = client_returning(401, {"error": "Unauthorized"})
        with pytest.raises(SolverRequestError, match="401: Unauthorized"):
            client.solve("x")

    def test_missing_setting_has_actionable_message(self, monkeypatch):
        monkeypatch.delenv("SOLVER_URL", raising=False)
        with pytest.raises(SolverConfigError, match="Secrets"):
            read_setting("SOLVER_URL")


class TestRendering:
    def test_solve_mode_shows_answer_and_code_without_explanation(self):
        markdown = render(SolveResult("0.5663", "print(1)", "Cholesky whitening.", ""), "solve")
        assert "Eredmény: `0.5663`" in markdown
        assert "```python\nprint(1)\n```" in markdown
        assert "Cholesky" not in markdown

    def test_check_mode_hides_code_so_the_answer_is_not_leaked(self):
        markdown = render(SolveResult("incorrect", "print(28)", "Nézd meg az indexelést.", ""), "check")
        assert "Nem egészen" in markdown
        assert "print(28)" not in markdown

    def test_collapsed_code_stays_available_as_fallback(self):
        markdown = render(SolveResult("28", "print(28)", "", ""), "solve", collapse_code=True)
        assert "<details>" in markdown and "print(28)" in markdown

    def test_cell_with_code_keeps_task_as_comments_and_drops_magic(self):
        source = cell_with_code("# Adott X...\nMi a távolság?", "print(28)\n")
        assert source == "# Adott X...\n# Mi a távolság?\nprint(28)\n"

    def test_run_locally_captures_stdout(self):
        assert run_locally(MANHATTAN_CODE) == ("28", None)

    def test_run_locally_returns_traceback_instead_of_raising(self):
        output, error = run_locally("print(1)\nprint(1/0)")
        assert output == "1"
        assert "ZeroDivisionError" in error and "line 2" in error


class TestMagicInIPython:
    @pytest.fixture
    def shell(self):
        ip = InteractiveShell.instance()
        load_ipython_extension(ip)
        return ip

    def test_extension_registers_cell_magic(self, shell):
        assert "solve" in shell.magics_manager.magics["cell"]

    @pytest.fixture
    def written(self, shell, monkeypatch) -> list[tuple[str, bool]]:
        written: list[tuple[str, bool]] = []
        monkeypatch.setattr(shell, "set_next_input", lambda text, replace=False: written.append((text, replace)))
        return written

    @pytest.fixture
    def shown(self, monkeypatch) -> list[str]:
        shown: list[str] = []
        monkeypatch.setattr("solver.magic.display", lambda obj: shown.append(obj.data))
        return shown

    def test_solution_is_written_into_the_cell_under_the_task(self, shell, shown, written):
        client, _ = client_returning(200, {"answer": "28", "code": MANHATTAN_CODE})
        shell.register_magics(SolverMagics(shell, client=client))

        shell.run_cell_magic("solve", "", "# Manhattan távolság?\n")

        assert written == [("# Manhattan távolság?\n" + MANHATTAN_CODE + "\n", True)]

    def test_keep_flag_leaves_the_cell_alone(self, shell, shown, written):
        client, _ = client_returning(200, {"answer": "28", "code": MANHATTAN_CODE})
        shell.register_magics(SolverMagics(shell, client=client))

        shell.run_cell_magic("solve", "--keep", "# feladat")

        assert written == []
        assert "<details>" not in "\n".join(shown)

    def test_check_mode_never_writes_the_solution_into_the_cell(self, shell, shown, written):
        client, _ = client_returning(200, {"answer": "incorrect", "code": "print(28)", "explanation": "Tipp."})
        shell.register_magics(SolverMagics(shell, client=client))

        shell.run_cell_magic("solve", "--check 27", "# feladat")

        assert written == []

    def test_magic_solves_and_runs_locally(self, shell, shown):
        client, session = client_returning(200, {"answer": "28", "code": MANHATTAN_CODE, "stdout": "28"})
        shell.register_magics(SolverMagics(shell, client=client))

        shell.run_cell_magic("solve", "", "# Mi a Manhattan távolság a 2. és 0. sor között?")

        assert session.post.call_args.kwargs["json"]["mode"] == "solve"
        output = "\n".join(shown)
        assert "Eredmény: `28`" in output
        assert "Helyi futtatás:**\n```\n28\n```" in output

    def test_failing_code_is_sent_back_for_repair(self, shell, shown, written):
        client, session = client_returning(
            200,
            {"answer": "28", "code": "print(undefined_name)"},
            {"answer": "28", "code": MANHATTAN_CODE, "explanation": "Hiányzó változó."},
        )
        shell.register_magics(SolverMagics(shell, client=client))

        shell.run_cell_magic("solve", "", "feladat")

        fix_request = session.post.call_args_list[1].kwargs["json"]
        assert fix_request["mode"] == "fix" and fix_request["question"] == "feladat"
        assert fix_request["code"] == "print(undefined_name)"
        assert "NameError" in fix_request["error"]
        output = "\n".join(shown)
        assert "elhalt" in output and "Hiányzó változó." in output
        assert output.rstrip().endswith("28\n```")
        assert written[0][0].endswith(MANHATTAN_CODE + "\n")

    def test_repair_gives_up_after_the_attempt_limit(self, shell, shown):
        broken = {"answer": "?", "code": "print(1/0)"}
        client, session = client_returning(200, broken, broken)
        shell.register_magics(SolverMagics(shell, client=client))

        shell.run_cell_magic("solve", "--fixes 1", "feladat")

        assert session.post.call_count == 2
        assert "is elhalt" in shown[-1]

    def test_no_run_skips_local_execution(self, shell, shown):
        client, session = client_returning(200, {"answer": "?", "code": "print(1/0)"})
        shell.register_magics(SolverMagics(shell, client=client))

        shell.run_cell_magic("solve", "--no-run", "feladat")

        assert session.post.call_count == 1
        assert not any("elhalt" in text for text in shown)

    def test_fix_magic_repairs_the_previous_failing_cell(self, shell, shown, written):
        client, session = client_returning(200, {"answer": "28", "code": MANHATTAN_CODE})
        shell.register_magics(SolverMagics(shell, client=client))

        shell.run_cell("import numpy as np\nprint(np.nonexistent(1))", store_history=True)
        shell.run_cell("%fix", store_history=True)

        sent = session.post.call_args.kwargs["json"]
        assert sent["mode"] == "fix"
        assert sent["code"] == "import numpy as np\nprint(np.nonexistent(1))"
        assert "nonexistent" in sent["error"]
        assert "28" in shown[-1]
        assert written == [(MANHATTAN_CODE, True)]

    def test_check_flag_selects_check_mode(self, shell):
        client, session = client_returning(200, {"answer": "correct", "code": "", "explanation": "Jó!"})
        shell.register_magics(SolverMagics(shell, client=client))

        shell.run_cell_magic("solve", "--check 28", "feladat")

        sent = session.post.call_args.kwargs["json"]
        assert sent["mode"] == "check" and sent["studentAnswer"] == "28"

    def test_empty_cell_does_not_call_worker(self, shell):
        client, session = client_returning(200)
        shell.register_magics(SolverMagics(shell, client=client))

        shell.run_cell_magic("solve", "", "   ")

        session.post.assert_not_called()
