from oscal_pydantic import document, catalog
from oscal_pydantic.core import common
from datetime import datetime
import re
import uuid
from pathlib import Path
import argparse

# This program relies heavily on the specific format of the tokenized CP documents.


def common_policy_to_catalog(common_policy: list[str]) -> document.Document:
    # We will parse the document and store all of the sections as their own list in a nested list
    sections: list[list[str]] = []

    # The first part of the document contains the metadata.
    # It's everything before the first section
    section: list[str] = []

    # Parse the policy into lists
    for line in common_policy:
        if len(line) == 0:
            # Skip blank lines
            continue
        elif line[0] == "#":
            # We've reached a new section. Assign the strings to the current
            # section and reset the list
            sections.append(section.copy())
            section.clear()
            section.append(line)
        else:
            section.append(line)

    # We have now parsed the policy into a list of lists, where each
    # list represents a section of the document.

    # The first list is always the introduction/metadata
    metadata = parse_metadata(sections[0])

    # Ignoring backmatter for now
    backmatter = parse_backmatter([])

    common_catalog = catalog.Catalog(
        uuid=uuid.uuid4(),
        metadata=metadata,
        back_matter=backmatter,
    )

    return document.Document(catalog=common_catalog)


def section_to_group(section_contents: list[str]) -> catalog.Group:
    pass


def section_to_control(section_contents: list[str]) -> catalog.Control:
    pass


def statement_to_part(statement: str) -> catalog.BasePart:
    pass


def parse_metadata(introduction: list[str]) -> common.Metadata:
    version = ""
    published = None
    for line in introduction:
        if line == "":
            # Blank line, ignore and move on
            continue
        elif "<table>" in line:
            # Revision history is maintained in a table - parse it
            # TODO: Parse Revision History
            continue
        elif re.match(r"\[\d.*", line) is not None:
            # This could be a TOC entry - we can derive the section number from this.
            # TODO: Parse TOC
            continue
        elif re.match("Version", line) is not None:
            # Parse out the version number then move on
            version = re.sub(r"Version ", "", line)
            continue
        elif line[0] in "*<[(":
            # First character of the line indicates it's a structural or other
            # metadata line, ignore since we've already parsed the ones we're
            # interested in
            continue
        else:
            try:
                # Try to parse the line as a date
                published = datetime.strptime(line, "%B %d, %Y")
            except ValueError:
                continue

    if version == "" or published is None:
        raise ValueError("Introduction is missing Version and/or Publication Date.")
    else:
        return common.Metadata(
            title="X.509 Certificate Policy for the U.S. Federal PKI Common Policy Framework",
            published=published.isoformat(),
            version=version,
            oscal_version="1.1.2",  # TODO
        )


def parse_backmatter(contents: list[str]) -> common.BackMatter:
    pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="oscal_common_cp",
        description="A program to convert a parsed, tokenized version of Common Policy to an OSCAL catalog.",
    )

    parser.add_argument("filename", help="The filename of the policy to parse.")

    args = parser.parse_args()

    common_file_path = Path(args.filename)

    if common_file_path.exists() and common_file_path.is_file():
        with open(common_file_path) as common_file:
            common_policy_to_catalog(common_file.read().splitlines())
    else:
        print("You provided an argument that does not exist or is not a file.")
        parser.print_help
