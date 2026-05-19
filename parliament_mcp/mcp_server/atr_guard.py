"""Pre-dispatch guard that screens MCP tool input against the open Agent Threat Rules corpus.

ATR (https://github.com/Agent-Threat-Rule/agent-threat-rules) is an MIT-licensed open
detection-rule standard for LLM agent attacks. This module bundles a small, conservative
subset of the corpus that targets the canonical attack shapes an MCP server is likely to
see in tool-input arguments: direct prompt injection, jailbreak attempts, system-prompt
override, fake system-tag delimiters, indirect prompt injection via embedded URLs, and
markdown-image data exfiltration directives.

Design choices for this guard:

1. Default behaviour is log-and-tag, not block. Parliamentary data is sensitive and we
   would rather flag a suspicious query for human review than refuse a legitimate
   research request.
2. The rule subset is embedded as compiled regex patterns. No PyYAML dependency, no
   network fetch at import time, no startup latency, no new pip dependency.
3. The full ATR corpus is much larger (348+ rules across nine categories). We bundle
   only a curated subset that is robust on Hansard-flavoured input, where words like
   "national security", "cyber attack", and "AI policy" appear legitimately in user
   queries. The selected patterns require specific instruction-override structure that
   does not occur in legitimate research queries.
4. The guard can be disabled by setting ATR_GUARD_DISABLED=1 in the environment.

Reference: ATR v2.1.4 / 348 rules (May 2026). The rule IDs cited below are stable.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from enum import Enum

from pydantic.fields import FieldInfo

logger = logging.getLogger(__name__)

# Maximum number of characters of a matched substring we are willing to record. Keeps
# logs free of full captured attack payloads while still leaving enough context for an
# operator to identify the rule that fired.
MAX_MATCH_SNIPPET_LEN = 80


class Severity(str, Enum):
    """Severity levels matching the ATR rule schema."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class GuardRule:
    """A single pre-compiled detection rule."""

    rule_id: str
    title: str
    severity: Severity
    pattern: re.Pattern[str]
    category: str


@dataclass(frozen=True)
class GuardMatch:
    """A rule match against an input string."""

    rule_id: str
    title: str
    severity: Severity
    category: str
    matched_text: str


