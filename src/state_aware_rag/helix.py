from __future__ import annotations

import json
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class HelixConfig:
    base_url: str = "http://localhost:6969"
    timeout_seconds: float = 15.0
    query_auth_header: str | None = None
    query_auth_value: str | None = None


class HelixHttpClient:
    """Python 側は Rust 製 HelixDB を HTTP 経由で扱う。"""

    def __init__(self, config: HelixConfig | None = None) -> None:
        self.config = config or HelixConfig()

    def query(self, request_body: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.config.base_url.rstrip('/')}/v1/query",
            data=payload,
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError(f"HelixDB query failed: {exc}") from exc

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self.config.query_auth_header and self.config.query_auth_value:
            headers[self.config.query_auth_header] = self.config.query_auth_value
        return headers


def helix_startup_hint() -> str:
    return (
        "Start HelixDB before using the default backend. "
        "Windows: .\\.tools\\helix.exe start dev --port 6969 --persist. "
        "Linux/macOS: cd helix_project && ../.tools/helix start --port 6969 dev. "
        "Use --backend sqlite for local development without HelixDB."
    )


class HelixTypeScriptQueryBuilder:
    """TypeScript SDK を Python から sidecar として呼び、動的クエリ JSON を作る。"""

    def __init__(self, sdk_dir: str | Path | None = None) -> None:
        root = Path(__file__).resolve().parents[2]
        self.sdk_dir = Path(sdk_dir) if sdk_dir else root / "vendor" / "helix-db" / "sdks" / "typescript"

    def build(self, expression: str) -> dict[str, Any]:
        if not self.sdk_dir.exists():
            raise RuntimeError("Helix TypeScript SDK directory not found")
        script = self._script(expression)
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", encoding="utf-8", delete=False, dir=self.sdk_dir) as file:
            file.write(script)
            script_path = Path(file.name)
        try:
            completed = subprocess.run(
                ["node", str(script_path)],
                cwd=self.sdk_dir,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Helix TypeScript query build failed: {exc.stderr}") from exc
        finally:
            try:
                script_path.unlink()
            except OSError:
                pass
        return json.loads(completed.stdout)

    def build_with_values(self, expression: str, params_source: str, values: dict[str, Any]) -> dict[str, Any]:
        if not self.sdk_dir.exists():
            raise RuntimeError("Helix TypeScript SDK directory not found")
        script = self._script_with_values(expression, params_source, values)
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", encoding="utf-8", delete=False, dir=self.sdk_dir) as file:
            file.write(script)
            script_path = Path(file.name)
        try:
            completed = subprocess.run(
                ["node", str(script_path)],
                cwd=self.sdk_dir,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Helix TypeScript query build failed: {exc.stderr}") from exc
        finally:
            try:
                script_path.unlink()
            except OSError:
                pass
        return json.loads(completed.stdout)

    def _script(self, expression: str) -> str:
        module_path = "./dist/index.js" if (self.sdk_dir / "dist" / "index.js").exists() else "./src/index.ts"
        return f"""
import * as helix from '{module_path}';
const {{
  g, readBatch, writeBatch, Predicate, PropertyInput, PropertyProjection, NodeRef,
  defineParams, param
}} = helix;
const query = ({expression});
if (!query || typeof query.toDynamicJson !== 'function') {{
  console.error('The expression must return a Helix readBatch()/writeBatch() builder.');
  process.exit(1);
}}
process.stdout.write(query.toDynamicJson());
"""

    def _script_with_values(self, expression: str, params_source: str, values: dict[str, Any]) -> str:
        module_path = "./dist/index.js" if (self.sdk_dir / "dist" / "index.js").exists() else "./src/index.ts"
        values_json = json.dumps(values, ensure_ascii=False)
        return f"""
import * as helix from '{module_path}';
const {{
  g, readBatch, writeBatch, Predicate, PropertyInput, PropertyProjection, NodeRef,
  defineParams, param
}} = helix;
const params = ({params_source});
const values = {values_json};
const query = ({expression});
if (!query || typeof query.toDynamicJson !== 'function') {{
  console.error('The expression must return a Helix readBatch()/writeBatch() builder.');
  process.exit(1);
}}
process.stdout.write(query.toDynamicJson(params, values));
"""


def extract_returned_rows(response: dict[str, Any], variable_name: str) -> list[dict[str, Any]]:
    # HelixDB の /v1/query は projection 結果を {<var>: {"properties": [...]}} で返す。
    # テスト用 stub は {<var>: [...]} のフラットなリスト形式も使うため、両方をここで吸収する。
    value = response.get(variable_name)
    rows = _coerce_rows(value)
    if rows is not None:
        return rows
    if isinstance(response.get("data"), dict):
        rows = _coerce_rows(response["data"].get(variable_name))
        if rows is not None:
            return rows
    if isinstance(response.get("results"), dict):
        rows = _coerce_rows(response["results"].get(variable_name))
        if rows is not None:
            return rows
    for item in response.values():
        if isinstance(item, dict):
            rows = _coerce_rows(item.get(variable_name))
            if rows is not None:
                return rows
    return []


def _coerce_rows(value: Any) -> list[dict[str, Any]] | None:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, list):
            return [row for row in properties if isinstance(row, dict)]
    return None
