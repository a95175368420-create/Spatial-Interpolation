"""Command-line entry points for batch validation, exports and the UI."""
import argparse
import json
from pathlib import Path
import sys

from .config import load_config
from .io import load_dataset


def main(argv=None):
    parser = argparse.ArgumentParser(prog="spatial-monitor")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ["validate", "export"]:
        cmd = sub.add_parser(command)
        cmd.add_argument("source", help="CSV folder or Excel workbook")
        cmd.add_argument("--config", help="YAML column mappings and parameter settings")
        if command == "export":
            cmd.add_argument("--output", required=True, help="New or empty output directory")
            cmd.add_argument("--max-maps", type=int, default=200)
            cmd.add_argument("--zip", action="store_true")
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8501)
    serve.add_argument("--address", default="127.0.0.1")
    api = sub.add_parser("api", help="Start the authenticated HTTP API")
    api.add_argument("--port", type=int, default=8000)
    api.add_argument("--address", default="127.0.0.1")
    api.add_argument("--keys-file", help="Private JSON object mapping client names to keys")
    keys = sub.add_parser("api-keys", help="Create per-client keys in a private file; never print keys")
    keys.add_argument("--client", action="append", required=True)
    keys.add_argument("--output", default=".api-keys.json")
    args = parser.parse_args(argv)
    if args.command == "api-keys":
        import os
        import re
        import secrets
        if len(set(args.client)) != len(args.client) or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) for name in args.client):
            parser.error("Client names must be unique and contain only letters, digits, underscores or hyphens")
        try:
            descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as handle:
                json.dump({name: secrets.token_urlsafe(32) for name in args.client}, handle)
            print(f"Private API keys saved to {args.output}; share each key securely with its designated client.")
            return 0
        except OSError as exc:
            print(f"Cannot create key file: {exc}", file=sys.stderr)
            return 2
    if args.command == "api":
        try:
            import uvicorn
            from .api import create_app
            api_keys = json.loads(Path(args.keys_file).read_text()) if args.keys_file else None
            application = create_app(api_keys=api_keys)
        except (OSError, ValueError) as exc:
            print(f"Cannot start API: {exc}", file=sys.stderr)
            return 2
        uvicorn.run(application, host=args.address, port=args.port, workers=1, log_level="info")
        return 0
    if args.command == "serve":
        from streamlit.web import cli
        sys.argv = ["streamlit", "run", str(Path(__file__).with_name("app.py")),
                    "--server.port", str(args.port), "--server.address", args.address,
                    "--server.headless", "true", "--browser.gatherUsageStats", "false"]
        return cli.main()
    try:
        config = load_config(args.config)
        dataset = load_dataset(args.source, config)
        summary = {"observations": len(dataset.observations), "valid": int(dataset.observations.valid.sum()),
                   "issues": len(dataset.issues), "errors": int(dataset.issues.severity.eq("error").sum())}
        if args.command == "export":
            from .export import export_dataset, zip_outputs
            summary = export_dataset(dataset, config, args.output, max_maps=args.max_maps)
            if args.zip:
                target = Path(str(args.output).rstrip("/") + ".zip")
                if target.exists():
                    raise ValueError(f"Archive already exists: {target}")
                target.write_bytes(zip_outputs(args.output))
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1 if args.command == "validate" and summary["errors"] else 0
    except (ValueError, OSError, KeyError) as exc:
        print(f"spatial-monitor: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
