#!/usr/bin/env python3
"""Write the PISA student files in the common format (STANDARD.md §8), four countries' rows.

Reads each release's data file (SPSS for 2015-2022 with pyreadstat; the 2012 fixed-width
text with pdt.readers.read_sas_input, through build/pisa.py) and writes
data/processed/pisa-<release>-student.parquet with the variables this repo has records for
and the rows whose CNT is in build/slice.yaml's `countries`. pdt.release reads each file
back and compares every cell with the reader's frame.
"""

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "build"))

try:
    from pdt.release import data_source, write_release
except ImportError:
    sys.exit("Needs the PublicDataToolkit library: pip install -e ../PublicDataToolkit, or run through `make data`.")
import pisa  # noqa: E402

INTERIM = ROOT / "data" / "interim"


def read(release, table, names):
    cfg = yaml.safe_load((ROOT / "build" / "slice.yaml").read_text())
    spec = cfg["releases"][release]
    source = data_source(ROOT, release, table)
    path = ROOT / source["path"]
    if not path.exists():
        sys.exit(f"{path} is missing; see sources.yaml")
    data = pisa.member(path, spec["member"], INTERIM)
    if spec["format"] == "spss":
        return pisa.read_sav(data, names, cfg["countries"]), source["id"]
    sources = {s["id"]: s for s in yaml.safe_load((ROOT / "sources.yaml").read_text())["sources"]}
    marked = pisa.sas2012(ROOT / sources[spec["sas"]]["path"], INTERIM)
    return pisa.read_2012(data, marked, names, cfg["countries"]), source["id"]


if __name__ == "__main__":
    write_release(ROOT, read)
