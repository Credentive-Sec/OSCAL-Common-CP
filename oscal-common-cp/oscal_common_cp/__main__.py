from oscal_pydantic import document, catalog
from oscal_pydantic.core import common
from datetime import datetime, timezone
import re
import uuid
from pathlib import Path
import argparse
from html.parser import HTMLParser


# We rely on the TOC in several places, so we define it first
# Turns out this doesn't solve the problem of multiple sections with the same name, so I am abandoning it.
toc_dict: dict[str, str] = {}
toc_line_re = re.compile(r"^\[(?P<secnum>[\d\.]+)\s(?P<secname>[\w\s-]+)\s\[.*$")


def parse_table_of_contents(contents: list[str]):
    for line in contents:
        toc_line_match = toc_line_re.match(line)
        if toc_line_match is not None:
            toc_line_dict = toc_line_match.groupdict()
            toc_dict[toc_line_dict["secname"]] = toc_line_dict["secnum"]


# Stupid python trick, initialize with zero using list comprehension
toc_pos: list[int] = [0 for _ in range(1, 10)]
toc_pos[0] = 1


# pandoc left some "span" tags in the document, so we need to strip html out of text
def strip_html_from_text(input: str) -> str:
    return re.sub("<.*>", "", input)
    

# This program relies heavily on the specific format of the tokenized CP documents.
def common_policy_to_catalog(common_policy: list[str]) -> document.Document:
    # We will parse the document and store all of the sections as their own list in a nested list
    sections: list[list[str]] = []

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
    # outer list represents a section of the document.

    # The first list is always the introduction/metadata
    metadata = parse_metadata(sections[0])

    # We keep a stack to represent the current place in the TOC
    parent_stack: list[catalog.Group] = []

    section_groups: list[catalog.Group] = []

    # Initialize an empty back-matter for later
    backmatter = None

    # Step through the sections and generate the appropriate OSCAL objects.
    # We skip the first section because is it the title page and other stuff
    for section in sections[1:]:
        # Check for a couple of special sections that we expect to see: TOC and References
        # First line of section is the contents, so we can check there
        if "Table of Contents" in section[0]:
            # In some versions of common, the TOC is a separate section - skip it.
            continue
        # The list of related documents is in a section titled "References" or "Bibliography"
        if "References" in section[0] or "Bibliography" in section[0]:
            # Pass everything except the title line to parse_backmatter
            backmatter = parse_backmatter(section[1:])
            continue
        
        # Assume every other section is a 
        # The first line has the section title, and because it is MD,
        # the number of hashes indicate the depth in the TOC
        header_hashes = re.match(r"#+", section[0])
        if header_hashes is not None:
            section_depth = len(header_hashes.group(0))

            # Parse the section as a group
            current_group = section_to_group(
                section_contents=section, section_depth=section_depth
            )

            # Figure out where the section fits in the overall document
            if current_group is None:
                # Sometimes we get blank headers in the Markdown. skip these.
                continue
            else:
                # update the TOC counter
                toc_pos[section_depth - 1] += 1

                toc_pos[section_depth:] = [0 for _ in range(0,9-section_depth)]

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


    if backmatter is None:
        # back-matter is required, so if we couldn't initialize it, we create an empty one now.
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


def title_to_id(title: str) -> str:
    # Turn "Section Name" into "section-name"
    id = title.lower().strip().replace(" ", "-")
    # Some sections have invalid characters in the name. the next line removes them
    id = "".join([c for c in id if c not in "(),/’:"])
    return id


def section_to_group(
    section_contents: list[str], section_depth: int
) -> catalog.Group | None:
    # Derive the section number
    sec_num = ".".join([str(x) for x in toc_pos][:section_depth])

    # First line in the title of the group.
    # Strip off the leading hashes and the trailing space
    group_title = strip_html_from_text(re.sub("#+", "", section_contents[0]).strip())

    if group_title != "":
        # Turn "Section Name" into "section-name"
        group_id = f"group-{sec_num}-{title_to_id(group_title)}"

        section_group = catalog.Group(
            id=group_id,
            title=f"{sec_num} {group_title}",
        )

        if len(section_contents) > 1:
            # The section contains requirements, and must have a control
            # Controls must be inside an inner group since a group can't have both
            # an innert group and inner controls
            section_control_group: catalog.Group = catalog.Group(
                id=re.sub("group", "control", group_id),
                title=f"{group_title} Controls",
            )

            section_control_list: list[catalog.Control] | None = [
                section_to_control(
                    section_number=sec_num,
                    section_contents=section_contents,
                )
            ]
            section_control_group.controls = section_control_list
            section_group = add_subsection_to_parent(
                section_group, section_control_group
            )

        return section_group
    else:
        return None


