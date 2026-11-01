"""Interpret short replies to an already described pending BOB action."""
import re


# Keep this grammar aligned with AgentBOBVoiceApproval in AgentFlow iOS.
# Match the whole reply so qualifications, negations and quoted speech cannot approve.
_PHRASES = {
    'approve': (
        'yes|yeah|yep|yup|sure|ok|okay|absolutely|certainly|of course|'
        'sounds good|that sounds good|please do|go ahead|do it|do that|'
        'proceed|approve it|approve this change'
    ),
    'reject': 'no|nope|nah|cancel it|cancel this change|reject it',
}
_PATTERNS = {
    decision: re.compile(rf'(?:please )?(?:{phrases})(?: (?:please )?(?:{phrases}))*(?: please| thanks| thank you)?')
    for decision, phrases in _PHRASES.items()
}


def voice_decision(text):
    normalized = ' '.join(re.findall(r"[a-z0-9]+", text.lower()))
    return next((decision for decision, pattern in _PATTERNS.items()
                 if pattern.fullmatch(normalized)), None)
