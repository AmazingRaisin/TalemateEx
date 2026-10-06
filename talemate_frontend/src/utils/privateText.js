// Private parts of messages: parts only some characters perceive
// (see talemate.private_text). In text they are marked as
// ⟦Sarah|Doug⟧the private part⟦/⟧, in the editor they are spans
// {start, end, characters} over the plain text.

const PRIVATE_PATTERN = /⟦(?!\/)([^⟧]*)⟧([\s\S]*?)⟦\/⟧/g;

export function hasPrivateParts(text) {
    return !!text && /⟦(?!\/)[^⟧]*⟧[\s\S]*?⟦\/⟧/.test(text);
}

// Marked up text -> {text, spans}
export function parsePrivateText(markup) {
    const spans = [];
    let text = '';
    let position = 0;
    const source = markup || '';
    PRIVATE_PATTERN.lastIndex = 0;
    let match;
    while ((match = PRIVATE_PATTERN.exec(source)) !== null) {
        text += source.slice(position, match.index);
        const characters = match[1].split('|').map(name => name.trim()).filter(Boolean);
        const start = text.length;
        text += match[2];
        if (characters.length && match[2].length) {
            spans.push({ start, end: text.length, characters });
        }
        position = match.index + match[0].length;
    }
    text += source.slice(position);
    return { text, spans };
}

// {text, spans} -> marked up text
export function serializePrivateText(text, spans) {
    const ordered = normalizeSpans(spans, (text || '').length);
    let result = '';
    let position = 0;
    for (const span of ordered) {
        result += text.slice(position, span.start);
        result += `⟦${span.characters.join('|')}⟧${text.slice(span.start, span.end)}⟦/⟧`;
        position = span.end;
    }
    return result + (text || '').slice(position);
}

// Sorted, within the text, without empty ones
export function normalizeSpans(spans, length) {
    return (spans || [])
        .map(span => ({
            start: Math.max(0, Math.min(span.start, length)),
            end: Math.max(0, Math.min(span.end, length)),
            characters: [...(span.characters || [])],
        }))
        .filter(span => span.end > span.start && span.characters.length)
        .sort((a, b) => a.start - b.start);
}

// The text changed from `before` to `after` (one edit): move the spans along.
// Typing inside a span grows it, typing right before or after it doesn't.
export function adjustSpans(before, after, spans) {
    if (!spans || !spans.length || before === after) {
        return spans || [];
    }
    before = before || '';
    after = after || '';
    let prefix = 0;
    const max = Math.min(before.length, after.length);
    while (prefix < max && before[prefix] === after[prefix]) {
        prefix++;
    }
    let suffix = 0;
    while (
        suffix < max - prefix &&
        before[before.length - 1 - suffix] === after[after.length - 1 - suffix]
    ) {
        suffix++;
    }
    const removedEnd = before.length - suffix;
    const inserted = after.length - prefix - suffix;
    const delta = after.length - before.length;

    const result = [];
    for (const span of spans) {
        let { start, end } = span;
        if (start >= removedEnd) {
            // after the change (typing right before the span isn't part of it)
            start += delta;
            end += delta;
        } else if (end <= prefix) {
            // before the change
        } else {
            // the change touches the span: new text is part of it when the
            // change starts inside it
            let newStart;
            if (start < prefix || start === prefix) {
                newStart = start;
            } else {
                newStart = prefix + inserted;
            }
            const newEnd = end > removedEnd ? end + delta : prefix + inserted;
            start = newStart;
            end = newEnd;
        }
        if (end > start) {
            result.push({ ...span, start, end });
        }
    }
    return result;
}

// The span a selection is in (all of the selection, or the cursor inside it)
export function spanAt(spans, start, end) {
    return (spans || []).findIndex(span => {
        if (end > start) {
            return span.start <= start && end <= span.end;
        }
        return span.start < start && start < span.end;
    });
}

