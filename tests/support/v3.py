from fastapi.testclient import TestClient as BaseClient
from pathlib import Path
import json, re
from jsonschema import Draft202012Validator, FormatChecker

SPEC = json.loads((Path(__file__).resolve().parents[2] / "oko/reader/generated/openapi.json").read_text())

class TestClient(BaseClient):
    __test__ = False
    def request(self, method, url, **kwargs):
        response = super().request(method, url, **kwargs)
        path = str(url).split("?")[0]
        if path.startswith("/api/v3/"):
            route = next(key for key in SPEC["paths"] if re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", key), path))
            content = SPEC["paths"][route][method.lower()]["responses"][str(response.status_code)].get("content", {})
            if "application/json" in response.headers.get("content-type", ""):
                schema = content["application/json"]["schema"]
                Draft202012Validator(dict(schema, components=SPEC["components"]), format_checker=FormatChecker()).validate(response.json())
        return response
