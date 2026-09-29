"""Language-model assistance after the first analysis.

* ``suggest_voices``: a casting pass. From each character's own lines and the
  narration about them the model proposes gender, age and pitch; Auris turns
  that into a voice description (no foreign accent) the user can apply.
* ``review_speakers``: a second attribution pass. The model sees a chapter's
  dialogue units with their current speakers and returns only corrections,
  which are applied without touching manual choices.
"""

from __future__ import annotations

import logging

from core.llm_characters import LLMAnalysisError, _chat

log = logging.getLogger(__name__)

AGES = ("child", "teenager", "young adult", "middle-aged", "elderly")
PITCHES = ("very low pitch", "low pitch", "moderate pitch", "high pitch", "very high pitch")

_CASTING_SYSTEM = """You are an audiobook casting director. For each character
you receive their own spoken lines and narration describing them. Propose a
fitting voice: gender, age group and pitch, plus a short Hungarian description
(max 20 words) of timbre and manner. Base it only on the evidence; when the
evidence is weak prefer moderate, neutral choices. Never add accents."""

_CASTING_SCHEMA = {
    "type": "object",
    "properties": {
        "voices": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "gender": {"type": "string", "enum": ["male", "female"]},
                    "age": {"type": "string", "enum": list(AGES)},
                    "pitch": {"type": "string", "enum": list(PITCHES)},
                    "description": {"type": "string"},
                },
                "required": ["name", "gender", "age", "pitch", "description"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["voices"],
    "additionalProperties": False,
}

_REVIEW_SYSTEM = """You are a meticulous literary editor checking dialogue
attribution in a novel. Each [D id | speaker] line is a spoken unit with the
speaker assigned by a first pass; [N id] lines are narration for context.
Hungarian dialogue often starts with a dash (–) and attribution clauses like
"– mondta Anna" follow the speech. Return ONLY units whose speaker is wrong,
with the correct speaker from the roster (or an empty string when the unit is
narration rather than speech). Do not return units that are already correct.
Never invent names that are not in the roster or the text."""

_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "corrections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "speaker": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "speaker", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["corrections"],
    "additionalProperties": False,
}


def voice_instruct(gender: str, age: str, pitch: str) -> str:
    gender = gender if gender in ("male", "female") else "female"
    age = age if age in AGES else "middle-aged"
    pitch = pitch if pitch in PITCHES else "moderate pitch"
    return f"{gender}, {age}, {pitch}"


def suggest_voices(*, title: str, characters: list[dict], llm: dict,
                   timeout: float = 600, max_tokens: int = 4096) -> list[dict]:
    """characters: [{name, gender, lines: [...], mentions: [...]}]."""
    blocks = []
    for character in characters:
        lines = "\n".join(f"  „{line[:220]}”" for line in character.get("lines", [])[:6])
        mentions = "\n".join(f"  {text[:260]}" for text in character.get("mentions", [])[:4])
        blocks.append(
            f"### {character['name']} (eddigi nem: {character.get('gender') or 'unknown'})\n"
            f"Saját mondatai:\n{lines or '  –'}\nLeírás a narrációban:\n{mentions or '  –'}"
        )
    prompt = (
        f"BOOK: {title}\n\nReturn one voice per character, using the exact names.\n\n"
        + "\n\n".join(blocks)
    )
    parsed = _chat(
        base_url=llm["base_url"], api_key=llm["api_key"], model=llm["model"],
        prompt=prompt, timeout=timeout, max_tokens=max_tokens, provider=llm["provider"],
        system=_CASTING_SYSTEM, schema=_CASTING_SCHEMA, schema_name="voice_casting",
    )
    wanted = {c["name"]: c for c in characters}
    result = []
    for item in parsed.get("voices") or []:
        name = str(item.get("name") or "").strip()
        if name not in wanted:
            continue
        instruct = voice_instruct(item.get("gender"), item.get("age"), item.get("pitch"))
        result.append({
            "name": name, "character_id": wanted[name].get("id"),
            "gender": instruct.split(",")[0], "instruct": instruct,
            "description": str(item.get("description") or "").strip()[:240],
        })
    return result


def review_speakers(*, title: str, units: list[dict], roster: list[str], llm: dict,
                    timeout: float = 600, max_tokens: int = 4096) -> list[dict]:
    """units: build_speaker_units output plus 'speaker' for dialogue units."""
    lines = []
    for unit in units:
        text = " ".join(str(unit["text"]).split())[:400]
        if unit.get("dialogue_candidate"):
            lines.append(f"[D {unit['index']} | {unit.get('speaker') or '?'}] {text}")
        else:
            lines.append(f"[N {unit['index']}] {text}")
    prompt = (
        f"BOOK: {title}\nROSTER: {', '.join(roster[:40]) or '(none)'}\n\n"
        "Return JSON with only the corrections.\n\n" + "\n".join(lines)
    )
    parsed = _chat(
        base_url=llm["base_url"], api_key=llm["api_key"], model=llm["model"],
        prompt=prompt, timeout=timeout, max_tokens=max_tokens, provider=llm["provider"],
        system=_REVIEW_SYSTEM, schema=_REVIEW_SCHEMA, schema_name="speaker_review",
    )
    valid_ids = {int(unit["index"]) for unit in units if unit.get("dialogue_candidate")}
    names = {name.lower(): name for name in roster}
    corrections = []
    for item in parsed.get("corrections") or []:
        try:
            unit_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        speaker = str(item.get("speaker") or "").strip()
        if unit_id not in valid_ids:
            continue
        if speaker and speaker.lower() not in names:
            continue  # never accept a name that is not in the roster
        corrections.append({
            "unit_index": unit_id,
            "speaker": names.get(speaker.lower(), ""),
            "reason": str(item.get("reason") or "")[:200],
        })
    return corrections


__all__ = ["suggest_voices", "review_speakers", "voice_instruct", "LLMAnalysisError"]
