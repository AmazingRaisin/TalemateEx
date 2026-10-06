"""
Private parts of messages: parts of a message only some characters perceive
(e.g. a whisper, or a note slipped to someone), set by the user in the chat.

In text they are marked as ⟦Sarah|Doug⟧the private part⟦/⟧. When a message
with such parts is added to the history, its text becomes the public version
(without them) and the marked up text is kept in its meta (`private_text`).
So everything that isn't built for a specific character (summaries, the
narrator, ...) only gets the public text, and prompts for the characters the
parts are for (and the one who wrote it) get them, as they were written.
"""

import difflib
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from talemate.scene_message import SceneMessage

__all__ = [
    "PRIVATE_PATTERN",
    "has_private_parts",
    "strip_keeping_private_parts",
    "reformat_keeping_private_parts",
    "tidy_private_parts",
    "private_parts",
    "public_text",
    "text_for",
    "apply_private_markup",
    "apply_display_private_markup",
    "private_only_hidden_from",
]

OPEN = "⟦"
CLOSE = "⟧"
END = "⟦/⟧"

# ⟦names⟧content⟦/⟧ (names separated by |)
PRIVATE_PATTERN = re.compile(r"⟦(?!/)([^⟧]*)⟧([\s\S]*?)⟦/⟧")


def has_private_parts(text: str | None) -> bool:
    return bool(text) and PRIVATE_PATTERN.search(text) is not None


def _names(group: str) -> list[str]:
    return [name.strip() for name in group.split("|") if name.strip()]


def private_parts(text: str) -> list[tuple[list[str], str]]:
    """The private parts of a text: (who they are for, the text)."""

    return [(_names(m.group(1)), m.group(2)) for m in PRIVATE_PATTERN.finditer(text)]


def _closes(text: str, index: int) -> bool:
    """A closing quote / asterisk at the index."""

    return (
        index < len(text)
        and text[index] in '"*'
        and (index + 1 >= len(text) or text[index + 1] in " \n.,!?;:")
    )


def _opens(before: str) -> bool:
    """The text before ends with an opening quote / asterisk."""

    return (
        bool(before)
        and before[-1] in '"*'
        and (len(before) == 1 or before[-2] in " \n")
    )


def _replace(text: str, replacement) -> str:
    """
    Replaces the private parts (`replacement(names, content)`, None to leave a
    part out). Leaving a part out also removes the space it leaves behind.
    """

    result = []
    position = 0
    for match in PRIVATE_PATTERN.finditer(text):
        before = text[position : match.start()]
        value = replacement(_names(match.group(1)), match.group(2))
        if value is None:
            after_start = match.end()
            space_before = before.endswith(" ")
            space_after = text[after_start : after_start + 1] == " "
            if space_before and space_after:
                # "a <part> b" -> "a b"
                before = before[:-1]
            elif space_before and (
                after_start >= len(text)
                or text[after_start] in ".,!?;:\n"
                or _closes(text, after_start)
            ):
                # "a <part>." -> "a.", '"a <part>"' -> '"a"'
                before = before[:-1]
            elif space_after and (
                not before or before.endswith("\n") or _opens(before)
            ):
                # "<part> b" -> "b", '"<part> b"' -> '"b"'
                position = after_start + 1
                result.append(before)
                continue
            value = ""
        result.append(before)
        result.append(value)
        position = match.end()
    result.append(text[position:])
    return "".join(result)


def _plain(text: str) -> tuple[str, list[tuple[int, int, str]]]:
    """The text as it reads (private parts inline), and where the parts are."""

    plain = []
    parts = []
    length = 0
    position = 0
    for match in PRIVATE_PATTERN.finditer(text):
        before = text[position : match.start()]
        plain.append(before)
        length += len(before)
        content = match.group(2)
        parts.append((length, length + len(content), match.group(1)))
        plain.append(content)
        length += len(content)
        position = match.end()
    plain.append(text[position:])
    return "".join(plain), parts


def _marked_up(plain: str, parts: list[tuple[int, int, str]]) -> str:
    result = []
    position = 0
    for start, end, names in parts:
        if end <= start:
            continue
        result.append(plain[position:start])
        result.append(f"{OPEN}{names}{CLOSE}{plain[start:end]}{END}")
        position = end
    result.append(plain[position:])
    return "".join(result)


def strip_keeping_private_parts(text: str, strip) -> str:
    """
    Cuts a text with private parts the way `strip` (cutting off the end, e.g.
    an unfinished sentence) cuts it as it reads, keeping the parts marked up.
    Text marked private is never cut: whoever marked it chose where it ends.
    """

    plain, parts = _plain(text)
    stripped = strip(plain)
    if not plain.startswith(stripped):
        # not a cut off end, leave it be
        return text

    keep = max([len(stripped)] + [end for _, end, _ in parts])
    return _marked_up(
        plain[:keep], [(start, min(end, keep), names) for start, end, names in parts]
    )


def _position_map(before: str, after: str) -> list[int | None]:
    """Where each character of `before` is in `after` (None: gone)."""

    positions: list[int | None] = [None] * len(before)
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    for tag, i1, i2, j1, _ in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                positions[i1 + offset] = j1 + offset
    return positions


def reformat_keeping_private_parts(text: str, reformat) -> str:
    """
    Runs a formatting fix (quotes, asterisks, spacing, ...) on a text with
    private parts as it reads, and puts the parts back around the same words.
    """

    plain, parts = _plain(text)
    formatted = reformat(plain)
    positions = _position_map(plain, formatted)

    moved = []
    for start, end, names in parts:
        kept = [positions[i] for i in range(start, end) if positions[i] is not None]
        if kept:
            moved.append((kept[0], kept[-1] + 1, names))
    return tidy_private_parts(_marked_up(formatted, moved))


