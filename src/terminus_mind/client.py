"""Thin TerminusDB v12 HTTP client.

Speaks the document API, WOQL, and the versioning endpoints (log, history,
time-travel reads, diff) directly over httpx. Every mutation carries an
author and a commit message, because the commit log *is* the memory's
audit trail.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Iterable

import httpx


class TerminusError(RuntimeError):
    def __init__(self, message: str, response: httpx.Response | None = None):
        super().__init__(message)
        self.response = response


@dataclass
class TerminusClient:
    """Connection to one TerminusDB database (team/db)."""

    server: str = field(default_factory=lambda: os.environ.get("TM_SERVER", "http://127.0.0.1:6363"))
    team: str = field(default_factory=lambda: os.environ.get("TM_TEAM", "admin"))
    db: str = field(default_factory=lambda: os.environ.get("TM_DB", "mind"))
    user: str = field(default_factory=lambda: os.environ.get("TM_USER", "admin"))
    password: str = field(default_factory=lambda: os.environ.get("TM_PASS", "root"))
    branch: str = "main"

    def __post_init__(self) -> None:
        self._http = httpx.Client(
            base_url=self.server.rstrip("/"),
            auth=(self.user, self.password),
            timeout=30.0,
        )

    # -- low level -----------------------------------------------------

    def _request(self, method: str, path: str, **kw: Any) -> Any:
        resp = self._http.request(method, path, **kw)
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise TerminusError(f"{method} {path} -> {resp.status_code}: {detail}", resp)
        if not resp.content:
            return None
        ctype = resp.headers.get("content-type", "")
        if "json" in ctype:
            # document GET streams one JSON doc per line unless as_list=true;
            # we always pass as_list, so plain .json() is fine.
            return resp.json()
        return resp.text

    @property
    def _ref(self) -> str:
        """Path segment addressing the current branch."""
        base = f"{self.team}/{self.db}"
        if self.branch and self.branch != "main":
            return f"{base}/local/branch/{self.branch}"
        return base

    # -- database lifecycle ---------------------------------------------

    def db_exists(self) -> bool:
        try:
            self._request("GET", f"/api/db/{self.team}/{self.db}")
            return True
        except TerminusError as e:
            if e.response is not None and e.response.status_code == 404:
                return False
            raise

    def create_db(self, label: str, comment: str = "") -> None:
        self._request(
            "POST",
            f"/api/db/{self.team}/{self.db}",
            json={"label": label, "comment": comment, "schema": True},
        )

    def delete_db(self) -> None:
        self._request("DELETE", f"/api/db/{self.team}/{self.db}")

    # -- documents -------------------------------------------------------

    def insert(
        self,
        docs: Iterable[dict] | dict,
        *,
        author: str,
        message: str,
        graph_type: str = "instance",
        raw_json: bool = False,
    ) -> list[str]:
        """Insert documents; returns their ids."""
        if isinstance(docs, dict):
            docs = [docs]
        params = {
            "graph_type": graph_type,
            "author": author,
            "message": message,
        }
        if raw_json:
            params["raw_json"] = "true"
        out = self._request(
            "POST", f"/api/document/{self._ref}", params=params, json=list(docs)
        )
        return [i.removeprefix("terminusdb:///data/") for i in (out or [])]

    def replace(
        self,
        docs: Iterable[dict] | dict,
        *,
        author: str,
        message: str,
        create: bool = False,
        graph_type: str = "instance",
    ) -> list[str]:
        if isinstance(docs, dict):
            docs = [docs]
        params = {
            "graph_type": graph_type,
            "author": author,
            "message": message,
            "create": str(create).lower(),
        }
        out = self._request(
            "PUT", f"/api/document/{self._ref}", params=params, json=list(docs)
        )
        return [i.removeprefix("terminusdb:///data/") for i in (out or [])]

    def delete(self, doc_id: str, *, author: str, message: str) -> None:
        self._request(
            "DELETE",
            f"/api/document/{self._ref}",
            params={"author": author, "message": message, "id": doc_id},
        )

    def get(self, doc_id: str, *, commit: str | None = None) -> dict | None:
        ref = f"{self.team}/{self.db}/local/commit/{commit}" if commit else self._ref
        try:
            return self._request(
                "GET", f"/api/document/{ref}", params={"id": doc_id}
            )
        except TerminusError as e:
            if e.response is not None and e.response.status_code == 404:
                return None
            raise

    def list_docs(
        self,
        type_: str | None = None,
        *,
        commit: str | None = None,
        graph_type: str = "instance",
        count: int | None = None,
        skip: int = 0,
    ) -> list[dict]:
        ref = f"{self.team}/{self.db}/local/commit/{commit}" if commit else self._ref
        params: dict[str, Any] = {"as_list": "true", "graph_type": graph_type, "skip": skip}
        if type_:
            params["type"] = type_
        if count is not None:
            params["count"] = count
        return self._request("GET", f"/api/document/{ref}", params=params) or []

    def query_template(
        self, type_: str, template: dict, *, count: int | None = None
    ) -> list[dict]:
        """Filter documents of a type by exact-match field template."""
        params: dict[str, Any] = {"as_list": "true"}
        if count is not None:
            params["count"] = count
        body = {"type": type_, "query": template}
        return (
            self._request(
                "POST",
                f"/api/document/{self._ref}",
                params=params,
                headers={"X-HTTP-Method-Override": "GET"},
                json=body,
            )
            or []
        )

    # -- WOQL --------------------------------------------------------------

    def woql(self, query: dict) -> list[dict]:
        """Run a WOQL JSON-LD query; returns bindings."""
        out = self._request(
            "POST", f"/api/woql/{self._ref}", json={"query": query}
        )
        return out.get("bindings", []) if isinstance(out, dict) else []

    # -- versioning / introspection -----------------------------------------

    def log(self, count: int = 10, start: int = 0) -> list[dict]:
        return (
            self._request(
                "GET",
                f"/api/log/{self.team}/{self.db}",
                params={"count": count, "start": start},
            )
            or []
        )

    def history(self, doc_id: str) -> list[dict]:
        """Commits that touched one document — the provenance of a memory."""
        return (
            self._request(
                "GET",
                f"/api/history/{self.team}/{self.db}",
                params={"id": doc_id},
            )
            or []
        )

    def diff_commits(self, before: str, after: str) -> list[dict]:
        return (
            self._request(
                "POST",
                f"/api/diff/{self.team}/{self.db}",
                json={"before_data_version": before, "after_data_version": after},
            )
            or []
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "TerminusClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
