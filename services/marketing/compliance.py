"""Fair Housing and advertising checks for marketing copy.

Real estate advertising is regulated in ways general marketing tools ignore.
The Fair Housing Act prohibits stating a preference based on race, color,
religion, sex, familial status, national origin, or disability, and HUD reads
"stating a preference" broadly enough that ordinary-sounding phrases qualify.
"Perfect for families" expresses a familial-status preference. "Safe
neighborhood" and "good schools" are long-established proxies for race. None of
that is obvious to an agent writing a friendly email at 9pm, and none of it is
obvious to a language model optimizing for warmth.

Two passes run over every template:

1. A deterministic pattern sweep, here. Fast, free, runs on every keystroke-ish
   save, and catches the phrases that generate actual complaints.
2. An AI review in ``review.py``, which catches phrasing this list does not
   know about.

Findings are ``block`` or ``warn``. Blocking stops the template from being used
at all. Warnings need an explicit acknowledgement from the agent, which is
recorded on the template, because "a human looked at this and accepted it" is
the difference between a mistake and a pattern of neglect.

This is not legal advice and does not make a template lawful. It removes the
well-known landmines and creates a record that the agent was warned.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Optional

# Shared so a new copy field is scanned by the linter and collected for review
# together, rather than silently by only one of them.
from services.marketing.blocks import SCANNED_FIELDS as _SCANNED_FIELDS

SEVERITY_BLOCK = 'block'
SEVERITY_WARN = 'warn'

# Protected classes, named the way the statute names them, so findings can
# explain which one is implicated.
CLASS_FAMILIAL = 'familial status'
CLASS_RELIGION = 'religion'
CLASS_RACE = 'race, color, or national origin'
CLASS_DISABILITY = 'disability'
CLASS_SEX = 'sex'
CLASS_PROXY = 'racial proxy'
# Not a protected class. Industry convention an agent should follow anyway.
CLASS_CONVENTION = 'industry convention'


@dataclass(frozen=True)
class Rule:
    pattern: str
    severity: str
    protected_class: str
    message: str
    suggestion: str

    def compiled(self) -> re.Pattern:
        return re.compile(self.pattern, re.IGNORECASE)


@dataclass
class Finding:
    severity: str
    matched_text: str
    protected_class: str
    message: str
    suggestion: str
    # Which block the phrase came from, so the studio can highlight it. None
    # when the phrase is in the subject line.
    block_index: Optional[int] = None
    field: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


# Word-boundary helper. Kept explicit rather than wrapping every pattern so
# rules that need phrase-internal matching can opt out.
def _w(body: str) -> str:
    return rf'\b{body}\b'


RULES: tuple[Rule, ...] = (
    # ---- Familial status -------------------------------------------------
    Rule(
        pattern=_w(r'(?:perfect|great|ideal|wonderful|excellent)\s+for\s+(?:a\s+)?famil(?:y|ies)'),
        severity=SEVERITY_BLOCK,
        protected_class=CLASS_FAMILIAL,
        message='States a preference for families, which is familial-status discrimination.',
        suggestion='Describe the property instead: "four bedrooms and a fenced yard".',
    ),
    Rule(
        pattern=_w(r'famil(?:y|ies)[- ]friendly'),
        severity=SEVERITY_BLOCK,
        protected_class=CLASS_FAMILIAL,
        message='"Family friendly" signals a familial-status preference.',
        suggestion='Name the feature that made you say it: a park nearby, a cul-de-sac, a big yard.',
    ),
    Rule(
        pattern=_w(r'(?:no|not for)\s+(?:kids|children)|child(?:ren)?[- ]free|adults?\s+only'),
        severity=SEVERITY_BLOCK,
        protected_class=CLASS_FAMILIAL,
        message='Excludes households with children.',
        suggestion='Remove it. Age restrictions are lawful only for qualified senior housing.',
    ),
    Rule(
        pattern=_w(r'empty[- ]nesters?|bachelor(?:ette)?\s+pad|newlyweds?|starter\s+famil'),
        severity=SEVERITY_WARN,
        protected_class=CLASS_FAMILIAL,
        message='Describes a household type rather than the property.',
        suggestion='Describe the space: "low maintenance and single story".',
    ),
    Rule(
        pattern=_w(r'perfect\s+(?:starter|first)\s+home\s+for\s+(?:young|new)\s+\w+'),
        severity=SEVERITY_WARN,
        protected_class=CLASS_FAMILIAL,
        message='Targets buyers by life stage.',
        suggestion='Lead with price and size instead of who you imagine buying it.',
    ),

    # ---- Religion --------------------------------------------------------
    Rule(
        pattern=_w(r'christian|catholic|jewish|muslim|mormon|buddhist|hindu'),
        severity=SEVERITY_BLOCK,
        protected_class=CLASS_RELIGION,
        message='Names a religion in property advertising.',
        suggestion='Remove the reference.',
    ),
    Rule(
        # The place of worship is usually named, as in "walking distance to
        # St. Mary's church", so allow a few words between the two halves.
        pattern=(
            r'\b(?:walk(?:ing)?(?:\s+distance)?|close|near|steps|minutes|convenient)'
            r'(?:\s+(?:to|from|by))?\s+'
            r"(?:[\w'’.\-]+\s+){0,3}"
            r'(?:church|synagogue|mosque|temple|parish|chapel|cathedral)\b'
        ),
        severity=SEVERITY_BLOCK,
        protected_class=CLASS_RELIGION,
        message='Using a house of worship as a selling point implies a religious preference.',
        suggestion='Reference a neutral landmark, or just say the neighborhood is walkable.',
    ),

    # ---- Race, color, national origin -----------------------------------
    Rule(
        pattern=_w(r'exclusive\s+(?:neighborhood|community|area|enclave)'),
        severity=SEVERITY_WARN,
        protected_class=CLASS_RACE,
        message='"Exclusive" reads as who is kept out.',
        suggestion='Say what is actually true: "custom homes on larger lots".',
    ),
    Rule(
        pattern=_w(r'(?:integrated|mixed|changing|transitional)\s+(?:neighborhood|community|area)'),
        severity=SEVERITY_BLOCK,
        protected_class=CLASS_RACE,
        message='Describes a neighborhood by its racial makeup.',
        suggestion='Remove it. Composition of the neighborhood is never a selling point.',
    ),
    Rule(
        pattern=_w(r'(?:hispanic|latino|asian|black|white|anglo|oriental|ethnic)\s+(?:neighborhood|community|area|families|buyers)'),
        severity=SEVERITY_BLOCK,
        protected_class=CLASS_RACE,
        message='Identifies a neighborhood or audience by race or national origin.',
        suggestion='Remove it.',
    ),
    Rule(
        pattern=_w(r'traditional\s+neighborhood|established\s+ethnic'),
        severity=SEVERITY_WARN,
        protected_class=CLASS_RACE,
        message='Historically used as coded language about who lives there.',
        suggestion='Describe the housing stock or the era it was built.',
    ),

    # ---- Disability ------------------------------------------------------
    Rule(
        pattern=_w(r'able[- ]bodied|no\s+wheelchairs?|not\s+(?:suitable|suited)\s+for\s+(?:the\s+)?(?:disabled|handicapped)|must\s+be\s+able\s+to\s+climb'),
        severity=SEVERITY_BLOCK,
        protected_class=CLASS_DISABILITY,
        message='Excludes people with disabilities.',
        suggestion='State the physical fact instead: "the only bedroom is upstairs".',
    ),
    # ---- Sex -------------------------------------------------------------
    Rule(
        pattern=_w(r'(?:perfect|ideal|great)\s+for\s+(?:a\s+)?(?:single\s+)?(?:man|woman|men|women|guy|girl|lady|gentleman)s?'),
        severity=SEVERITY_BLOCK,
        protected_class=CLASS_SEX,
        message='States a preference based on sex.',
        suggestion='Describe the property, not the buyer.',
    ),

    # ---- Convention ------------------------------------------------------
    Rule(
        pattern=_w(r'master\s+(?:bedroom|bath|suite)'),
        severity=SEVERITY_WARN,
        protected_class=CLASS_CONVENTION,
        message='Most MLSs and brokerages have moved to "primary".',
        suggestion='Use "primary bedroom", "primary bath", or "primary suite".',
    ),

    # ---- Racial proxies --------------------------------------------------
    # These are the ones agents push back on hardest and that HUD and NAR
    # guidance are clearest about. They stay warnings, not blocks, because the
    # agent may have a defensible non-coded use.
    Rule(
        pattern=_w(r'safe\s+(?:neighborhood|area|community|part\s+of\s+town)|crime[- ]free|low[- ]crime'),
        severity=SEVERITY_WARN,
        protected_class=CLASS_PROXY,
        message=(
            'Safety claims about a neighborhood are treated as a proxy for race, '
            'and you cannot substantiate them.'
        ),
        suggestion='Point to something verifiable, or leave it out.',
    ),
    Rule(
        pattern=_w(r'good\s+schools?|great\s+schools?|best\s+schools?|top[- ]rated\s+schools?|desirable\s+schools?'),
        severity=SEVERITY_WARN,
        protected_class=CLASS_PROXY,
        message='School quality claims are a documented proxy for race.',
        suggestion='Name the district and let the recipient look it up themselves.',
    ),
    Rule(
        pattern=_w(r'(?:nice(?:r|st)?|good|better|best|desirable|up[- ]and[- ]coming|bad|rough|sketchy)\s+(?:part\s+of\s+town|side\s+of\s+town|neighborhood)'),
        severity=SEVERITY_WARN,
        protected_class=CLASS_PROXY,
        message='Ranking neighborhoods invites a steering claim.',
        suggestion='Describe the property and let the buyer choose the area.',
    ),
    Rule(
        pattern=_w(r'you(?:\'ll| will)?\s+(?:fit|feel)\s+right\s+in|people\s+like\s+you'),
        severity=SEVERITY_WARN,
        protected_class=CLASS_PROXY,
        message='Implies the neighborhood is sorted by who belongs.',
        suggestion='Remove it.',
    ),
)

_COMPILED: tuple[tuple[re.Pattern, Rule], ...] = tuple(
    (rule.compiled(), rule) for rule in RULES
)

def scan_text(
    text: str,
    *,
    block_index: Optional[int] = None,
    field: Optional[str] = None,
) -> list[Finding]:
    """Every rule hit in one string."""
    if not text:
        return []

    findings: list[Finding] = []
    for pattern, rule in _COMPILED:
        for match in pattern.finditer(text):
            findings.append(Finding(
                severity=rule.severity,
                matched_text=match.group(0),
                protected_class=rule.protected_class,
                message=rule.message,
                suggestion=rule.suggestion,
                block_index=block_index,
                field=field,
            ))
    return _dedupe(findings)


def scan_blocks(blocks: list[dict], subject: str = '', preheader: str = '') -> list[Finding]:
    """Scan a whole template, keeping track of where each phrase came from."""
    findings: list[Finding] = []

    findings.extend(scan_text(subject, field='subject'))
    findings.extend(scan_text(preheader, field='preheader'))

    for index, block in enumerate(blocks):
        for name in _SCANNED_FIELDS:
            value = block.get(name)
            if value:
                findings.extend(scan_text(value, block_index=index, field=name))
        for item in block.get('items') or []:
            findings.extend(scan_text(item, block_index=index, field='items'))
        for stat in block.get('stats') or []:
            if stat.get('label'):
                findings.extend(scan_text(stat['label'], block_index=index, field='stats'))
        for entry in block.get('steps') or []:
            for name in ('title', 'text'):
                if entry.get(name):
                    findings.extend(
                        scan_text(entry[name], block_index=index, field='steps')
                    )

    return findings


def _dedupe(findings: list[Finding]) -> list[Finding]:
    """One finding per phrase per location. Repeated phrases are common."""
    seen: set[tuple] = set()
    out: list[Finding] = []
    for finding in findings:
        key = (
            finding.matched_text.lower(), finding.block_index,
            finding.field, finding.message,
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(finding)
    return out


def state_for(findings: list[Finding]) -> str:
    """Roll findings up to the template's ``compliance_state``."""
    if any(f.severity == SEVERITY_BLOCK for f in findings):
        return 'blocked'
    if findings:
        return 'warn'
    return 'pass'


