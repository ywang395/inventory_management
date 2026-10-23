"""Write station/.env for the scanner station: the API URL and door-1's API key.

Run from the repo root, after `aws login`:

    uv run python scripts/station_env.py

The key goes into the file (git-ignored, readable only by you) and is never printed.
"""

import sys
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from seed import STACK_NAME, stack_outputs

ENV_FILE = Path(__file__).resolve().parents[1] / "station" / ".env"


def env_text(api_url: str, api_key: str) -> str:
    return (
        "# Written by scripts/station_env.py. Contains a secret: don't commit or share it.\n"
        f"API_URL={api_url}\n"
        f"API_KEY={api_key}\n"
    )


def main() -> int:
    try:
        outputs = stack_outputs(boto3.client("cloudformation"), STACK_NAME)
        key = boto3.client("apigateway").get_api_key(
            apiKey=outputs["Door1ApiKeyId"], includeValue=True
        )
    except (ClientError, BotoCoreError) as error:
        print(f"AWS error: {error}\nIs your `aws login` still valid?", file=sys.stderr)
        return 1

    ENV_FILE.write_text(env_text(outputs["ApiUrl"], key["value"]))
    ENV_FILE.chmod(0o600)  # only your user can read it
    print(f"Wrote {ENV_FILE} (station door-1, key not shown).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
