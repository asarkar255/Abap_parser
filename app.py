# abap_parser_app.py
from fastapi import FastAPI
from pydantic import BaseModel
import re
from typing import List, Dict, Any

app = FastAPI(title="ABAP Parser API", version="1.7")

class ABAPInput(BaseModel):
    pgm_name: str
    inc_name: str
    code: str

# ---------- Robust, line-aware block patterns ----------
FORM_BLOCK_RE   = re.compile(r"(?ms)^\s*FORM\s+(\w+)\s*\.\s*.*?^\s*ENDFORM\s*\.(?:[ \t]*\"[^\n]*)?\s*$")
CLDEF_BLOCK_RE  = re.compile(r"(?ms)^\s*CLASS\s+(\w+)\s+DEFINITION\b[^\n]*\.\s*.*?^\s*ENDCLASS\s*\.(?:[ \t]*\"[^\n]*)?\s*$")
CLIMP_BLOCK_RE  = re.compile(r"(?ms)^\s*CLASS\s+(\w+)\s+IMPLEMENTATION\s*\.\s*.*?^\s*ENDCLASS\s*\.(?:[ \t]*\"[^\n]*)?\s*$")
METHOD_BLOCK_RE = re.compile(r"(?ms)^\s*METHOD\s+(\w+)\s*\.\s*.*?^\s*ENDMETHOD\s*\.(?:[ \t]*\"[^\n]*)?\s*$")
FUNC_BLOCK_RE   = re.compile(r"(?ms)^\s*FUNCTION\s+(\w+)\s*\.\s*.*?^\s*ENDFUNCTION\s*\.(?:[ \t]*\"[^\n]*)?\s*$")
MODULE_BLOCK_RE = re.compile(r"(?ms)^\s*MODULE\s+(\w+)(?:\s+(INPUT|OUTPUT))?\s*\.\s*.*?^\s*ENDMODULE\s*\.(?:[ \t]*\"[^\n]*)?\s*$", re.IGNORECASE)
MACRO_BLOCK_RE  = re.compile(r"(?ms)^\s*DEFINE\s+(\w+)\s*\.\s*.*?^\s*END-OF-DEFINITION\s*\.(?:[ \t]*\"[^\n]*)?\s*$")

# Combined regex for all top-level blocks
TOPLEVEL_RE = re.compile(
    r"(?ms)"
    r"(^\s*FORM\s+\w+\s*\..*?^\s*ENDFORM\s*\..*$)"
    r"|(^\s*CLASS\s+\w+\s+DEFINITION\b[^\n]*\..*?^\s*ENDCLASS\s*\..*$)"
    r"|(^\s*CLASS\s+\w+\s+IMPLEMENTATION\s*\..*?^\s*ENDCLASS\s*\..*$)"
    r"|(^\s*FUNCTION\s+\w+\s*\..*?^\s*ENDFUNCTION\s*\..*$)"
    r"|(^\s*MODULE\s+\w+(?:\s+(?:INPUT|OUTPUT))?\s*\..*?^\s*ENDMODULE\s*\..*$)"
    r"|(^\s*DEFINE\s+\w+\s*\..*?^\s*END-OF-DEFINITION\s*\..*$)"
)

def _offsets_to_lines(src: str, start: int, end: int):
    start_line = src.count("\n", 0, start) + 1 if src else 0
    end_line   = src.count("\n", 0, end) + 1 if src else 0
    return start_line, end_line

def _emit_block(input_json: Dict[str, Any], block_text: str, start_off: int, end_off: int, results: List[Dict[str, Any]]):
    src_all = input_json["code"]
    start_line, end_line = _offsets_to_lines(src_all, start_off, end_off)

    # FORM
    m = FORM_BLOCK_RE.match(block_text)
    if m:
        results.append({
            **input_json,
            "type": "perform",
            "name": m.group(1),
            "start_line": start_line,
            "end_line": end_line,
            "code": block_text
        })
        return

    # CLASS DEF
    m = CLDEF_BLOCK_RE.match(block_text)
    if m:
        results.append({
            **input_json,
            "type": "class_definition",
            "name": m.group(1),
            "start_line": start_line,
            "end_line": end_line,
            "code": block_text
        })
        return

    # CLASS IMP + methods
    m = CLIMP_BLOCK_RE.match(block_text)
    if m:
        class_name = m.group(1)
        results.append({
            **input_json,
            "type": "class_impl",
            "name": class_name,
            "start_line": start_line,
            "end_line": end_line,
            "code": block_text
        })
        for mm in METHOD_BLOCK_RE.finditer(block_text):
            m_name = mm.group(1)
            m_sl, m_el = _offsets_to_lines(src_all, start_off + mm.start(0), start_off + mm.end(0))
            results.append({
                **input_json,
                "type": "method",
                "class_implementation": class_name,
                "name": m_name,
                "start_line": m_sl,
                "end_line": m_el,
                "code": mm.group(0)
            })
        return

    # FUNCTION
    m = FUNC_BLOCK_RE.match(block_text)
    if m:
        results.append({
            **input_json,
            "type": "function",
            "name": m.group(1),
            "start_line": start_line,
            "end_line": end_line,
            "code": block_text
        })
        return

    # MODULE
    m = MODULE_BLOCK_RE.match(block_text)
    if m:
        rec = {
            **input_json,
            "type": "module",
            "name": m.group(1),
            "start_line": start_line,
            "end_line": end_line,
            "code": block_text
        }
        if m.group(2):
            rec["mode"] = m.group(2).upper()
        results.append(rec)
        return

    # MACRO
    m = MACRO_BLOCK_RE.match(block_text)
    if m:
        results.append({
            **input_json,
            "type": "macro",
            "name": m.group(1),
            "start_line": start_line,
            "end_line": end_line,
            "code": block_text
        })
        return

def parse_abap_code_to_ndjson(input_json: dict):
    src = input_json.get("code", "") or ""
    results: List[Dict[str, Any]] = []

    last_end = 0
    for m in TOPLEVEL_RE.finditer(src):
        s, e = m.start(0), m.end(0)
        gap = src[last_end:s]
        if gap.strip():
            g_sl, g_el = _offsets_to_lines(src, last_end, s)
            results.append({
                **input_json,
                "type": "raw_code",
                "name": input_json["inc_name"],
                "start_line": g_sl,
                "end_line": g_el,
                "code": gap
            })
        _emit_block(input_json, m.group(0), s, e, results)
        last_end = e

    tail = src[last_end:]
    if tail.strip():
        t_sl, t_el = _offsets_to_lines(src, last_end, len(src))
        results.append({
            **input_json,
            "type": "raw_code",
            "name": input_json["inc_name"],
            "start_line": t_sl,
            "end_line": t_el,
            "code": tail
        })

    return results

@app.post("/parse_abap")
def parse_abap(abap_input: ABAPInput):
    return parse_abap_code_to_ndjson(abap_input.dict())
