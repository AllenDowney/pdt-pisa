#!/usr/bin/env python3
"""Write the PISA student files in the common format (STANDARD.md §8), the rows `data_rows` keeps.

Reads each release's data file with the shared readers: pdt.readers.read_sav for 2015-2022
(SPSS), pdt.readers.read_sas_input for 2012 (fixed-width text and its SAS program), and
writes data/processed/pisa-<release>-student.parquet with the variables this repo has records
for and the rows dataset.yaml's `data_rows` names (four countries). pdt.release reads each
file back and compares every cell with the reader's frame.
"""

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "build"))

try:
    from pdt.readers import read_sas_input, read_sav
    from pdt.release import data_source, write_release
except ImportError:
    sys.exit("Needs the PublicDataToolkit library: pip install -e ../PublicDataToolkit, or run through `make data`.")
import pisa  # noqa: E402

INTERIM = ROOT / "data" / "interim"


def read(release, table, names):
    spec = yaml.safe_load((ROOT / "build" / "slice.yaml").read_text())["releases"][release]
    rows = yaml.safe_load((ROOT / "dataset.yaml").read_text()).get("data_rows")
    keep = {rows["column"]: rows["in"]} if rows else {}
    source = data_source(ROOT, release, table)
    path = ROOT / source["path"]
    if not path.exists():
        sys.exit(f"{path} is missing; see sources.yaml")
    data = pisa.member(path, spec["member"], INTERIM)
    if spec["format"] == "spss":
        return read_sav(data, names, rows=keep), source["id"]
    sources = {s["id"]: s for s in yaml.safe_load((ROOT / "sources.yaml").read_text())["sources"]}
    frame = read_sas_input(data, ROOT / sources[spec["sas"]]["path"], list(dict.fromkeys(names + list(keep))))
    for col, values in keep.items():
        frame = frame[frame[col].isin(values)]
    return frame[names].reset_index(drop=True), source["id"]


if __name__ == "__main__":
    write_release(ROOT, read)
