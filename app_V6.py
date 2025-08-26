# abap_parser_app.py
from fastapi import FastAPI
from pydantic import BaseModel
import re
from typing import List, Dict, Any

app = FastAPI(title="ABAP Parser API", version="1.9")

class ABAPInput(BaseModel):
    pgm_name: str
    inc_name: str
    code: str

# ---------- Robust, line-aware block patterns ----------
# Notes:
# - FORM allows parameters before the header dot (USING/CHANGING/TABLES/RAISING...)
# - CLASS ... DEFINITION matches modifiers on the header
# - MODULE captures optional INPUT/OUTPUT mode
# - MACRO: DEFINE ... END-OF-DEFINITION.
# - METHOD: allow constructor/class_constructor and interface methods (lif_iface~method)

# Case-insensitive everywhere
FORM_BLOCK_RE   = re.compile(
    r"(?ims)^\s*FORM\s+(\w+)\b([^\n]*?)\.\s*.*?^\s*ENDFORM\s*\.(?:[ \t]*\"[^\n]*)?\s*$"
)
CLDEF_BLOCK_RE  = re.compile(
    r"(?ims)^\s*CLASS\s+(\w+)\s+DEFINITION\b[^\n]*\.\s*.*?^\s*ENDCLASS\s*\.(?:[ \t]*\"[^\n]*)?\s*$"
)
CLIMP_BLOCK_RE  = re.compile(
    r"(?ims)^\s*CLASS\s+(\w+)\s+IMPLEMENTATION\s*\.\s*.*?^\s*ENDCLASS\s*\.(?:[ \t]*\"[^\n]*)?\s*$"
)
# IMPORTANT: no trailing '$' so we can find multiple methods inside a class impl
# Name supports 'constructor', 'class_constructor', and 'iface~method'
METHOD_BLOCK_RE = re.compile(
    r"(?ims)^\s*METHOD\s+([A-Za-z_]\w*(?:~\w+)?|constructor|class_constructor)\s*\.\s*.*?^\s*ENDMETHOD\s*\.(?:[ \t]*\"[^\n]*)?"
)
FUNC_BLOCK_RE   = re.compile(
    r"(?ims)^\s*FUNCTION\s+(\w+)\s*\.\s*.*?^\s*ENDFUNCTION\s*\.(?:[ \t]*\"[^\n]*)?\s*$"
)
MODULE_BLOCK_RE = re.compile(
    r"(?ims)^\s*MODULE\s+(\w+)(?:\s+(INPUT|OUTPUT))?\s*\.\s*.*?^\s*ENDMODULE\s*\.(?:[ \t]*\"[^\n]*)?\s*$"
)
MACRO_BLOCK_RE  = re.compile(
    r"(?ims)^\s*DEFINE\s+(\w+)\s*\.\s*.*?^\s*END-OF-DEFINITION\s*\.(?:[ \t]*\"[^\n]*)?\s*$"
)

# Combined regex for all top-level blocks (METHODs are emitted only via class_impl extraction)
TOPLEVEL_RE = re.compile(
    r"(?ims)"
    r"(^\s*FORM\s+\w+\b[^\n]*\.\s*.*?^\s*ENDFORM\s*\.(?:[ \t]*\"[^\n]*)?\s*$)"
    r"|(^\s*CLASS\s+\w+\s+DEFINITION\b[^\n]*\.\s*.*?^\s*ENDCLASS\s*\.(?:[ \t]*\"[^\n]*)?\s*$)"
    r"|(^\s*CLASS\s+\w+\s+IMPLEMENTATION\s*\.\s*.*?^\s*ENDCLASS\s*\.(?:[ \t]*\"[^\n]*)?\s*$)"
    r"|(^\s*FUNCTION\s+\w+\s*\.\s*.*?^\s*ENDFUNCTION\s*\.(?:[ \t]*\"[^\n]*)?\s*$)"
    r"|(^\s*MODULE\s+\w+(?:\s+(?:INPUT|OUTPUT))?\s*\.\s*.*?^\s*ENDMODULE\s*\.(?:[ \t]*\"[^\n]*)?\s*$)"
    r"|(^\s*DEFINE\s+\w+\s*\.\s*.*?^\s*END-OF-DEFINITION\s*\.(?:[ \t]*\"[^\n]*)?\s*$)"
)

def _offsets_to_lines(src: str, start: int, end: int):
    """Convert absolute character offsets into 1-based line numbers (inclusive)."""
    start_line = src.count("\n", 0, start) + 1 if src else 0
    end_line   = src.count("\n", 0, end) + 1 if src else 0
    return start_line, end_line

