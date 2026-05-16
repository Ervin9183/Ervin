"""Extract the Excel query output into app-friendly files.

The workbook stores the main query result as worksheet XML, so this script uses
streaming XML parsing instead of loading the entire workbook into memory.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import shutil
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


NS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
DEFAULT_WORKBOOK = Path("/Users/ervin/Downloads/Constraint_Query (MB).xlsx")
QUERY_SHEET = "xl/worksheets/sheet1.xml"
DA_SHEET = "xl/worksheets/sheet3.xml"
MAP_IMAGE = "xl/media/image1.jpeg"


def shared_strings(zf: zipfile.ZipFile) -> list[str]:
    strings: list[str] = []
    with zf.open("xl/sharedStrings.xml") as handle:
        for _, elem in ET.iterparse(handle, events=("end",)):
            if elem.tag.endswith("}si"):
                parts = [
                    text.text or ""
                    for text in elem.iter()
                    if text.tag.endswith("}t")
                ]
                strings.append("".join(parts))
                elem.clear()
    return strings


def cell_value(cell: ET.Element, strings: list[str]) -> str:
    value = cell.find("a:v", NS)
    if value is None or value.text is None:
        return ""

    raw = value.text
    if cell.attrib.get("t") == "s":
        return strings[int(raw)]
    return raw


def extract_query_csv(
    zf: zipfile.ZipFile,
    strings: list[str],
    output_path: Path,
) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    row_count = 0

    with zf.open(QUERY_SHEET) as xml_handle:
        with gzip.open(output_path, "wt", newline="", encoding="utf-8") as csv_handle:
            writer = csv.writer(csv_handle)

            for _, elem in ET.iterparse(xml_handle, events=("end",)):
                if not elem.tag.endswith("}row"):
                    continue

                row_values = [""] * 6
                for cell in elem.findall("a:c", NS):
                    ref = cell.attrib.get("r", "")
                    if not ref:
                        continue

                    column = 0
                    for char in ref:
                        if not char.isalpha():
                            break
                        column = column * 26 + ord(char.upper()) - 64

                    if 1 <= column <= 6:
                        row_values[column - 1] = cell_value(cell, strings)

                writer.writerow(row_values)
                row_count += 1
                elem.clear()

    return max(row_count - 1, 0)


def extract_da_groups(zf: zipfile.ZipFile, strings: list[str], output_path: Path) -> list[str]:
    groups: list[str] = []

    with zf.open(DA_SHEET) as xml_handle:
        for _, elem in ET.iterparse(xml_handle, events=("end",)):
            if not elem.tag.endswith("}row"):
                continue

            row_number = int(elem.attrib.get("r", "0"))
            if 3 <= row_number <= 21:
                target_ref = f"C{row_number}"
                for cell in elem.findall("a:c", NS):
                    if cell.attrib.get("r") == target_ref:
                        value = cell_value(cell, strings)
                        if value:
                            groups.append(value)

            elem.clear()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(groups) + "\n", encoding="utf-8")
    return groups


def extract_map(zf: zipfile.ZipFile, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(MAP_IMAGE) as source:
        with output_path.open("wb") as target:
            shutil.copyfileobj(source, target)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--out-dir", type=Path, default=Path("data"))
    args = parser.parse_args()

    with zipfile.ZipFile(args.workbook) as zf:
        strings = shared_strings(zf)
        rows = extract_query_csv(
            zf,
            strings,
            args.out_dir / "constraint_boundary.csv.gz",
        )
        groups = extract_da_groups(zf, strings, args.out_dir / "da_groups.txt")
        extract_map(zf, args.out_dir / "constraint_map.jpeg")

    print(f"Extracted {rows:,} query rows.")
    print(f"Extracted {len(groups)} DA groups.")


if __name__ == "__main__":
    main()
