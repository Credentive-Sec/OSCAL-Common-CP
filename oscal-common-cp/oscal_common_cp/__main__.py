from oscal_pydantic import document, catalog
from oscal_pydantic.core import common
from datetime import datetime
import re
import uuid
from pathlib import Path
import argparse
from html.parser import HTMLParser

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

    # We keep a stack to represent the current place in the TOC
    parent_stack: list[catalog.Group] = []

    section_groups: list[catalog.Group] = []

    # Step through the sections and generate the appropriate OSCAL objects.
    for section in sections[1:]:
        # The first line has the section title, and because it is MD,
        # the number of hashes indicate the depth in the TOC
        header_hashes = re.match(r"#+", section[0])
        if header_hashes is not None:
            section_depth = len(header_hashes.group(0))
            current_group = section_to_group(section)
            if current_group.title.strip() == "":
                # Sometimes we get junk headers in the Markdown
                continue
            if section_depth == 1:
                if len(parent_stack) == 0:
                    # e.g. this is the first run through the loop
                    parent_stack = [current_group]
                else:
                    parent_stack[0] = current_group
                # Add the top-level group to the final list of groups
                section_groups.append(parent_stack[0])
            else:
                # Careful! If we jump more than one level at a time, bad things could happen
                if section_depth > len(parent_stack):
                    parent_stack.append(current_group)
                    # Add current group to the section above
                    add_subsection_to_parent(
                        parent_stack[section_depth - 2], current_group
                    )
                elif section_depth == len(parent_stack):
                    # Replace the previous TOC leaf node with current
                    parent_stack[section_depth - 1] = current_group
                    add_subsection_to_parent(
                        parent_stack[section_depth - 2], current_group
                    )
                elif section_depth < len(parent_stack):
                    # Trim the stack
                    parent_stack = parent_stack[:section_depth]
                    # Replace TOC leaf node with current
                    parent_stack[section_depth - 1] = current_group
                    add_subsection_to_parent(
                        parent_stack[section_depth - 2], current_group
                    )
        else:
            raise Exception("Section does not have a title")

    # Ignoring backmatter for now
    backmatter = parse_backmatter([])

    common_catalog = catalog.Catalog(
        uuid=uuid.uuid4(),
        metadata=metadata,
        groups=section_groups,
        back_matter=backmatter,
    )

    return document.Document(catalog=common_catalog)


def add_subsection_to_parent(
    parent: catalog.Group, child: catalog.Group
) -> catalog.Group:
    if parent.groups is None:
        parent.groups = [child]
    else:
        parent.groups.append(child)

    return parent


def section_to_group(section_contents: list[str]) -> catalog.Group:
    # Strip off the leading hashes and the trailing space
    group_title = re.sub("#+ ", "", section_contents[0])

    section_group = catalog.Group(
        id=group_title.lower().replace(
            " ", "-"
        ),  # "Section Title" becomes "section-title"
        title=group_title,
    )

    if len(section_contents) > 1:
        # The section contains requirements, and must have a control
        section_control_list: list[catalog.Control] | None = [
            section_to_control(title=group_title, section_contents=section_contents)
        ]
    else:
        section_control_list = None

    section_group.controls = section_control_list

    return section_group


def section_to_control(title: str, section_contents: list[str]) -> catalog.Control:
    control = catalog.Control(
        id=title.lower().replace(" ", "-"),  # "Section Title" becomes "section-title",
        title=title,
        parts=[],
    )

    parts: list[catalog.BasePart] = []
    for part_num, part in enumerate(section_contents[1:]):
        parts.append(
            catalog.StatementPart(
                id=f"{id}-{part_num}",
                name="statement",
                prose=part,
            )
        )

    control.parts = parts

    return control


def parse_metadata(introduction: list[str]) -> common.Metadata:
    version = ""
    published = None
    for line_number, line in enumerate(introduction):
        if line == "":
            # Blank line, ignore and move on
            continue
        elif "</table>" in line:
            # Revision history is maintained in a table - parse it
            # Function works backwards from the END of a table, hence </table>
            revision_table = parse_html_table(introduction, line_number)
            # TODO: Create Revision table
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
    return common.BackMatter(resources=None)


def parse_html_table(contents: list[str], table_end_line: int) -> list[list[str]]:
    current_line = table_end_line
    while contents[current_line] != "<table>":
        current_line -= 1

    table_list = contents[current_line : table_end_line + 1]

    # It's weird to define a class inside a function, but this is how HTMLParser works.
    class TableParser(HTMLParser):
        parsed_table: list[list[str]] = []
        current_row: list[str] = []
        current_cell: str
        in_row: bool = False
        in_cell: bool = False

        def handle_starttag(
            self, tag: str, attrs: list[tuple[str, str | None]]
        ) -> None:
            if tag == "tr":
                # We're starting a new row
                self.current_row = []
                self.in_row = True
            if tag == "td":
                self.current_cell = ""
                self.in_cell = True

        def handle_endtag(self, tag: str) -> None:
            if tag == "tr":
                self.parsed_table.append(self.current_row)
                self.in_row = False
            if tag == "td":
                self.current_row.append(self.current_cell)
                self.in_cell = False
            else:
                # There are some style tags in the rows - we want a space between the contents
                if self.in_row and self.in_cell:
                    # Add a space to the cell we're processing now.
                    self.current_cell = self.current_cell + " "

        def handle_data(self, data: str) -> None:
            if self.in_row and self.in_cell:
                self.current_cell = self.current_cell + data

        def return_results(self) -> list[list[str]]:
            return self.parsed_table

    table_parser = TableParser()
    table_parser.feed("".join(table_list))
    return table_parser.return_results()


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