def _emit_block(input_json: Dict[str, Any], block_text: str, start_off: int, end_off: int, results: List[Dict[str, Any]]):
    """
    Emits one or more result records for a matched block.
    For class_impl: emit container-only code first, then full method items.
    For others: emit single record as-is.
    """
    src_all = input_json["code"]
    start_line, end_line = _offsets_to_lines(src_all, start_off, end_off)

    # FORM (with optional parameters before the dot)
    m = FORM_BLOCK_RE.match(block_text)
    if m:
        name = m.group(1)
        results.append({
            "pgm_name": input_json["pgm_name"],
            "inc_name": input_json["inc_name"],
            "type": "perform",
            "name": name,
            "start_line": start_line,
            "end_line": end_line,
            "code": block_text
        })
        return

    # CLASS DEFINITION (modifiers on header allowed)
    m = CLDEF_BLOCK_RE.match(block_text)
    if m:
        name = m.group(1)
        results.append({
            "pgm_name": input_json["pgm_name"],
            "inc_name": input_json["inc_name"],
            "type": "class_definition",
            "name": name,
            "start_line": start_line,
            "end_line": end_line,
            "code": block_text
        })
        return

    # CLASS IMPLEMENTATION (container-only + methods-after)
    m = CLIMP_BLOCK_RE.match(block_text)
    if m:
        class_name = m.group(1)

        # Find method spans inside the class block
        method_spans = [(mm.start(0), mm.end(0)) for mm in METHOD_BLOCK_RE.finditer(block_text)]

        if method_spans:
            first_start = method_spans[0][0]
            last_end    = method_spans[-1][1]
            header = block_text[:first_start].rstrip()
            footer = block_text[last_end:].lstrip()
            container_code = header + ("\n" if header and footer else "") + footer
        else:
            # No methods inside: container is the whole block
            container_code = block_text

        # Emit the class_impl FIRST with container-only code
        results.append({
            "pgm_name": input_json["pgm_name"],
            "inc_name": input_json["inc_name"],
            "type": "class_impl",
            "name": class_name,
            "start_line": start_line,
            "end_line": end_line,
            "code": container_code
        })

        # Then emit each method (full body) immediately after
        for mm in METHOD_BLOCK_RE.finditer(block_text):
            m_name = mm.group(1)
            m_abs_start = start_off + mm.start(0)
            m_abs_end   = start_off + mm.end(0)
            m_sl, m_el  = _offsets_to_lines(src_all, m_abs_start, m_abs_end)
            results.append({
                "pgm_name": input_json["pgm_name"],
                "inc_name": input_json["inc_name"],
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
        name = m.group(1)
        results.append({
            "pgm_name": input_json["pgm_name"],
            "inc_name": input_json["inc_name"],
            "type": "function",
            "name": name,
            "start_line": start_line,
            "end_line": end_line,
            "code": block_text
        })
        return

    # MODULE (capture optional mode)
    m = MODULE_BLOCK_RE.match(block_text)
    if m:
        name = m.group(1)
        mode = (m.group(2) or "").upper()
        rec = {
            "pgm_name": input_json["pgm_name"],
            "inc_name": input_json["inc_name"],
            "type": "module",
            "name": name,
            "start_line": start_line,
            "end_line": end_line,
            "code": block_text
        }
        if mode:
            rec["mode"] = mode  # optional field
        results.append(rec)
        return

    # MACRO
    m = MACRO_BLOCK_RE.match(block_text)
    if m:
        name = m.group(1)
        results.append({
            "pgm_name": input_json["pgm_name"],
            "inc_name": input_json["inc_name"],
            "type": "macro",
            "name": name,
            "start_line": start_line,
            "end_line": end_line,
            "code": block_text
        })
        return

    # Unrecognized → nothing
    return

def parse_abap_code_to_ndjson(input_json: dict):
    src = input_json.get("code", "") or ""
    results: List[Dict[str, Any]] = []

    last_end = 0
    for m in TOPLEVEL_RE.finditer(src):
        s, e = m.start(0), m.end(0)

        # Raw code segment before this block
        gap = src[last_end:s]
        if gap.strip():
            g_sl, g_el = _offsets_to_lines(src, last_end, s - 1 if s > 0 else 0)
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
        t_sl, t_el = _offsets_to_lines(src, last_end, len(src) - 1 if src else 0)
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
