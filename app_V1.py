from fastapi import FastAPI
from pydantic import BaseModel
import re
import json

app = FastAPI(title="ABAP Parser API", version="1.0")

class ABAPInput(BaseModel):
    pgm_name: str
    inc_name: str
    code: str

def find_line_numbers(block_text, all_lines):
    """Find start and end line numbers of a code block."""
    block_lines = block_text.strip().splitlines()
    first_line = block_lines[0].strip()
    last_line = block_lines[-1].strip()

    start_line = None
    end_line = None
    for idx, line in enumerate(all_lines, start=1):
        if start_line is None and first_line == line.strip():
            start_line = idx
        if last_line == line.strip():
            end_line = idx
    return start_line, end_line

# ...imports and FastAPI model stay the same...

def parse_abap_code_to_ndjson(input_json: dict):
    abap_code = input_json.get("code", "")
    lines = abap_code.splitlines()
    results = []

    # Combined regex for FORM, CLASS DEF, CLASS IMPL
    # NOTE: allow optional spaces before the period in starters/enders
    pattern = re.compile(
        r"(FORM\s+\w+\s*\..*?ENDFORM\s*\.)|"
        r"(CLASS\s+\w+\s+DEFINITION\s*\..*?ENDCLASS\s*\.)|"
        r"(CLASS\s+\w+\s+IMPLEMENTATION\s*\..*?ENDCLASS\s*\.)",
        re.IGNORECASE | re.DOTALL
    )

    for match in pattern.finditer(abap_code):
        block = match.group(0).strip()

        if re.match(r"^FORM\s+\w+\s*\.", block, re.IGNORECASE):
            name = re.match(r"FORM\s+(\w+)\s*\.", block, re.IGNORECASE).group(1)
            btype = "perform"
            extra = {}

        elif re.match(r"^CLASS\s+\w+\s+DEFINITION\s*\.", block, re.IGNORECASE):
            name = re.match(r"CLASS\s+(\w+)\s+DEFINITION\s*\.", block, re.IGNORECASE).group(1)
            btype = "class_definition"
            extra = {}

        elif re.match(r"^CLASS\s+\w+\s+IMPLEMENTATION\s*\.", block, re.IGNORECASE):
            name = re.match(r"CLASS\s+(\w+)\s+IMPLEMENTATION\s*\.", block, re.IGNORECASE).group(1)
            btype = "class_impl"
            extra = {}

            # Extract methods inside class impl (also allow spaces before dots)
            method_pattern = re.compile(
                r"(METHOD\s+\w+\s*\..*?ENDMETHOD\s*\.)",
                re.IGNORECASE | re.DOTALL
            )
            for m_block in method_pattern.findall(block):
                m_name = re.match(r"METHOD\s+(\w+)\s*\.", m_block, re.IGNORECASE).group(1)
                m_start, m_end = find_line_numbers(m_block, lines)
                results.append({
                    "pgm_name": input_json.get("pgm_name", ""),
                    "inc_name": input_json.get("inc_name", ""),
                    "type": "method",
                    "class_implementation": name,
                    "name": m_name,
                    "start_line": m_start,
                    "end_line": m_end,
                    "code": m_block.strip()
                })

        start, end = find_line_numbers(block, lines)
        results.append({
            "pgm_name": input_json.get("pgm_name", ""),
            "inc_name": input_json.get("inc_name", ""),
            "type": btype,
            "name": name,
            "start_line": start,
            "end_line": end,
            "code": block
        })

    if not results:
        results.append({
            "pgm_name": input_json.get("pgm_name", ""),
            "inc_name": input_json.get("inc_name", ""),
            "type": "raw_code",
            "start_line": 1,
            "end_line": len(lines),
            "code": abap_code.strip()
        })

    results.sort(key=lambda x: x["start_line"])
    return results



@app.post("/parse_abap")
def parse_abap(abap_input: ABAPInput):
    parsed = parse_abap_code_to_ndjson(abap_input.dict())
    return parsed


# To run:
# uvicorn main:app --reload