// Set who sees [start, end): parts of other spans there are taken over
export function setSpanCharacters(spans, start, end, characters) {
    const result = [];
    for (const span of spans || []) {
        if (span.end <= start || span.start >= end) {
            result.push(span);
            continue;
        }
        if (span.start < start) {
            result.push({ ...span, end: start });
        }
        if (span.end > end) {
            result.push({ ...span, start: end });
        }
    }
    if (characters && characters.length && end > start) {
        result.push({ start, end, characters: [...characters] });
    }
    return result.sort((a, b) => a.start - b.start);
}

export function joinNames(names) {
    names = names || [];
    if (names.length <= 1) {
        return names.join('');
    }
    return names.slice(0, -1).join(', ') + ' and ' + names[names.length - 1];
}

function hashString(text) {
    let hash = 0;
    for (let i = 0; i < text.length; i++) {
        hash = (hash * 31 + text.charCodeAt(i)) | 0;
    }
    return Math.abs(hash);
}

function hexToRgb(color) {
    const match = /^#?([0-9a-f]{3}|[0-9a-f]{6})([0-9a-f]{2})?$/i.exec((color || '').trim());
    if (!match) {
        return null;
    }
    let hex = match[1];
    if (hex.length === 3) {
        hex = hex.split('').map(c => c + c).join('');
    }
    return [0, 2, 4].map(i => parseInt(hex.slice(i, i + 2), 16));
}

// A light tint for a span: the character's color, or (several characters)
// a color picked for that set of characters
export function privateTint(characters, colors = {}, alpha = 0.22) {
    characters = characters || [];
    if (characters.length === 1) {
        const rgb = hexToRgb(colors[characters[0]]);
        if (rgb) {
            return `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${alpha})`;
        }
    }
    const hue = hashString([...characters].sort().join('|')) % 360;
    return `hsla(${hue}, 70%, 55%, ${alpha})`;
}

