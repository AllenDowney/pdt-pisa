"""Build the PISA records (2012, 2015, 2018, 2022 student questionnaire files).

Usage:
    python build/build_records.py        (make build; needs the PublicDataToolkit library and pyreadstat)

Reads build/slice.yaml and the sources in data/raw/ (sources.yaml); extracts data files to
data/interim/. Writes metadata/releases/<release>/student/variables.jsonl, groups.jsonl,
links.jsonl and build/report.md.

Where each field comes from (STANDARD.md §3):
- 2015-2022, label: the codebook (VARLABEL). Values: the data file's own value labels, which
  are the codes as stored (pyreadstat), in its order. A code is missing when the codebook
  marks it (".N/97"); else when the data file declares it missing (SPSS missing range); else
  by rule pisa_missing_from_label, for labels such as "Invalid" that nothing marks.
- 2012, label: the SAS control file's label statement. Values: its value formats. Missing:
  the SPSS syntax's MISSING VALUES; else the same label rule.
- missing types: rule pisa_missing_types, from each code's label (Valid Skip -> inap, ...).
- question text: the 2022 student questionnaire (stem, item, instructions, number); none for
  2012-2018, whose questionnaires aren't sources yet.
- measure, variable_type: build/slice.yaml (hand), by kind of variable.
- links: rule pisa_same_name_same_label between consecutive cycles from 2015 (below).
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from pdt.compare import compare_text, is_change
except ImportError:
    sys.exit("Needs the PublicDataToolkit library on PYTHONPATH (make build sets it).")
import pisa  # noqa: E402

META = ROOT / "metadata"
INTERIM = ROOT / "data" / "interim"
DATASET = "pisa"
TABLE = "student"
RULES = {"missing": ("pisa_missing_from_label", "0.1"), "types": ("pisa_missing_types", "0.1"),
         "links": ("pisa_same_name_same_label", "0.2"), "series": ("pisa_name_series", "0.1")}

# Missing types by the publisher's label (rule pisa_missing_types). PISA's "Not Applicable"
# (2012: "N/A") marks a question the student wasn't given (a rotated or national option).
TYPES = {"valid skip": ["inap"], "not applicable": ["not_asked"], "n/a": ["not_asked"],
         "invalid": ["uncodeable"], "no response": ["no_answer"], "missing": ["no_answer"],
         "not reached": ["other"]}
MISSING_LABELS = set(TYPES)     # labels that mark a code missing when nothing else does
# Stems that the codebooks put before an item's text, which differ by cycle.
STEMS = ("Thinking about your school:", "Agree/disagree:", "Subjective well-being:")


def jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def sources():
    return {s["id"]: s for s in yaml.safe_load((ROOT / "sources.yaml").read_text())["sources"]}


def names_of(spec):
    """(name, kind) for the release's variables, in a fixed order."""
    out = [(n, "id") for n in spec["ids"]] + [("W_FSTUWT", "weight")]
    out += [(f"{spec['replicates']}{i}", "replicate") for i in range(1, 81)]
    out += [(spec["sex"], "sex"), (spec["grade"], "grade"), ("ESCS", "index")]
    out += [(n, "belonging") for n in spec["belonging"]] + [("BELONG", "index")]
    out += [(f"PV{i}READ", "pv") for i in range(1, spec["pvs"] + 1)]
    return out


def types_note():
    rule, version = RULES["types"]
    return (f"Missing types by rule {rule} {version}, from the code's label: "
            + "; ".join(f"{k} -> {', '.join(v)}" for k, v in TYPES.items()) + "; any other label -> unspecified.")


def classify(code, label, marked_by):
    """A value entry. marked_by: the origin of a source that marks the code missing, or None."""
    if marked_by is None and label.strip().lower() in MISSING_LABELS:
        rule, version = RULES["missing"]
        marked_by = {"origin": "rule", "rule": rule, "rule_version": version,
                     "note": "No source marks this code missing; its label does. " + types_note()}
    if marked_by is None:
        return {"code": code, "label": label, "kind": "valid"}
    types = ["unspecified"] if code == "." else TYPES.get(label.strip().lower(), ["unspecified"])
    return {"code": code, "label": label, "kind": "missing", "missing_types": types, "origin": marked_by}


def storage(kind, is_text, fmt=None):
    if is_text:
        return "string"
    if fmt:
        return "integer" if re.search(r"\.0$", str(fmt)) else "float"
    return "integer" if kind in ("sex", "grade", "belonging") else "float"


