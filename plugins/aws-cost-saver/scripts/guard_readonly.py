#!/usr/bin/env python3
"""PreToolUse guard: keep every AWS MCP call read-only.

Reads the hook payload from stdin, extracts the AWS CLI command from
tool_input.cli_command, and denies anything that is not an explicitly
read-only operation. Default-deny: unknown or unparseable commands are
blocked, so new mutating APIs can never slip through.
"""

import json
import shlex
import sys

# Operation-name prefixes that are always read-only in the AWS CLI.
READ_ONLY_PREFIXES = (
    "describe-",
    "get-",
    "list-",
    "lookup-",
    "search-",
    "batch-get-",
    "head-",
)

# Exact read-only operations that don't follow the prefix convention.
READ_ONLY_EXACT = {
    "ls",       # aws s3 ls
    "scan",     # aws dynamodb scan
    "query",    # aws dynamodb query
    "help",
}

# Global CLI options that consume the following token as a value.
VALUE_OPTIONS = {
    "--profile",
    "--region",
    "--output",
    "--endpoint-url",
    "--cli-connect-timeout",
    "--cli-read-timeout",
    "--query",
    "--color",
    "--ca-bundle",
}


def deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"aws-cost-saver read-only guard: {reason}",
        }
    }))
    sys.exit(0)


def allow() -> None:
    sys.exit(0)


def extract_service_operation(command: str) -> tuple[str, str] | None:
    """Return (service, operation) from an AWS CLI command string."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None

    if not tokens or tokens[0] != "aws":
        return None

    positional = []
    i = 1
    while i < len(tokens) and len(positional) < 2:
        token = tokens[i]
        if token.startswith("--"):
            option = token.split("=", 1)[0]
            if option in VALUE_OPTIONS and "=" not in token:
                i += 1  # skip the option's value
        else:
            positional.append(token)
        i += 1

    if len(positional) < 2:
        return None
    return positional[0], positional[1]


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        deny("could not parse hook payload")

    command = (payload.get("tool_input") or {}).get("cli_command", "")
    if not isinstance(command, str) or not command.strip():
        deny("no cli_command found in tool input")

    parsed = extract_service_operation(command)
    if parsed is None:
        deny(f"could not parse AWS CLI command: {command[:120]}")

    service, operation = parsed
    if operation in READ_ONLY_EXACT or operation.startswith(READ_ONLY_PREFIXES):
        allow()

    deny(
        f"'aws {service} {operation}' is not a read-only operation. "
        "This plugin only permits describe-/get-/list-/lookup-/search- style calls."
    )


if __name__ == "__main__":
    main()