def blocking(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == SEVERITY_BLOCK]


def warnings(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == SEVERITY_WARN]


def summarize(findings: list[Finding]) -> str:
    """One line for a tool result or a toast."""
    if not findings:
        return 'No Fair Housing issues found.'

    blocks = len(blocking(findings))
    warns = len(warnings(findings))
    parts = []
    if blocks:
        parts.append(f'{blocks} blocking issue{"s" if blocks != 1 else ""}')
    if warns:
        parts.append(f'{warns} warning{"s" if warns != 1 else ""}')
    return ' and '.join(parts) + '.'


# ---------------------------------------------------------------------------
# Sending prerequisites
# ---------------------------------------------------------------------------

def missing_org_disclosure(organization) -> list[str]:
    """Which required advertising or CAN-SPAM fields the org has not filled in.

    Sending is blocked while this is non-empty. CAN-SPAM requires a physical
    mailing address in every commercial message, and Texas advertising rules
    require the brokerage be identified, so there is no version of this feature
    that works without them.
    """
    missing: list[str] = []
    if not (getattr(organization, 'broker_name', None) or '').strip():
        missing.append('brokerage name')
    if not (getattr(organization, 'broker_license_number', None) or '').strip():
        missing.append('brokerage license number')
    if not (getattr(organization, 'broker_address', None) or '').strip():
        missing.append('brokerage mailing address')
    return missing
