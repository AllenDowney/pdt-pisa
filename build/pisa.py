"""Read the OECD's PISA files: the SPSS data and codebooks of 2015-2022, the 2012 text file
with its SAS and SPSS control files, and the 2022 student questionnaire.

    member(zip, name, interim)            extract one file of a zip to data/interim (once)
    sav_meta(path)                        pyreadstat metadata (labels, value labels, missing ranges)
    read_sav(path, columns, countries)    raw frame for pdt.data: system missing -> Code(".")
    codebook(xlsx, sheet)                 {name: entry} from a PISA codebook sheet
    code_of(text)                         a codebook VAL ("1", ".N/97", "7 / .N") -> (code, marked missing?)
    sas2012(sas, interim)                 the 2012 SAS program with `$` added from its LENGTH statement
    read_2012(txt, sas, columns, countries)   raw frame via pdt.readers.read_sas_input
    labels_2012(sas), formats_2012(sas), missing_2012(sps)
    questionnaire_2022(txt)               {item id: {text, stem, item, instructions, number, page}}

The raw frames keep the publisher's codes as numbers (97, 9997): they're ordinary values
that the records list as missing. Only SPSS's system missing (and a blank numeric field
in the 2012 text) is a token, Code("."), as pdt.readers does for SAS and Stata.
"""

import re
import zipfile
from pathlib import Path

import pandas as pd
import pyreadstat

from pdt.data import Code
from pdt.readers import parse_sas_input, read_sas_input


def member(zip_path, name, interim):
    """data/interim/<name>, extracted from the zip if it isn't there yet."""
    out = Path(interim) / Path(name).name
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as z, z.open(name) as src, open(out, "wb") as dst:
            while block := src.read(1 << 24):
                dst.write(block)
    return out


# ------------------------------------------------------------------ SPSS (2015-2022)

def sav_meta(path):
    _, meta = pyreadstat.read_sav(str(path), metadataonly=True, user_missing=True)
    return meta


def _keep(frame, countries):
    if countries:
        frame = frame[frame["CNT"].isin(countries)]
    return frame.reset_index(drop=True)


def read_sav(path, columns, countries=None):
    """The .sav file's columns as a raw frame, with only the rows whose CNT is in countries.

    User-missing values (95-99, 5-9) stay numbers; system missing becomes Code(".").
    """
    use = list(dict.fromkeys(list(columns) + (["CNT"] if countries else [])))
    frame, meta = pyreadstat.read_sav(str(path), usecols=use, user_missing=True)
    frame = _keep(frame, countries)
    out = {}
    for name in columns:
        if meta.readstat_variable_types[name] == "string":
            out[name] = pd.Series(frame[name].tolist(), dtype=object)
        else:
            out[name] = pd.Series([Code(".") if v != v else (int(v) if float(v).is_integer() else float(v))
                                   for v in frame[name].tolist()], dtype=object)
    return pd.DataFrame(out)


# ------------------------------------------------------------------ codebooks (2015-2022)

def codebook(path, sheet):
    """{name: {label, type, format, minmax, values: [(VAL text, label)], row}} from a codebook sheet.

    A variable's row has its NAME in the first column; its value rows follow, with the first
    six columns empty and VAL, LABEL in the seventh and eighth.
    """
    import openpyxl
    ws = openpyxl.load_workbook(path, read_only=True)[sheet]
    out, current = {}, None
    for i, row in enumerate(ws.iter_rows(values_only=True), start=1):
        row = list(row) + [None] * 10
        if row[0] not in (None, "") and row[1] not in (None, "") and row[0] != "NAME":
            current = out[str(row[0]).strip()] = {"label": str(row[1]).strip(), "type": row[2], "format": row[3],
                                                  "minmax": row[5], "values": [], "row": i}
        elif current is not None and row[6] not in (None, ""):
            current["values"].append((str(row[6]).strip(), str(row[7] or "").strip()))
    return out


def code_of(text):
    """(code, marked missing) from a codebook VAL: "1" -> (1, False); ".N/97" or "97 / .N" -> (97, True);
    "SYSTEM MISSING" -> (".", True); "ALB" -> ("ALB", False)."""
    if text.upper() == "SYSTEM MISSING":
        return ".", True
    m = re.fullmatch(r"\.[A-Z]\s*/\s*(\d+)|(\d+)\s*/\s*\.[A-Z]", text)
    if m:
        return int(m.group(1) or m.group(2)), True
    if re.fullmatch(r"-?\d+", text):
        return int(text), False
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text), False
    return text, False


# ------------------------------------------------------------------ 2012

def _statement(text, keyword, nth=0):
    """The nth statement that starts with `keyword` alone on a line, up to its ';'."""
    found = re.findall(rf"^\s*{keyword}\s*$(.*?);", text, re.M | re.S | re.I)
    return found[nth] if len(found) > nth else ""


def sas2012(sas, interim):
    """A copy of the 2012 SAS program whose input statement marks text columns with `$`.

    The program declares text columns in its LENGTH statement (`CNT $3`), not in its input
    statement (`CNT 1 - 3`), which is where pdt.readers.parse_sas_input looks. Only that
    mark is added; nothing else changes.
    """
    text = open(sas, encoding="utf-8-sig", errors="replace").read()
    texts = {m.group(1).upper() for m in re.finditer(r"^\s*(\w+)\s+\$\s*\d+\s*$", _statement(text, "length"), re.M)}
    body = _statement(text, "input")
    marked = re.sub(r"^(\s*)(\w+)(\s+)(\d+\s*-\s*\d+)",
                    lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{'$ ' if m.group(2).upper() in texts else ''}{m.group(4)}",
                    body, flags=re.M)
    out = Path(interim) / "PISA2012_SAS_student.marked.sas"
    out.write_text("input\n" + marked + ";\nlabel\n" + _statement(text, "label") + ";\n")
    return out


