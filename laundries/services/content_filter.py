"""Objectionable-language filter for text customers publish (laundry reviews).

App Store Guideline 1.2 requires apps with user-generated content to filter
objectionable material before it is posted. Reviews are shown on the public
website, so a comment with profanity or slurs is rejected at submission and
the customer is asked to rephrase.

Words match whole tokens only (after undoing common character swaps such as
"sh1t" or "f*ck"), so ordinary words that merely contain one, like
"Scunthorpe" or "assessment", pass.
"""
import re

# Stems match the word and its inflections ("fucking", "shitty").
_BLOCKED_STEMS = (
    'fuck', 'shit', 'bitch', 'cunt', 'motherfuck', 'nigg', 'fagg', 'whore',
    'slut', 'wank', 'retard', 'bullshit', 'dickhead', 'asshole', 'arsehole',
)
_BLOCKED_WORDS = (
    'ass', 'arse', 'bastard', 'cock', 'dick', 'pussy', 'twat', 'fag', 'prick',
    'piss', 'pissed', 'damn', 'crap', 'douche', 'jackass', 'dumbass', 'kike',
    'spic', 'chink', 'coon', 'tranny',
)

_SWAPS = str.maketrans({'@': 'a', '4': 'a', '3': 'e', '1': 'i', '!': 'i', '0': 'o', '$': 's', '5': 's', '7': 't'})
_MASKED = re.compile(r'(?<=\w)[*#._-]+(?=\w)')  # "f*ck", "s.h.i.t" -> "fck", "shit"
_PATTERN = re.compile(
    r'\b(?:' + '|'.join(_BLOCKED_STEMS) + r')\w*\b'
    r'|\b(?:' + '|'.join(_BLOCKED_WORDS) + r')s?\b'
    r'|\bf\W*c\W*k\w*\b|\bf\W*u\W*k\w*\b'
)


_SPACED_LETTERS = re.compile(r'(?<=\b\w) (?=\w\b)')  # "f u c k" -> "fuck"
_STRETCHED = re.compile(r'(\w)\1{2,}')  # "fuuuck" -> "fuck"; "class" is untouched


def contains_objectionable_language(text: str) -> bool:
    if not text:
        return False
    normalized = _MASKED.sub('', text.lower().translate(_SWAPS))
    normalized = _SPACED_LETTERS.sub('', normalized)
    return bool(_PATTERN.search(normalized) or _PATTERN.search(_STRETCHED.sub(r'\1', normalized)))