# Embedded ATR rule subset. Each entry is a tuple of:
#   (rule_id, title, severity, category, regex_source)
# Patterns are conservative subsets of the full ATR YAML rules. They are chosen for
# robustness on the kind of text an MCP client (Claude Desktop, Cursor, etc.) would
# pass as a tool-input query against parliamentary data.
_RULE_DEFINITIONS: tuple[tuple[str, str, Severity, str, str], ...] = (
    # ATR-2026-00001 -- Direct prompt injection via instruction-override verbs
    # paired with target nouns. This is the canonical "ignore previous instructions"
    # family of patterns. The verb list and noun list are both deliberately narrow
    # so that "please ignore my previous suggestion" (a benign correction) does not
    # match because "suggestion" is not a target noun.
    (
        "ATR-2026-00001",
        "Direct prompt injection via instruction override",
        Severity.HIGH,
        "prompt-injection",
        r"(?i)\b(ignore|disregard|forget|override|bypass|nullify|revoke|discard)"
        r"\s+(all\s+)?(the\s+|your\s+|any\s+|these\s+|those\s+)?"
        r"(previous|prior|above|earlier|preceding|original|existing|current|initial|default)?"
        r"\s*(instructions?|prompts?|guidelines?|directives?|constraints?|guardrails?|safeguards?|"
        r"restrictions?|protocols?|policies)",
    ),
    # ATR-2026-00001 layer 1e -- System prompt extraction request. Allows stacking
    # adjectives like "your hidden system instructions" where multiple qualifiers
    # precede the target noun.
    (
        "ATR-2026-00001",
        "System prompt extraction request",
        Severity.HIGH,
        "prompt-injection",
        r"(?i)(reveal|show|display|output|print|repeat)\s+(me\s+)?(your|the)\s+"
        r"((secret|hidden|internal|original|initial|complete|full)\s+)*"
        r"(system\s+)?(instructions?|prompts?|configuration|directives?)",
    ),
    # ATR-2026-00001 layer 7 -- Fake system / instruction delimiters
    (
        "ATR-2026-00001",
        "Fake system-level delimiter tags",
        Severity.HIGH,
        "prompt-injection",
        r"(?i)(\[\s*(SYSTEM|INST|INSTRUCTION|SYS|ADMIN|ROOT|OVERRIDE)\s*\]"
        r"|<<\s*(SYS|SYSTEM|INST)\s*>>"
        r"|\{\{\s*(SYSTEM|INSTRUCTION|OVERRIDE)\s*\}\})",
    ),
    # ATR-2026-00001 layer 8 -- Restriction removal via pretend / imagine
    (
        "ATR-2026-00001",
        "Hypothetical restriction removal",
        Severity.HIGH,
        "prompt-injection",
        r"(?i)(pretend|act\s+as\s+if|imagine|suppose)\s+(you\s+)?(are|have|were|had)\s+"
        r"(no|zero|removed|disabled|free\s+from|without)\s+"
        r"(restrictions?|limits?|limitations?|guardrails?|safety|filters?|constraints?|content\s+policy)",
    ),
    # ATR-2026-00004 -- System prompt override via ChatML tokens
    (
        "ATR-2026-00004",
        "ChatML or model special-token injection",
        Severity.CRITICAL,
        "prompt-injection",
        r"(?i)<\|?(im_start|im_end|endoftext|begin_of_text|start_header_id|end_header_id|eot_id)\|?>",
    ),
    # ATR-2026-00004 -- New system prompt assignment with delimiter
    (
        "ATR-2026-00004",
        "New system prompt assignment with delimiter",
        Severity.CRITICAL,
        "prompt-injection",
        r"(?i)\b(new|updated|revised|replacement|corrected|overriding)\s+"
        r"system\s+(prompt|message|instructions?|directives?)\s*[:=\-]",
    ),
    # ATR-2026-00003 -- Jailbreak attempt via DAN / developer mode personas
    (
        "ATR-2026-00003",
        "Known jailbreak persona invocation",
        Severity.HIGH,
        "prompt-injection",
        r"(?i)\b(DAN|do\s+anything\s+now|developer\s+mode|unfiltered\s+mode|"
        r"jailbreak\s+mode|godmode|sudo\s+mode)\b",
    ),
    # ATR-2026-00002 -- Indirect prompt injection canonical marker phrases.
    # These are imperative phrases attackers embed in documents and tool responses
    # to redirect an LLM that retrieved them. Their presence in a user search query
    # against Hansard is a strong signal of either an attack or a research query
    # quoting one verbatim.
    (
        "ATR-2026-00002",
        "Indirect prompt injection canonical phrase",
        Severity.HIGH,
        "prompt-injection",
        r"(?i)(when\s+you\s+see\s+this[,\s]+|if\s+you\s+are\s+an\s+(AI|LLM)[,\s]+)"
        r"(immediately|first|always|please)?\s*"
        r"(send|forward|email|post|exfiltrate|transmit|leak)",
    ),
    # ATR-2026-00261 -- Markdown image exfiltration directive in user input.
    # An attacker would not typically place this in a search query, but if a user
    # pastes a captured attack payload it is worth flagging.
    (
        "ATR-2026-00261",
        "Markdown image exfiltration directive",
        Severity.HIGH,
        "context-exfiltration",
        r"(?i)(replace|substitute)\s+\[?[A-Z_]{3,}\]?\s+with\s+(the\s+)?"
        r"(base64|hex|url)[\s-]?encoded\s+"
        r"(previous\s+message|conversation|user.{0,20}(email|token|secret|api[\s-]?key))",
    ),
    # ATR-2026-00161 -- IMPORTANT-tag tool poisoning marker as it would appear if
    # an attacker pasted a captured payload into a query.
    (
        "ATR-2026-00161",
        "IMPORTANT-tag tool poisoning marker",
        Severity.CRITICAL,
        "tool-poisoning",
        r"(?i)<important>[\s\S]{0,600}?\b(read|send|exec|fetch|invoke|extract|include|forward|upload)"
        r"\b[\s\S]{0,300}?</important>",
    ),
    # ATR-2026-00021 -- Sensitive credential file references inside an attack payload
    # that an operator could copy into a query while investigating an incident. To
    # avoid false positives on stand-alone words like "known_hosts" or "mcp.json" that
    # may appear in legitimate technical discussion, we require a path-style anchor
    # (a leading slash, ~/, or .ssh/) before the sensitive token.
    (
        "ATR-2026-00021",
        "Sensitive credential path embedded in input",
        Severity.HIGH,
        "context-exfiltration",
        r"((?:^|[\s\"'])(?:~|\.)?/?(?:\.ssh/|\.aws/|\.kube/|\.docker/)?"
        r"(id_rsa|id_dsa|id_ed25519|id_ecdsa)\b"
        r"|/etc/(passwd|shadow|ssl/private)"
        r"|/proc/self/environ)",
    ),
)


