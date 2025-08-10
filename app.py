from fastapi import FastAPI
from pydantic import BaseModel
import re

app = FastAPI(title="ABAP Parser API", version="1.3")

class ABAPInput(BaseModel):
    pgm_name: str
    inc_name: str
    code: str

# ---------- Robust, line-aware block patterns ----------
FORM_BLOCK_RE     = re.compile(r"(?ms)^\s*FORM\s+(\w+)\s*\.\s*.*?^\s*ENDFORM\s*\.(?:[ \t]*\"[^\n]*)?\s*$")
CLDEF_BLOCK_RE    = re.compile(r"(?ms)^\s*CLASS\s+(\w+)\s+DEFINITION\s*\.\s*.*?^\s*ENDCLASS\s*\.(?:[ \t]*\"[^\n]*)?\s*$")
CLIMP_BLOCK_RE    = re.compile(r"(?ms)^\s*CLASS\s+(\w+)\s+IMPLEMENTATION\s*\.\s*.*?^\s*ENDCLASS\s*\.(?:[ \t]*\"[^\n]*)?\s*$")
METHOD_BLOCK_RE   = re.compile(r"(?ms)^\s*METHOD\s+(\w+)\s*\.\s*.*?^\s*ENDMETHOD\s*\.(?:[ \t]*\"[^\n]*)?\s*$")
FUNC_BLOCK_RE     = re.compile(r"(?ms)^\s*FUNCTION\s+(\w+)\s*\.\s*.*?^\s*ENDFUNCTION\s*\.(?:[ \t]*\"[^\n]*)?\s*$")

# Combined regex for all top-level blocks
TOPLEVEL_RE = re.compile(
    r"(?ms)"
    r"(^\s*FORM\s+\w+\s*\.\s*.*?^\s*ENDFORM\s*\.(?:[ \t]*\"[^\n]*)?\s*$)"
    r"|(^\s*CLASS\s+\w+\s+DEFINITION\s*\.\s*.*?^\s*ENDCLASS\s*\.(?:[ \t]*\"[^\n]*)?\s*$)"
    r"|(^\s*CLASS\s+\w+\s+IMPLEMENTATION\s*\.\s*.*?^\s*ENDCLASS\s*\.(?:[ \t]*\"[^\n]*)?\s*$)"
    r"|(^\s*FUNCTION\s+\w+\s*\.\s*.*?^\s*ENDFUNCTION\s*\.(?:[ \t]*\"[^\n]*)?\s*$)"
)

def _offsets_to_lines(src: str, start: int, end: int):
    start_line = src.count("\n", 0, start) + 1
    end_line   = src.count("\n", 0, end) + 1
    return start_line, end_line

def _emit_block(input_json, block_text, start_off, end_off, results):
    start_line, end_line = _offsets_to_lines(input_json["code"], start_off, end_off)

    if FORM_BLOCK_RE.match(block_text):
        name = FORM_BLOCK_RE.match(block_text).group(1)
        btype = "perform"
        extra = {}
    elif CLDEF_BLOCK_RE.match(block_text):
        name = CLDEF_BLOCK_RE.match(block_text).group(1)
        btype = "class_definition"
        extra = {}
    elif CLIMP_BLOCK_RE.match(block_text):
        name = CLIMP_BLOCK_RE.match(block_text).group(1)
        btype = "class_impl"
        extra = {}
        # Extract methods
        for mm in METHOD_BLOCK_RE.finditer(block_text):
            m_name = mm.group(1)
            m_abs_start = start_off + mm.start(0)
            m_abs_end   = start_off + mm.end(0)
            m_sl, m_el  = _offsets_to_lines(input_json["code"], m_abs_start, m_abs_end)
            results.append({
                "pgm_name": input_json["pgm_name"],
                "inc_name": input_json["inc_name"],
                "type": "method",
                "class_implementation": name,
                "name": m_name,
                "start_line": m_sl,
                "end_line": m_el,
                "code": mm.group(0)
            })
    elif FUNC_BLOCK_RE.match(block_text):
        name = FUNC_BLOCK_RE.match(block_text).group(1)
        btype = "function"
        extra = {}
    else:
        return  # skip unrecognized

    results.append({
        "pgm_name": input_json["pgm_name"],
        "inc_name": input_json["inc_name"],
        "type": btype,
        "name": name,
        "start_line": start_line,
        "end_line": end_line,
        "code": block_text,
        **extra
    })

def parse_abap_code_to_ndjson(input_json: dict):
    src = input_json.get("code", "")
    results = []

    last_end = 0
    for m in TOPLEVEL_RE.finditer(src):
        s, e = m.start(0), m.end(0)

        # Raw code segment before this block
        gap = src[last_end:s]
        if gap.strip():
            g_sl, g_el = _offsets_to_lines(src, last_end, s-1 if s>0 else 0)
            results.append({
                "pgm_name": input_json.get("pgm_name", ""),
                "inc_name": input_json.get("inc_name", ""),
                "type": "raw_code",
                "name": input_json.get("inc_name", ""),
                "start_line": g_sl,
                "end_line": g_el,
                "code": gap
            })

        block_text = m.group(0)
        _emit_block(input_json, block_text, s, e, results)
        last_end = e

    # Raw code segment after last block
    tail = src[last_end:]
    if tail.strip():
        t_sl, t_el = _offsets_to_lines(src, last_end, len(src)-1 if src else 0)
        results.append({
            "pgm_name": input_json.get("pgm_name", ""),
            "inc_name": input_json.get("inc_name", ""),
            "type": "raw_code",
            "name": input_json.get("inc_name", ""),
            "start_line": t_sl,
            "end_line": t_el,
            "code": tail
        })

    # Fallback if nothing matched
    if not results:
        total_lines = src.count("\n") + (1 if src else 0)
        results.append({
            "pgm_name": input_json.get("pgm_name", ""),
            "inc_name": input_json.get("inc_name", ""),
            "type": "raw_code",
            "start_line": 1 if total_lines else 0,
            "end_line": total_lines,
            "code": src
        })

    return results

@app.post("/parse_abap")
def parse_abap(abap_input: ABAPInput):
    return parse_abap_code_to_ndjson(abap_input.dict())