def common(rec, fields, release, kind, cfg, source, name):
    hand = {"origin": "hand", "author": cfg["author"], "date": cfg["date"]}
    facts = cfg["kinds"][kind]
    rec["measure"], rec["variable_type"] = facts["measure"], facts["variable_type"]
    fields["measure"] = {**hand, "note": f"By kind of variable ({kind}) in build/slice.yaml."}
    fields["variable_type"] = dict(fields["measure"])
    rec["availability"] = [{"wave": int(release)}]
    fields["availability"] = {"origin": "data_file", "source": source,
                              "note": "Each cycle is one release; the variable is in its file."}
    rec["source_location"] = {"source": source, "upstream_id": name}


# ------------------------------------------------------------------ 2015-2022

def questionnaire(src, spec):
    pdf = ROOT / src[spec["questionnaire"]]["path"]
    out = INTERIM / (pdf.stem + ".txt")
    if not out.exists():
        subprocess.run(["pdftotext", "-layout", str(pdf), str(out)], check=True)
    return pisa.questionnaire_2022(out)


def add_question(rec, fields, name, kind, spec, quest):
    """The 2022 questionnaire's wording of a belonging item, or the question behind sex and grade."""
    origin = {"origin": "publisher", "source": spec["questionnaire"]}
    if kind == "belonging" and name in quest and name[:5] in quest:
        q, item = quest[name[:5]], quest[name]
        rec["questions"] = [{"text": f"{q['stem']} {item['item']}", "stem": q["stem"], "item": item["item"],
                             "instructions": q["instructions"], "number": name}]
        fields["questions"] = {**origin, "location": f"p. {item['page']}"}
    elif kind in ("sex", "grade"):
        qid = {"sex": "ST004", "grade": "ST001"}[kind]
        if qid in quest:
            q = quest[qid]
            rec["questions"] = [{"text": q["stem"], "number": f"{qid}Q01TA",
                                 **({"instructions": q["instructions"]} if q["instructions"] else {}),
                                 "note": f"{name} is derived from this question ({rec['label']})."}]
            fields["questions"] = {**origin, "location": f"p. {q['page']}"}


def build_spss(release, spec, src, cfg, report):
    path = pisa.member(ROOT / src[spec["data"]]["path"], spec["member"], INTERIM)
    meta = pisa.sav_meta(path)
    book = pisa.codebook(ROOT / src[spec["codebook"]]["path"], spec["sheet"])
    quest = questionnaire(src, spec) if spec.get("questionnaire") else {}
    records = []
    for name, kind in names_of(spec):
        if name not in meta.column_names:
            report.append(f"- `{name}`: not in the data file")
            continue
        entry = book.get(name)
        loc = f"sheet {spec['sheet']}, row {entry['row']}" if entry else None
        rec = {"dataset": DATASET, "release": release, "table": TABLE, "name": name}
        fields = {}
        rec["label"] = entry["label"] if entry else meta.column_names_to_labels.get(name)
        if not entry:
            fields["label"] = {"origin": "data_file", "source": spec["data"]}
            report.append(f"- `{name}`: not in the codebook; label from the data file")
        elif is_change(compare_text(entry["label"], meta.column_names_to_labels.get(name))):
            report.append(f"- `{name}`: codebook label {entry['label']!r}, data file "
                          f"{meta.column_names_to_labels.get(name)!r}")
        is_text = meta.readstat_variable_types[name] == "string"
        rec["storage_type"] = storage(kind, is_text, meta.original_variable_types.get(name))

        marked, by_label = {}, {}
        for val, lab in (entry or {}).get("values", []):
            code, is_marked = pisa.code_of(val)
            if is_marked:
                marked[code] = val
                if code != ".":                 # SYSTEM MISSING is SPSS's own missing, not a code
                    by_label[lab.strip().lower()] = val
        ranges = meta.missing_ranges.get(name) or []
        values = []
        for raw, lab in meta.variable_value_labels.get(name, {}).items():
            code = int(raw) if isinstance(raw, float) and raw.is_integer() else raw
            by = None
            if code in marked:
                by = {"origin": "publisher", "source": spec["codebook"], "location": loc,
                      "note": f"The codebook marks it missing ({marked[code]}). " + types_note()}
            elif lab.strip().lower() in by_label:
                val = by_label[lab.strip().lower()]
                by = {"origin": "publisher", "source": spec["codebook"], "location": loc,
                      "note": f"The codebook marks {lab!r} missing as {val}; the data file codes it {code}. "
                              + types_note()}
                report.append(f"- `{name}`: the codebook gives {lab!r} as {val}, the data file as {code}")
            elif isinstance(code, (int, float)) and any(r["lo"] <= code <= r["hi"] for r in ranges):
                by = {"origin": "data_file", "source": spec["data"],
                      "note": "The data file declares it missing (SPSS missing values). " + types_note()}
            values.append(classify(code, lab, by))
        if not is_text and any(v == "SYSTEM MISSING" for v, _ in (entry or {}).get("values", [])):
            values.append(classify(".", "Missing", {
                "origin": "publisher", "source": spec["codebook"], "location": loc,
                "note": "The codebook's 'SYSTEM MISSING' row (SPSS system missing). It gives no reason: unspecified."}))
        elif not is_text:
            # Any SPSS numeric can hold system missing, the data file's own missing value; this
            # codebook doesn't list it for this variable (2015's ESCS has 1,679 such cells).
            values.append({"code": ".", "kind": "missing", "missing_types": ["unspecified"],
                           "origin": {"origin": "data_file", "source": spec["data"],
                                      "note": "SPSS system missing, which the codebook doesn't list for this "
                                              "variable. No source gives a reason: unspecified."}})
        if values:
            rec["values"] = values
            fields["values"] = {"origin": "data_file", "source": spec["data"],
                                "note": "Codes and labels from the data file's value labels; each missing code's "
                                        "own origin says what marks it missing."}
        common(rec, fields, release, kind, cfg, spec["codebook"], name)
        if quest:
            add_question(rec, fields, name, kind, spec, quest)
        rec["provenance"] = {"record": {"origin": "publisher", "source": spec["codebook"], "location": loc},
                             "fields": fields}
        if not loc:
            del rec["provenance"]["record"]["location"]
        records.append(rec)
    return records


