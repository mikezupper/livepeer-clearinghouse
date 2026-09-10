#!/usr/bin/env python3
"""Fail when HTTP/event contracts or generated component metadata drift."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HTTP_METHODS = frozenset({"delete", "get", "head", "options", "patch", "post", "put", "trace"})


def load_json(path: Path) -> dict[str, Any]:
    """Load JSON while rejecting duplicate object keys."""

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key {key!r} in {path.relative_to(ROOT)}")
            result[key] = value
        return result

    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise TypeError(f"{path.relative_to(ROOT)} must contain a JSON object")
    return value


def referenced_files(document: Any, base: Path) -> set[Path]:
    """Return external files referenced by one JSON contract."""
    result: set[Path] = set()
    if isinstance(document, dict):
        reference = document.get("$ref")
        if isinstance(reference, str) and not reference.startswith("#"):
            result.add((base / reference.split("#", maxsplit=1)[0]).resolve())
        for value in document.values():
            result.update(referenced_files(value, base))
    elif isinstance(document, list):
        for value in document:
            result.update(referenced_files(value, base))
    return result


def generated_openapi() -> dict[str, Any]:
    """Generate OpenAPI from the fully composed runtime without external I/O."""
    from clearinghouse.infrastructure.config import Settings
    from clearinghouse.main import create_app

    return create_app(Settings(environment="test", _env_file=None)).openapi()


def assert_openapi_matches(canonical: dict[str, Any], runtime: dict[str, Any]) -> None:
    """Fail on any semantic runtime drift; object key order is the only normalization."""
    if canonical != runtime:
        raise RuntimeError(
            "runtime OpenAPI differs from contracts/openapi.yaml; "
            "run 'uv run python scripts/check_contract_drift.py --write-openapi' and review it"
        )


def _generic_schema(schema: object) -> bool:
    """Identify schemas that do not bind a useful public response shape."""
    if not isinstance(schema, dict) or not schema:
        return True
    if "$ref" in schema or "oneOf" in schema or "anyOf" in schema:
        return False
    if schema.get("type") == "array":
        return _generic_schema(schema.get("items"))
    return schema.get("type") == "object" and schema.get("additionalProperties") is True


def assert_expressive_openapi(document: dict[str, Any]) -> None:
    """Reject generic success bodies, missing security, and undocumented problem media."""
    paths = document.get("paths")
    if not isinstance(paths, dict):
        raise TypeError("OpenAPI paths must be an object")
    errors: list[str] = []
    for path, item in paths.items():
        if not isinstance(path, str) or not isinstance(item, dict):
            errors.append("path entries must be objects")
            continue
        for method, operation in item.items():
            if method not in HTTP_METHODS:
                continue
            if not isinstance(operation, dict):
                errors.append(f"{method.upper()} {path}: operation must be an object")
                continue
            if "security" not in operation:
                errors.append(f"{method.upper()} {path}: security must be explicit")
            responses = operation.get("responses")
            if not isinstance(responses, dict):
                errors.append(f"{method.upper()} {path}: responses must be an object")
                continue
            if operation.get("security") and "401" not in responses:
                errors.append(f"{method.upper()} {path}: authenticated operation must document 401")
            for status, response in responses.items():
                if not isinstance(status, str) or not isinstance(response, dict):
                    errors.append(f"{method.upper()} {path} {status}: response must be an object")
                    continue
                content = response.get("content", {})
                if status.startswith("2") and status not in {"202", "204"}:
                    if not isinstance(content, dict) or "application/json" not in content:
                        errors.append(f"{method.upper()} {path} {status}: JSON schema is required")
                    elif not isinstance(content["application/json"], dict) or _generic_schema(
                        content["application/json"].get("schema")
                    ):
                        errors.append(
                            f"{method.upper()} {path} {status}: generic schema is forbidden"
                        )
                if status.startswith(("4", "5")):
                    domain_denial = method == "post" and path == "/v1/authorize" and status == "402"
                    media_type = (
                        "application/json"
                        if domain_denial or status == "422"
                        else "application/problem+json"
                    )
                    if not isinstance(content, dict) or media_type not in content:
                        operation_name = f"{method.upper()} {path} {status}"
                        errors.append(f"{operation_name}: {media_type} is required")
                    elif not isinstance(content[media_type], dict) or _generic_schema(
                        content[media_type].get("schema")
                    ):
                        errors.append(
                            f"{method.upper()} {path} {status}: generic error schema is forbidden"
                        )
    if errors:
        raise RuntimeError("OpenAPI is not semantically expressive:\n" + "\n".join(errors))


def write_openapi() -> None:
    """Regenerate the canonical OpenAPI artifact from the composed application."""
    path = ROOT / "contracts" / "openapi.yaml"
    path.write_text(
        json.dumps(generated_openapi(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def check_component_manifest() -> None:
    """Regenerate and compare the committed custom-elements manifest."""
    frontend = ROOT / "frontend"
    package = frontend / "packages" / "ui"
    expected = load_json(package / "custom-elements.json")
    npx = shutil.which("npx")
    if npx is None:
        raise RuntimeError("npx is required to verify generated component metadata")
    with tempfile.TemporaryDirectory(prefix=".ci-cem-", dir=frontend) as directory:
        relative_output = Path(directory).name
        subprocess.run(  # noqa: S603 -- executable and arguments are fixed repository tooling.
            [
                npx,
                "--no-install",
                "custom-elements-manifest",
                "analyze",
                "--litelement",
                "--globs",
                "packages/ui/src/och-app-shell.ts",
                "--outdir",
                relative_output,
            ],
            cwd=frontend,
            check=True,
        )
        generated = load_json(Path(directory) / "custom-elements.json")
    if generated != expected:
        raise RuntimeError("frontend/packages/ui/custom-elements.json is stale; regenerate it")


def check() -> None:
    """Validate canonical files, references, runtime paths, and generated output."""
    openapi_path = ROOT / "contracts" / "openapi.yaml"
    asyncapi_path = ROOT / "contracts" / "asyncapi.yaml"
    openapi = load_json(openapi_path)
    asyncapi = load_json(asyncapi_path)
    if openapi.get("openapi") != "3.1.0":
        raise ValueError("contracts/openapi.yaml must use OpenAPI 3.1.0")
    if asyncapi.get("asyncapi") != "3.0.0":
        raise ValueError("contracts/asyncapi.yaml must use AsyncAPI 3.0.0")
    pending = [(openapi_path, openapi), (asyncapi_path, asyncapi)]
    checked: set[Path] = set()
    while pending:
        path, document = pending.pop()
        checked.add(path)
        for reference in referenced_files(document, path.parent):
            if not reference.is_file():
                relative = reference.relative_to(ROOT)
                raise FileNotFoundError(f"missing contract reference: {relative}")
            if reference not in checked:
                pending.append((reference, load_json(reference)))
    runtime = generated_openapi()
    assert_expressive_openapi(runtime)
    assert_openapi_matches(openapi, runtime)
    check_component_manifest()


def main() -> int:
    """Run the contract drift checks."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--write-openapi", action="store_true", help="regenerate contracts/openapi.yaml"
    )
    arguments = parser.parse_args()
    if arguments.write_openapi:
        write_openapi()
    check()
    print("contract drift: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
