from pathlib import Path
import argparse
from typing import Any

from .simple_oscal_parser import SimpleOscalParser


if __name__ == "__main__":
    arg_parser = argparse.ArgumentParser(
        prog="oscal_common_cp",
        description="A program to convert a parsed, tokenized version of Common Policy to an OSCAL catalog.",
    )

    arg_parser.add_argument(
        "-t",
        "--type",
        dest="parser_type",
        help="Type of parser to use (default: simple)",
        default="simple",
    )
    arg_parser.add_argument("filename", help="The filename of the policy to parse.")

    args = arg_parser.parse_args()

    parser_dict: dict[str, Any] = {}

    parser_dict["simple"] = SimpleOscalParser()

    oscal_parser = parser_dict[args.parser_type]

    common_file_path = Path(args.filename)

    if common_file_path.exists() and common_file_path.is_file():
        with open(common_file_path) as common_file:
            common_catalog = oscal_parser.common_policy_to_catalog(
                common_file.read().splitlines()
            )
    else:
        print("You provided an argument that does not exist or is not a file.")
        arg_parser.print_help
        exit(1)

    # Write the catalog to stdout
    print(common_catalog.model_dump_json())