function escapeHtml(text) {
    return text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

// HTML for the editor backdrop: the text (invisible) with the spans tinted
export function backdropHtml(text, spans, colors) {
    const ordered = normalizeSpans(spans, (text || '').length);
    let html = '';
    let position = 0;
    ordered.forEach((span, index) => {
        html += escapeHtml(text.slice(position, span.start));
        html += `<mark class="private-span" data-index="${index}" data-characters="${escapeHtml(span.characters.join('|'))}" style="background-color: ${privateTint(span.characters, colors)}">${escapeHtml(text.slice(span.start, span.end))}</mark>`;
        position = span.end;
    });
    html += escapeHtml((text || '').slice(position));
    // a trailing line break needs content to take up its line
    return html + String.fromCharCode(0x200b);
}

// Rendered message HTML with the private parts tinted. `render(text)` renders
// the text (e.g. SceneTextParser.parse), the parts are carried through it as
// markers and wrapped afterwards, piece by piece, so they may cross the
// rendered formatting (quotes, emphasis).
const START = String.fromCharCode(0xe000);
const START_END = String.fromCharCode(0xe001);
const STOP = String.fromCharCode(0xe002);

export function renderPrivateHtml(markup, render, colors) {
    const { text, spans } = parsePrivateText(markup);
    if (!spans.length) {
        return render(text);
    }
    let marked = '';
    let position = 0;
    spans.forEach((span, index) => {
        marked += text.slice(position, span.start) + START + index + START_END;
        marked += text.slice(span.start, span.end) + STOP;
        position = span.end;
    });
    marked += text.slice(position);

    const template = document.createElement('template');
    template.innerHTML = render(marked);

    const walker = document.createTreeWalker(template.content, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) {
        nodes.push(walker.currentNode);
    }

    let active = null;
    for (const node of nodes) {
        const value = node.nodeValue;
        if (active === null && !value.includes(START) && !value.includes(STOP)) {
            continue;
        }
        const fragment = document.createDocumentFragment();
        let buffer = '';
        const flush = () => {
            if (!buffer) {
                return;
            }
            if (active !== null && spans[active]) {
                const element = document.createElement('span');
                element.className = 'private-span';
                element.dataset.characters = spans[active].characters.join('|');
                element.style.backgroundColor = privateTint(spans[active].characters, colors);
                element.textContent = buffer;
                fragment.appendChild(element);
            } else {
                fragment.appendChild(document.createTextNode(buffer));
            }
            buffer = '';
        };
        for (let i = 0; i < value.length; i++) {
            const ch = value[i];
            if (ch === START) {
                flush();
                const close = value.indexOf(START_END, i);
                active = Number(value.slice(i + 1, close));
                i = close;
            } else if (ch === STOP) {
                flush();
                active = null;
            } else {
                buffer += ch;
            }
        }
        flush();
        node.parentNode.replaceChild(fragment, node);
    }

    return template.innerHTML;
}

// ---------------------------------------------------------------------------
// copy / paste: the marked up text goes along as its own clipboard format, so
// pasting into a text box with private parts brings them back. Other apps get
// the plain text as usual.
// ---------------------------------------------------------------------------

export const CLIPBOARD_TYPE = 'application/x-talemate-private-text';

// "⟦A⟧x⟦/⟧⟦A⟧y⟦/⟧" -> "⟦A⟧xy⟦/⟧" (a part rendered in pieces)
export function mergeAdjacentParts(markup) {
    let previous;
    let result = markup;
    do {
        previous = result;
        result = result.replace(/⟦(?!\/)([^⟧]*)⟧([^⟦]*)⟦\/⟧⟦\1⟧/g, '⟦$1⟧$2');
    } while (result !== previous);
    return result;
}

// The marked up text of a selected part of rendered text (private parts are
// .private-span elements), read with line breaks like the browser copies it
export function renderedRangeMarkup(range) {
    const fragment = range.cloneContents();
    for (const element of fragment.querySelectorAll('.private-span')) {
        element.replaceWith(
            document.createTextNode(`⟦${element.dataset.characters}⟧${element.textContent}⟦/⟧`),
        );
    }
    const holder = document.createElement('div');
    holder.style.position = 'fixed';
    holder.style.left = '-10000px';
    holder.style.top = '0';
    holder.appendChild(fragment);
    document.body.appendChild(holder);
    const text = holder.innerText;
    holder.remove();
    return mergeAdjacentParts(text);
}

function containsNode(range, node) {
    const nodeRange = document.createRange();
    nodeRange.selectNodeContents(node);
    return (
        range.compareBoundaryPoints(Range.START_TO_START, nodeRange) <= 0 &&
        range.compareBoundaryPoints(Range.END_TO_END, nodeRange) >= 0
    );
}

// The marked up text of a selection over messages in the chat (`elements`:
// their rendered text, `data-source` holding the message's own marked up
// text). Null when no private part is selected.
export function selectionMarkup(selection, elements) {
    if (!selection || selection.isCollapsed || !selection.rangeCount) {
        return null;
    }
    const range = selection.getRangeAt(0);
    const parts = [];
    let hasPrivate = false;
    for (const element of elements) {
        if (!range.intersectsNode(element)) {
            continue;
        }
        if (containsNode(range, element) && element.dataset.source != null) {
            // all of the message: its own text (with its formatting)
            parts.push(element.dataset.source);
            hasPrivate = hasPrivate || hasPrivateParts(element.dataset.source);
            continue;
        }
        const partial = document.createRange();
        partial.selectNodeContents(element);
        if (range.compareBoundaryPoints(Range.START_TO_START, partial) > 0) {
            partial.setStart(range.startContainer, range.startOffset);
        }
        if (range.compareBoundaryPoints(Range.END_TO_END, partial) < 0) {
            partial.setEnd(range.endContainer, range.endOffset);
        }
        const markup = renderedRangeMarkup(partial);
        parts.push(markup);
        hasPrivate = hasPrivate || hasPrivateParts(markup);
    }
    if (!hasPrivate) {
        return null;
    }
    return parts.join('\n\n');
}

// The marked up text of [start, end) of a text with private parts
export function sliceMarkup(text, spans, start, end) {
    const sliced = (spans || [])
        .filter(span => span.end > start && span.start < end)
        .map(span => ({
            start: Math.max(span.start, start) - start,
            end: Math.min(span.end, end) - start,
            characters: span.characters,
        }));
    return serializePrivateText(text.slice(start, end), sliced);
}
