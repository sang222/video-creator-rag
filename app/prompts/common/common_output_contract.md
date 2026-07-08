If JSON is required, return JSON only.
Do not include markdown, code fences, or prose outside the schema.
Return one top-level BaseEnvelope object, not a nested artifact-only object.
The top-level object must have exactly these keys:
contract_version, agent_key, status, confidence_label, evidence_refs, limitations, next_action, operator_summary_vi, technical_appendix, artifact.
Use contract_version "m12.1.0".
Use the exact agent_key from the task.
Use uppercase enum values only:
status = OK, REVIEW_REQUIRED, BLOCK, REFUSAL, or ERROR.
Use OK, not SUCCESS or PASS, for a successful envelope.
confidence_label = LOW, MEDIUM, or HIGH.
Do not include top-level risk_level. If risk semantics are needed, put them inside artifact.risk_assessment.risk_level.
evidence_refs must be a list of objects. limitations must be a list of strings. technical_appendix must be an object. artifact must be an object or null.
Every value must be valid JSON: use quoted strings, numbers, booleans, null, arrays, or objects only. Do not put formulas, arithmetic expressions, comments, Python values, or JavaScript values in JSON fields.
Do not add unknown top-level fields.
Do not omit required envelope fields.