# ------------------------------------------------------------------ 2012

def build_2012(release, spec, src, cfg, report):
    sas = ROOT / src[spec["sas"]]["path"]
    layout = pisa.layout_2012(pisa.sas2012(sas, INTERIM))
    labels, formats = pisa.labels_2012(sas), pisa.formats_2012(sas)
    missing = pisa.missing_2012(ROOT / src[spec["spss"]]["path"])
    records = []
    for name, kind in names_of(spec):
        if name not in layout:
            report.append(f"- `{name}`: not in the SAS input statement")
            continue
        up = name.upper()
        rec = {"dataset": DATASET, "release": release, "table": TABLE, "name": name}
        fields = {}
        if up in labels:
            rec["label"] = labels[up]
        rec["storage_type"] = storage(kind, layout[name][2])
        declared = missing.get(up) or []
        values = []
        for code, lab in formats.get(up) or []:
            by = None
            if code in declared:
                by = {"origin": "publisher", "source": spec["spss"],
                      "note": f"The SPSS syntax declares it missing (Missing values {up}). " + types_note()}
            values.append(classify(code, lab, by))
        if values:
            rec["values"] = values
            fields["values"] = {"origin": "publisher", "source": spec["sas"],
                                "note": "Codes and labels from the SAS control file's value formats; each missing "
                                        "code's own origin says what marks it missing."}
        if unlabeled := [c for c in declared if c not in {v["code"] for v in values}]:
            report.append(f"- `{name}`: the SPSS syntax declares {unlabeled} missing, but no format labels them")
        common(rec, fields, release, kind, cfg, spec["sas"], name)
        rec["provenance"] = {"record": {"origin": "publisher", "source": spec["sas"]}, "fields": fields}
        records.append(rec)
    return records


# ------------------------------------------------------------------ groups and links

def groups_for(release, spec, records):
    names = {r["name"] for r in records}
    rule, version = RULES["series"]
    ref = lambda n: {"release": release, "table": TABLE, "name": n}  # noqa: E731
    reps = [f"{spec['replicates']}{i}" for i in range(1, 81) if f"{spec['replicates']}{i}" in names]
    pvs = [f"PV{i}READ" for i in range(1, spec["pvs"] + 1) if f"PV{i}READ" in names]
    series = {"origin": "rule", "rule": rule, "rule_version": version,
              "note": "Members by the publisher's numbered names and labels."}
    out = [{"id": f"{release}.replicate-weights", "kind": "other", "label": "Replicate weights (BRR-Fay)",
            "releases": [release], "members": [ref(n) for n in reps], "ordered": True, "origin": series,
            "note": "The 80 replicate weights, labelled '... STUDENT REPLICATE BRR-FAY WEIGHT(S) n'. Variance is "
                    "estimated over all 80, with W_FSTUWT (dataset.yaml `design`)."},
           {"id": f"{release}.pv-read", "kind": "other", "label": "Plausible values in reading",
            "releases": [release], "members": [ref(n) for n in pvs], "ordered": True, "origin": series,
            "note": f"{len(pvs)} plausible values of each student's reading score, to be analysed together."}]
    bel = [n for n in spec["belonging"] if n in names]
    stem = next((r["questions"][0].get("stem") for r in records if r["name"] in bel and r.get("questions")), None)
    battery = {"id": f"{release}.belonging", "kind": "battery", "label": "Sense of belonging at school",
               "releases": [release], "members": [ref(n) for n in bel], "ordered": True}
    if stem:
        battery["stem"] = stem
        battery["origin"] = {"origin": "publisher", "source": spec["questionnaire"]}
    else:
        battery["origin"] = {**series, "note": f"Members by their shared question number ({bel[0][:5]})."}
    out.append(battery)
    return out