def _compile_rules() -> tuple[GuardRule, ...]:
    """Compile rule definitions at import time. Silently skip any rule that fails to compile."""
    compiled: list[GuardRule] = []
    for rule_id, title, severity, category, source in _RULE_DEFINITIONS:
        try:
            pattern = re.compile(source)
        except re.error as exc:
            logger.warning("ATR guard skipped rule %s due to compile error: %s", rule_id, exc)
            continue
        compiled.append(
            GuardRule(
                rule_id=rule_id,
                title=title,
                severity=severity,
                pattern=pattern,
                category=category,
            )
        )
    return tuple(compiled)


# Compile once at import. If a future ATR rule has a regex that does not compile under
# Python re, we log and continue — guard load failure must never prevent the MCP server
# from starting.
RULES: tuple[GuardRule, ...] = _compile_rules()


def is_enabled() -> bool:
    """Return True if the ATR guard is enabled.

    The guard is enabled by default and can be disabled with ATR_GUARD_DISABLED=1.
    """
    return os.environ.get("ATR_GUARD_DISABLED", "").strip().lower() not in {"1", "true", "yes", "on"}


def screen(text: str) -> list[GuardMatch]:
    """Screen a single input string against the bundled rule subset.

    Returns a list of GuardMatch entries, one per matched rule. The list is empty if
    no rule matches or if the guard is disabled. The function is pure and side-effect
    free; logging is left to the caller so this can be used from sync and async code.
    """
    if not is_enabled() or not text:
        return []

    matches: list[GuardMatch] = []
    for rule in RULES:
        hit = rule.pattern.search(text)
        if hit is None:
            continue
        # Keep the matched span short so we do not echo entire payloads into logs.
        snippet = hit.group(0)
        if len(snippet) > MAX_MATCH_SNIPPET_LEN:
            snippet = snippet[: MAX_MATCH_SNIPPET_LEN - 3] + "..."
        matches.append(
            GuardMatch(
                rule_id=rule.rule_id,
                title=rule.title,
                severity=rule.severity,
                category=rule.category,
                matched_text=snippet,
            )
        )
    return matches


def screen_kwargs(kwargs: dict[str, object]) -> list[GuardMatch]:
    """Screen all string-valued tool kwargs. Non-string values are ignored.

    Pydantic FieldInfo defaults are skipped (they appear when a tool is called without
    binding a value to a parameter); only actual user-supplied strings are screened.
    """
    if not is_enabled():
        return []

    aggregated: list[GuardMatch] = []
    for key, value in kwargs.items():
        if isinstance(value, FieldInfo):
            continue
        if not isinstance(value, str):
            continue
        for match in screen(value):
            logger.warning(
                "ATR guard match on parameter '%s': rule=%s severity=%s category=%s",
                key,
                match.rule_id,
                match.severity.value,
                match.category,
            )
            aggregated.append(match)
    return aggregated


def rule_count() -> int:
    """Return the number of successfully compiled rules. Used by health checks."""
    return len(RULES)
