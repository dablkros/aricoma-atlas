#!/usr/bin/env python3
"""Run the Cisco Support API diagnostic through the Atlas backend."""

from pathlib import Path
import sys

import requests


API_URL = "http://127.0.0.1:8081/api/integrations/cisco/test"
OPERATOR_TOKEN_FILE = Path(".runtime/monitoring/operator-token")
EXPECTED_FIELDS = ("oauth", "software_suggestion", "eox", "overall")


def load_operator_token(path: Path = OPERATOR_TOKEN_FILE) -> str:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        raise RuntimeError("Atlas operator token is unavailable") from None
    if len(token) < 32 or any(character.isspace() for character in token):
        raise RuntimeError("Atlas operator token is invalid")
    return token


def request_test(token: str) -> dict:
    try:
        response = requests.post(
            API_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=(3, 60),
        )
    except requests.RequestException:
        raise RuntimeError("Atlas backend is unavailable") from None
    try:
        body = response.json()
    except ValueError:
        raise RuntimeError("Atlas backend returned invalid data") from None
    if not response.ok:
        error = body.get("error") if isinstance(body, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        raise RuntimeError(str(code or "cisco_integration_test_failed"))
    if not isinstance(body, dict) or any(
        not isinstance(body.get(field), str) for field in EXPECTED_FIELDS
    ):
        raise RuntimeError("Atlas backend returned invalid data")
    return body


def main() -> int:
    body = request_test(load_operator_token())
    for field in EXPECTED_FIELDS:
        print(f"{field}: {body[field]}")
    return 0 if body["overall"] == "READY" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
