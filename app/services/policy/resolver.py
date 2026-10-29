from collections.abc import Mapping, Sequence

from app.schemas.preprocessing import PolicyEvidence, PolicyResolution


_FLAGS = {
    "NO_AI": ("RED", "NO_AI"),
    "RED": ("RED", "NO_AI"),
    "PROCESS_DISCLOSURE": ("YELLOW", "PROCESS_DISCLOSURE"),
    "YELLOW": ("YELLOW", "PROCESS_DISCLOSURE"),
    "FULL_INTEGRATION": ("GREEN", "FULL_INTEGRATION"),
    "GREEN": ("GREEN", "FULL_INTEGRATION"),
}


def resolve_policy(
    *, assignment_id: str | None,
    authoritative_records: Sequence[Mapping[str, object]] | None = (),
    evidence: Sequence[PolicyEvidence] = (),
) -> PolicyResolution:
    """Resolve only explicit authoritative settings; extracted evidence stays advisory."""
    if authoritative_records:
        parsed: list[tuple[str, str, Mapping[str, object]]] = []
        for record in authoritative_records:
            raw = record.get("policy") or record.get("policy_level") or record.get("canonical_policy")
            if not isinstance(raw, str) or raw.strip().upper() not in _FLAGS:
                return PolicyResolution(policy_flag="UNKNOWN", policy_source="assignment", policy_evidence=list(evidence), requires_human_review=True, resolution_reason="POLICY_CONFLICT")
            flag, canonical = _FLAGS[raw.strip().upper()]
            scope = record.get("scope")
            if scope is not None and str(scope).strip().lower() not in {"assignment", "assignment_wide"}:
                return PolicyResolution(policy_flag="UNKNOWN", policy_source="assignment", policy_evidence=list(evidence), requires_human_review=True, resolution_reason="POLICY_SCOPE_UNCLEAR")
            parsed.append((flag, canonical, record))
        signatures = {
            (flag, canonical, tuple(sorted(str(value) for value in record.get("required_conditions", [])))
             if isinstance(record.get("required_conditions", []), list) else (),
             str(record.get("version", "")))
            for flag, canonical, record in parsed
        }
        if len(signatures) != 1:
            return PolicyResolution(policy_flag="UNKNOWN", policy_source="assignment", policy_evidence=list(evidence), requires_human_review=True, resolution_reason="POLICY_CONFLICT")
        flag, canonical, record = parsed[0]
        return PolicyResolution(
            policy_flag=flag, canonical_policy=canonical, policy_source="assignment",
            policy_version=str(record["version"]) if record.get("version") else None,
            policy_evidence=list(evidence), resolution_reason="AUTHORITATIVE_POLICY_FOUND",
            required_conditions=[str(value) for value in record.get("required_conditions", [])] if isinstance(record.get("required_conditions", []), list) else [],
        )
    if any(item.ambiguous or item.requires_human_review for item in evidence):
        return PolicyResolution(policy_flag="UNKNOWN", policy_source="extracted_evidence", policy_evidence=list(evidence), requires_human_review=True, resolution_reason="POLICY_EXTRACTION_UNCERTAIN")
    reason = "ASSIGNMENT_NOT_FOUND" if assignment_id and authoritative_records is None else ("POLICY_NOT_FOUND" if assignment_id or not evidence else "POLICY_SCOPE_UNCLEAR")
    return PolicyResolution(policy_flag="UNKNOWN", policy_source="extracted_evidence" if evidence else None, policy_evidence=list(evidence), requires_human_review=bool(evidence), resolution_reason=reason)
