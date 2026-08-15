"""One-off invocation shell; downstream graph wiring supplies actual execution."""

import argparse
from pathlib import Path

from contracts.models import Bank, ParseRequest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parse one credit-card statement")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--bank", choices=[b.value for b in Bank], required=True)
    parser.add_argument("--request-id", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    request = ParseRequest(args.pdf, args.pdf.name, Bank(args.bank), args.request_id)
    raise NotImplementedError(f"workstream 2 must wire graph execution for {request.request_id}")


if __name__ == "__main__":
    main()
