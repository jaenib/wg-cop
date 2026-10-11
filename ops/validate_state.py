"""Offline structural check for whole-store restoration; never writes state."""
import argparse
import json
from pathlib import Path


def _invalid_constant(value):
    raise ValueError(f"Non-finite JSON number: {value}")


def read_state(path):
    return json.loads(Path(path).read_text(), parse_constant=_invalid_constant)


def _validate_household(home):
    fields = {"members": list, "expenses": list, "chores": dict, "penalties": dict}
    if not isinstance(home, dict) or any(not isinstance(home.get(k), t) for k, t in fields.items()):
        raise ValueError("Expected members, expenses, chores and penalties")


def validate_state(data):
    if not isinstance(data, dict):
        raise ValueError("State must be a JSON object")
    if "schema_version" not in data:
        _validate_household(data)
        return 1
    if data["schema_version"] != 2:
        raise ValueError("Unsupported state schema")
    if not isinstance(data.get("homes"), dict) or not isinstance(data.get("selections"), dict):
        raise ValueError("Expected homes and selections in the whole-store backup")
    for hid, home in data["homes"].items():
        _validate_household(home)
        if str(home.get("id")) != hid:
            raise ValueError("Household identity mismatch")
        if not isinstance(home.get("roles"), dict) or not home["roles"].get("admins"):
            raise ValueError("Household must retain administrators")
    return 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--same-format-as", type=Path)
    args = parser.parse_args()
    try:
        schema = validate_state(read_state(args.file))
        if args.same_format_as is not None:
            current = validate_state(read_state(args.same_format_as))
            if schema != current:
                raise ValueError("Restore schema differs from live state; use explicit offline migration/recovery")
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(1, f"State validation failed: {exc}\n")


if __name__ == "__main__":
    main()