def read_2012(txt, sas_marked, columns, countries=None):
    use = list(dict.fromkeys(list(columns) + (["CNT"] if countries else [])))
    frame = read_sas_input(txt, sas_marked, use)
    frame = _keep(frame, countries)
    return frame[list(columns)]


def layout_2012(sas_marked):
    return parse_sas_input(sas_marked)


def labels_2012(sas):
    """{NAME (upper case): label}; the label statement's names don't always match the input's case."""
    text = open(sas, encoding="utf-8-sig", errors="replace").read()
    return {m.group(1).upper(): m.group(2).strip()
            for m in re.finditer(r'^\s*(\w+)\s*=\s*"([^"]*)"', _statement(text, "label"), re.M)}


def formats_2012(sas):
    """{NAME (upper case): [(code, label)]}: the value statements, through the format statement
    that assigns them (`ST87Q01 ST87Q01F.`)."""
    text = open(sas, encoding="utf-8-sig", errors="replace").read()
    library, current = {}, None
    for line in text.splitlines():
        m = re.match(r"^\s*value\s+(\$?\w+)", line, re.I)
        if m:
            current = library[m.group(1).upper()] = []
            continue
        m = re.match(r'^\s*("?)([^"=]*?)\1\s*=\s*"([^"]*)"', line)
        if current is not None and m and not line.strip().startswith(("label", "format")):
            raw = m.group(2).strip()
            code = raw if m.group(1) else (int(raw) if re.fullmatch(r"-?\d+", raw) else raw)
            current.append((code, m.group(3).strip()))
        if line.strip() == ";":
            current = None
    assigned = {}
    for m in re.finditer(r"^\s*(\w+)\s+(\$?\w+)\.\s*$", _statement(text, "format", 1), re.M):
        assigned[m.group(1).upper()] = library.get(m.group(2).upper())
    return assigned


def missing_2012(sps):
    """{NAME (upper case): [codes]} from the SPSS syntax's `Missing values NAME (9,8,7).` lines."""
    text = open(sps, encoding="utf-8-sig", errors="replace").read()
    out = {}
    for m in re.finditer(r"^\s*Missing values\s+(.+?)\s*\(([^)]*)\)\s*\.", text, re.M | re.I):
        codes = [c.strip().strip("'\"") for c in m.group(2).split(",")]
        codes = [int(c) if re.fullmatch(r"-?\d+", c) else c for c in codes]
        for name in m.group(1).split():
            out[name.upper()] = codes
    return out


# ------------------------------------------------------------------ the 2022 questionnaire

ITEM = re.compile(r"^(ST\d{3}Q\d{2}[A-Z]{2})\s*(.*?)(?:\s{2,}0\d(?:\s+0\d)+)?\s*$")


def questionnaire_2022(txt):
    """{question or item id: {text, ...}} for the questions this slice needs, from pdftotext -layout.

    A question starts with its id alone or with its text ("ST034        Thinking about ...").
    A battery's items follow ("ST034Q01TA   I feel like ... 01 02 03 04"), wrapped onto lines
    indented as far. Line breaks and the response-code columns are removed; text is verbatim.
    """
    pages = open(txt, encoding="utf-8").read().split("\f")
    lines = [(n, l) for n, page in enumerate(pages, start=1) for l in page.split("\n")]
    out = {}
    i = 0
    while i < len(lines):
        page, line = lines[i]
        m = re.match(r"^\s*(ST\d{3})(?:\s+(\S.*))?$", line)
        if m and not re.match(r"^\s*ST\d{3}Q", line):
            qid, first = m.group(1), [m.group(2).strip()] if m.group(2) else []
            j, stem, instr = i + 1, first, []
            while j < len(lines) and not re.match(r"^\s*ST\d{3}", lines[j][1]):
                t = lines[j][1].strip()
                if t.startswith("("):
                    instr.append(t)
                    j += 1
                    while j < len(lines) and instr[-1].count("(") > instr[-1].count(")"):
                        instr[-1] += " " + lines[j][1].strip()
                        j += 1
                    break
                if t and not re.fullmatch(r"[\d\sA-Za-z]*agree.*|.*disagree.*", t, re.I):
                    stem.append(t)
                elif not t and stem:
                    j += 1
                    continue
                j += 1
            out[qid] = {"text": " ".join(stem + instr), "stem": " ".join(stem),
                        "instructions": " ".join(instr), "number": qid, "page": page}
            i = j
            continue
        m = ITEM.match(line)
        if m and re.match(r"^ST\d{3}Q", line):
            iid, text = m.group(1), [m.group(2).strip()] if m.group(2).strip() else []
            j = i + 1
            while j < len(lines):
                t = lines[j][1]
                if re.match(r"^\s*ST\d{3}", t) or (not t.strip() and text):
                    break
                if t.strip() and not re.match(r"^\s*\d+\s*$", t):
                    text.append(re.sub(r"(\s{2,}0\d)+\s*$", "", t).strip())
                j += 1
            out[iid] = {"item": " ".join(x for x in text if x), "number": iid, "page": page}
            i = j
            continue
        i += 1
    return out
