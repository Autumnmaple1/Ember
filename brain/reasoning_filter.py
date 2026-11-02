"""Remove provider reasoning embedded in content, including split stream tags.

Character-authored <thought> monologue is a separate application feature.
Only public content from this filter may enter dialogue events or storage.
"""
import re


class ReasoningFilter:
    _tag = re.compile(r'<(/?)(think|reasoning)\s*>', re.IGNORECASE)
    _prefixes = ('<think', '</think', '<reasoning', '</reasoning')

    def __init__(self):
        self.pending = ''
        self.hidden = []

    def feed(self, text):
        self.pending += text
        public = []
        while self.pending:
            start = self.pending.find('<')
            if start < 0:
                if not self.hidden:
                    public.append(self.pending)
                self.pending = ''
                break
            if start:
                if not self.hidden:
                    public.append(self.pending[:start])
                self.pending = self.pending[start:]
            match = self._tag.match(self.pending)
            if match:
                closing, name = match.groups()
                name = name.lower()
                if not closing:
                    self.hidden.append(name)
                elif self.hidden and self.hidden[-1] == name:
                    self.hidden.pop()
                self.pending = self.pending[match.end():]
                continue
            lower = self.pending.lower()
            # Retain only a possible split reasoning tag, never its body.
            if any(prefix.startswith(lower) or
                   (lower.startswith(prefix) and not lower[len(prefix):].strip())
                   for prefix in self._prefixes):
                break
            if not self.hidden:
                public.append('<')
            self.pending = self.pending[1:]
        return ''.join(public)


def remove_reasoning(text):
    # An unfinished tag or reasoning block stays private at end-of-stream.
    return ReasoningFilter().feed(text) if text else text