# a part filling a quote / an asterisk pair: '*⟦Sarah⟧winks⟦/⟧*'
_WRAPPED_PART = re.compile(
    r'(?<!\S)(["*])⟦(?!/)([^⟧]*)⟧([\s\S]*?)⟦/⟧\1(?=$|[\s.,!?;:])'
)


def tidy_private_parts(text: str) -> str:
    """
    Whitespace at the edges of a private part goes outside it (formatting
    fixes put spaces around quotes, e.g. ⟦Sarah⟧ "Psst." ⟦/⟧), without
    doubling the spaces next to it. Quotes / asterisks around just the part
    go inside it, so the public text isn't left with an empty pair.
    """

    text = _WRAPPED_PART.sub(
        lambda m: f"{OPEN}{m.group(2)}{CLOSE}{m.group(1)}{m.group(3)}{m.group(1)}{END}",
        text,
    )

    def outside(match: re.Match) -> str:
        names, content = match.group(1), match.group(2)
        if not content.strip():
            return match.group(0)
        lead = content[: len(content) - len(content.lstrip())]
        trail = content[len(content.rstrip()) :]
        return f"{lead}{OPEN}{names}{CLOSE}{content.strip()}{END}{trail}"

    tidied = PRIVATE_PATTERN.sub(outside, text)
    tidied = re.sub(r" {2,}(?=⟦(?!/))", " ", tidied)
    tidied = re.sub(r"(?<=⟦/⟧) {2,}", " ", tidied)
    if text == text.rstrip():
        tidied = tidied.rstrip()
    return tidied


def public_text(text: str) -> str:
    """The text without its private parts."""

    return _replace(text, lambda names, content: None)


def text_for(
    text: str, viewer: str | None, speaker: str | list[str] | None = None
) -> str:
    """
    The text as a character perceives it: the private parts it (or the one
    who wrote them, the members of a group whose message it is) may see as
    they were written, the others left out.

    For a group's prompt (talemate.groups) the parts all / any of its
    members may see.
    """

    from talemate.groups import perspective_of

    speakers = speaker if isinstance(speaker, list) else [speaker]
    perspective = perspective_of(viewer)
    viewers = perspective.members if perspective else [viewer]

    def sees(name: str | None, names: list[str]) -> bool:
        return bool(name) and (
            name in names or (name in speakers and _for_all_writers(names, speakers))
        )

    def replacement(names: list[str], content: str):
        if perspective:
            visible = perspective.combine(sees(name, names) for name in viewers)
        else:
            visible = sees(viewer, names)
        return content if visible else None

    return _replace(text, replacement)


def _for_all_writers(names: list[str], speakers: list[str]) -> bool:
    """
    Whether all who wrote a message perceive a part of it: unless the part is
    for some of them only (a group's message, talemate.groups).
    """

    return not any(speaker in names for speaker in speakers)


def _speaker(message: "SceneMessage") -> str | None:
    from talemate.scene_message import CharacterMessage

    if isinstance(message, CharacterMessage):
        return message.character_name
    return None


def _speakers(message: "SceneMessage") -> list[str]:
    """Who wrote it: its character, or a group's members (talemate.groups)."""

    from talemate.groups import message_members

    return message_members(message)


def _body(message: "SceneMessage", text: str) -> str:
    speaker = _speaker(message)
    if speaker and text.startswith(f"{speaker}:"):
        return text[len(speaker) + 1 :]
    return text


def apply_private_markup(message: "SceneMessage") -> bool:
    """
    A message (being added to the history, or edited) with private parts in
    its text: the text becomes the public version, the marked up text goes to
    its meta. Without private parts, any earlier ones are dropped.

    A message that is private entirely is only for the characters its parts
    are for (and its speaker).
    """

    text = message.message
    meta = message.meta or {}

    if not has_private_parts(text):
        if "private_text" in meta:
            for key in ("private_text", "private_only", "private_viewers"):
                meta.pop(key, None)
        return False

    text = tidy_private_parts(text)
    speaker = _speaker(message)
    speakers = _speakers(message)
    viewers = set()
    for names, _ in private_parts(text):
        viewers.update(names)
        if _for_all_writers(names, speakers):
            viewers.update(speakers)

    message.message = public_text(text)
    message.set_meta(private_text=text)

    # nothing left but e.g. the quotes it was wrapped in
    if not re.sub(r'["*\s]', "", _body(message, message.message)):
        if speaker:
            message.message = f"{speaker}:"
        message.set_meta(private_only=True, private_viewers=sorted(viewers))
    else:
        message.meta.pop("private_only", None)
        message.meta.pop("private_viewers", None)

    return True


def apply_display_private_markup(message: "SceneMessage"):
    """
    The private parts of a message's display revision (narrative omniscience):
    its text becomes the public version, the marked up text goes to the meta
    (`display_private_text`).
    """

    text = getattr(message, "display_message", None)
    meta = message.meta or {}

    if not text or not has_private_parts(text):
        meta.pop("display_private_text", None)
        return

    text = tidy_private_parts(text)
    message.display_message = public_text(text)
    message.set_meta(display_private_text=text)


def private_only_hidden_from(message, viewer: str | None) -> bool:
    """
    A message that is private entirely, and not for the viewer (for a group's
    prompt, not for all / any of its members, talemate.groups).
    """

    meta = getattr(message, "meta", None) or {}
    if not meta.get("private_only"):
        return False

    from talemate.groups import perspective_of

    viewers = meta.get("private_viewers") or []
    perspective = perspective_of(viewer)
    if perspective:
        return not perspective.combine(m in viewers for m in perspective.members)
    return not viewer or viewer not in viewers
