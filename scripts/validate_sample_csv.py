"""Validate the checked-in synthetic sample CSV without importing it into a database."""
import csv
from pathlib import Path

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample_assignments.csv"
EXPECTED = {"title", "description", "policy_level", "course"}


def validate(path: Path = SAMPLE) -> int:
    with path.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if set(reader.fieldnames or []) != EXPECTED:
            raise ValueError(f"CSV headers must be: {', '.join(sorted(EXPECTED))}")
        count = 0
        for line, row in enumerate(reader, start=2):
            if not row["title"].strip():
                raise ValueError(f"line {line}: title is required")
            if row["policy_level"].strip().upper() not in {"RED", "YELLOW", "GREEN"}:
                raise ValueError(f"line {line}: invalid policy_level")
            count += 1
    return count


if __name__ == "__main__":
    print(f"Valid sample file: {validate()} records; no database was changed.")