def strip_stem(label):
    for s in STEMS:
        if (label or "").startswith(s):
            return label[len(s):].strip()
    return label or ""


def links_between(old, new, recs_old, recs_new, cfg, codebook_new):
    rule, version = RULES["links"]
    out, report = [], []
    by_new = {r["name"]: r for r in recs_new}
    for r in recs_old:
        n = r["name"]
        if n not in by_new:
            report.append(f"- `{n}`: not in {new}")
            continue
        if n in cfg["rescaled"]:
            report.append(f"- `{n}`: a scaled index, estimated afresh in each cycle; not linked")
            continue
        s = by_new[n]
        if is_change(compare_text(strip_stem(r.get("label")), strip_stem(s.get("label")))):
            report.append(f"- `{n}`: labels differ ({r.get('label')!r}, {s.get('label')!r}); not linked")
            continue
        relations, notes = ["same_item"], []
        va = {str(v["code"]): v.get("label") for v in r.get("values", [])}
        vb = {str(v["code"]): v.get("label") for v in s.get("values", [])}
        if set(va) != set(vb):
            relations.append("codes_changed")
            notes.append(f"codes only in {old}: {sorted(set(va) - set(vb))}, only in {new}: {sorted(set(vb) - set(va))}")
        # A code with no label on one side (SPSS system missing, unlabelled when the codebook doesn't
        # list it) hasn't been relabelled.
        changed = [c for c in va if c in vb and va[c] is not None and vb[c] is not None
                   and is_change(compare_text(va[c], vb[c]))]
        label_changed = is_change(compare_text(r.get("label"), s.get("label")))
        if changed or label_changed:
            relations.append("labels_changed")
            if label_changed:
                notes.append(f"label {r.get('label')!r} -> {s.get('label')!r}")
            notes += [f"code {c} {va[c]!r} -> {vb[c]!r}" for c in changed]
        out.append({"from": {"release": old, "table": TABLE, "name": n},
                    "to": {"release": new, "table": TABLE, "name": n}, "relations": relations,
                    "origin": {"origin": "rule", "rule": rule, "rule_version": version, "source": codebook_new},
                    "note": "The same name in consecutive cycles, with the same codebook label once the cycle's "
                            "stem is removed." + (" Changed: " + "; ".join(notes) + "." if notes else "")})
    return out, report


# ------------------------------------------------------------------ main

def main():
    cfg = yaml.safe_load((ROOT / "build" / "slice.yaml").read_text())
    src = sources()
    report, groups, built = [], [], {}
    for release, spec in cfg["releases"].items():
        lines = []
        recs = (build_spss if spec["format"] == "spss" else build_2012)(release, spec, src, cfg, lines)
        built[release] = recs
        jsonl(META / "releases" / release / TABLE / "variables.jsonl", recs)
        groups += groups_for(release, spec, recs)
        report += [f"## Release {release}", "", f"- {len(recs)} records"] + lines + [""]
    links = []
    order = sorted(r for r in built if r != "2012")
    for old, new in zip(order, order[1:]):
        more, lines = links_between(old, new, built[old], built[new], cfg, cfg["releases"][new]["codebook"])
        links += more
        report += [f"## Links {old} -> {new}: {len(more)}", ""] + lines + [""]
    report += ["## 2012 -> 2015", "", "- Not linked. The belonging items are ST87Q01-09 in 2012 and ST034Q01TA-06TA "
               "from 2015, and no source here says which continues which (the technical reports would).", ""]
    jsonl(META / "groups.jsonl", groups)
    jsonl(META / "links.jsonl", links)
    head = ["# Build report: pisa", "", "Generated by `build/build_records.py`. Do not edit.", "",
            f"- {sum(len(r) for r in built.values())} records in {len(built)} releases; {len(groups)} groups; "
            f"{len(links)} links", ""]
    (ROOT / "build" / "report.md").write_text("\n".join(head + report) + "\n")
    print("\n".join(head + report))


if __name__ == "__main__":
    main()
