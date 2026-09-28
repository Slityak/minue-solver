"""HTTP client for the data-mining solver Worker."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

import requests

Mode = Literal["solve", "explain", "check", "fix"]

URL_SETTING = "SOLVER_URL"
TOKEN_SETTING = "SOLVER_TOKEN"
DEFAULT_TIMEOUT_SECONDS = 120


class SolverConfigError(RuntimeError):
    """Raised when the Worker URL or token is not configured."""


class SolverRequestError(RuntimeError):
    """Raised when the Worker returns an error response."""


@dataclass(frozen=True)
class SolveResult:
    answer: str
    code: str
    explanation: str
    stdout: str


def read_setting(name: str) -> str:
    """Read a setting from Colab Secrets first, then from environment variables."""
    try:
        from google.colab import userdata  # type: ignore[import-not-found]

        value = userdata.get(name)
        if value:
            return value
    except Exception:  # Not running in Colab, or the secret is missing / not shared.
        pass

    value = os.environ.get(name, "")
    if not value:
        raise SolverConfigError(
            f"Missing setting '{name}'. Add it in Colab under Secrets (key icon) "
            f"and enable notebook access, or set the {name} environment variable."
        )
    return value


class SolverClient:
    """Sends exercise text to the Worker and returns the parsed result."""

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        session: requests.Session | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._session = session or requests.Session()

    @classmethod
    def from_settings(cls) -> SolverClient:
        return cls(read_setting(URL_SETTING), read_setting(TOKEN_SETTING))

    def health(self) -> dict:
        response = self._session.get(f"{self._base_url}/health", timeout=self._timeout)
        response.raise_for_status()
        return response.json()

    def solve(
        self, question: str, mode: Mode = "solve", student_answer: str | None = None
    ) -> SolveResult:
        payload: dict[str, str] = {"question": question, "mode": mode}
        if student_answer is not None:
            payload["studentAnswer"] = student_answer
        return self._post(payload)

    def fix(self, question: str, code: str, error: str) -> SolveResult:
        """Ask for a corrected script after `code` failed with `error`."""
        return self._post({"question": question, "mode": "fix", "code": code, "error": error})

    def _post(self, payload: dict[str, str]) -> SolveResult:
        response = self._session.post(
            f"{self._base_url}/solve",
            json=payload,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=self._timeout,
        )
        body = _json_or_empty(response)
        if not response.ok:
            detail = body.get("error", response.text[:300])
            raise SolverRequestError(f"Solver returned HTTP {response.status_code}: {detail}")

        return SolveResult(
            answer=str(body.get("answer", "")),
            code=body.get("code", ""),
            explanation=body.get("explanation", ""),
            stdout=body.get("stdout", ""),
        )


def _json_or_empty(response: requests.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
