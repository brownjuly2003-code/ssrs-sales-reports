import argparse
from pathlib import Path

from .contracts import check_pbip, check_rdl
from .data import generate
from .pbip import write_project
from .reference import reference


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    data = commands.add_parser("generate")
    data.add_argument("--seed", type=int, default=39)
    for cmd in [data, commands.add_parser("reference")]:
        cmd.add_argument("--data", type=Path, default=Path("data"))
    commands.choices["reference"].add_argument(
        "--output", type=Path, default=Path("reports/reference.json")
    )
    commands.add_parser("check-rdl")
    for cmd in [commands.add_parser("build-pbip"), commands.add_parser("check-pbip")]:
        cmd.add_argument("--project", type=Path, default=Path("powerbi"))
    commands.choices["check-pbip"].add_argument("--data", type=Path, default=Path("data"))
    commands.add_parser("write-dax-check").add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    if args.command == "generate":
        generate(args.data, args.seed)
    elif args.command == "reference":
        print({k: v["total"] for k, v in reference(args.data, args.output).items()})
    elif args.command == "check-rdl":
        print("RDL datasets verified:", check_rdl(Path("rdl/SalesStock.rdl"), Path("sql")))
    elif args.command == "build-pbip":
        write_project(args.project)
    elif args.command == "check-pbip":
        print("PBIR visuals verified:", check_pbip(args.project, args.data))
    elif args.command == "write-dax-check":
        from .dax_check import write_query

        print(write_query(args.root))


if __name__ == "__main__":
    main()