def section_to_control(
    section_number: str, section_contents: list[str]
) -> catalog.Control:
    # Strip off the leading hashes and the surrounding spaces
    control_title = strip_html_from_text(re.sub("#+", "", section_contents[0]).strip())
    control_id = f"ctrl-{section_number}-{title_to_id(control_title)}"
    control = catalog.Control(
        id=control_id,
        title=control_title,
        parts=[],
    )

    parts: list[catalog.BasePart] = []
    part_num = 1
    in_table: bool = False
    for section_line_number, section_line_text in enumerate(section_contents[1:]):
        if "<table" in section_line_text:
            # We're inside an html table - we should ignore all content until we are out again.
            in_table = True
            continue
        elif "</table" in section_line_text:
            in_table = False
            table_contents = parse_html_table(
                contents=section_contents, table_end_line=section_line_number
            )
            for row in table_contents:
                parts.append(
                    catalog.StatementPart(
                        id=f"{re.sub("ctrl", "stmt", control_id)}-{part_num}",
                        name="statement",
                        prose="|" + "|".join(row) + "|",
                    )
                )
                part_num += 1
        elif in_table:
            continue
        elif section_line_text[0] == "<":
            # There is sometimes embedded html, which we don't want to include
            continue
        else:
            # If we get here, it's a regular text line
            parts.append(
                catalog.StatementPart(
                    id=f"{re.sub("ctrl", "stmt", control_id)}-{part_num}",
                    name="statement",
                    prose=strip_html_from_text(section_line_text), # Strip any html left in.
                )
            )
            part_num += 1

    control.parts = parts

    return control


def parse_metadata(introduction: list[str]) -> common.Metadata:
    version = ""
    published = None
    revisions = None
    in_table: bool = False  # track if we're in a table
    in_toc: bool = False  # track if we're in a TOC
    toc_lines: list[str] = []  # lines of the TOC to pass to the toc parser
    for line_number, line in enumerate(introduction):
        if line == "":
            # Blank line, ignore and move on
            continue
        elif "<table" in line:
            # We're inside an html table - we should ignore all content until we are out again.
            in_table = True
        elif "</table" in line:
            in_table = False
            # Revision history is maintained in a table - parse it
            # Function works backwards from the END of a table, hence </table>
            revision_table = parse_html_table(introduction, line_number)
            revisions = revision_history_to_revisions(revision_table)
            continue
        elif in_table:
            continue
        elif "Table of Contents" in line:
            in_toc = True
        elif line[0] == "[" and in_toc:
            toc_lines.append(line)
        elif "Version " in line:
            # Parse out the version number then move on
            version = re.sub(r"Version ", "", line)
            continue
        elif line[0] in "*<[(" and not in_toc:
            # First character of the line indicates it's a structural or other
            # metadata line, ignore since we've already parsed the ones we're
            # interested in
            continue
        else:
            try:
                # Try to parse the line as a date
                published = datetime.strptime(line, "%B %d, %Y").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                continue

    parse_table_of_contents(toc_lines)

    if version == "" or published is None:
        raise ValueError("Introduction is missing Version and/or Publication Date.")
    else:
        return common.Metadata(
            title="X.509 Certificate Policy for the U.S. Federal PKI Common Policy Framework",
            published=published.isoformat(),
            version=version,
            oscal_version="1.1.2",  # TODO
            revisions=revisions,
        )


def parse_backmatter(contents: list[str]) -> common.BackMatter:
    # Parse the "References" in Appendix B and convert them to Resources in back-matter
    resource_table: list[list[str]] = []
    resource_list: list[common.Resource] = []

    # References are passed in as an html table - parse it
    for index, line in enumerate(contents):
        if "</table" in line:
            resource_table = parse_html_table(contents=contents, table_end_line=index-1) # Have to subtract 1 from index for the parse_html_table function
    
    resource_re = re.compile(r"^(?P<name>.*)\s*(?P<url>http.*)\s*$")
    # Format should be document_title, description, URL
    for resource in resource_table:
        resource_title = resource[0]
        resource_re_matches = resource_re.match(resource[1])
        if resource_re_matches is not None:
            match_dict = resource_re_matches.groupdict()
            resource_descripton = match_dict["name"]
            resource_url = match_dict["url"]
        else:
            # If we can't parse a resource - it's usually because it doesn't have a URL. 
            # OSCAL resources have to include a URL, so skip this one.
            continue

        resource_list.append(
            common.Resource(
                uuid = uuid.uuid4(), # We are creating a new resource UUID every time - this should be re-considered
                title=resource_title,
                description=resource_descripton,
                rlinks=[
                    common.ResourceLink(href=resource_url)
                ],
            )
        )

    return common.BackMatter(resources=resource_list)


def revision_history_to_revisions(revisions: list[list[str]]) -> list[common.Revision]:
    revision_list: list[common.Revision] = []
    for row in revisions[1:]:
        version_id = row[0]
        try:
            published_date = (
                datetime.strptime(row[1], "%B %d, %Y")
                .replace(tzinfo=timezone.utc)
                .isoformat()
            )
        except ValueError:
            # If we can't parse the date, we're probably in a weird header row
            continue
        revision_details = row[2]
        revision_record = common.Revision(
            version=version_id,
            published=published_date,
            remarks=revision_details,
        )
        revision_list.append(revision_record)

    return revision_list


def parse_html_table(contents: list[str], table_end_line: int) -> list[list[str]]:
    # Start with the end of the table
    current_line = table_end_line + 1

    # Walk up until we find the opening tag
    while "<table" not in contents[current_line]:
        current_line -= 1

    table_list = contents[current_line : table_end_line + 2]

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
            common_catalog = common_policy_to_catalog(common_file.read().splitlines())
    else:
        print("You provided an argument that does not exist or is not a file.")
        parser.print_help
        exit(1)

    # Write the catalog to stdout
    print(common_catalog.model_dump_json())
