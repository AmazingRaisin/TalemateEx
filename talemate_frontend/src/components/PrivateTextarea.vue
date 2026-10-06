<template>
    <div class="private-textarea">
        <v-textarea
            ref="field"
            v-bind="$attrs"
            :model-value="modelValue"
            @update:model-value="(value) => $emit('update:modelValue', value)"
        >
            <template v-for="(_, name) in $slots" #[name]="slotProps">
                <slot :name="name" v-bind="slotProps || {}"></slot>
            </template>
        </v-textarea>

        <v-menu
            v-model="menu.open"
            :target="menu.target"
            location="bottom start"
            :close-on-content-click="false"
            @update:model-value="onMenuToggle"
        >
            <v-card density="compact" min-width="220" class="private-menu">
                <div class="text-caption text-muted px-4 pt-2">
                    {{ menu.editing ? 'This part is visible to' : 'Make the selection visible only to' }}
                </div>
                <v-list density="compact" class="private-menu-list">
                    <!-- a shortcut to their members, the part is for the characters (talemate.groups) -->
                    <template v-if="groups.length">
                        <v-list-item
                            v-for="group in groups"
                            :key="'group-' + group.id"
                            density="compact"
                            @click="toggleGroup(group)"
                        >
                            <template v-slot:prepend>
                                <v-checkbox-btn
                                    :model-value="groupState(group) === 'all'"
                                    :indeterminate="groupState(group) === 'some'"
                                    density="compact"
                                    @click.stop="toggleGroup(group)"
                                ></v-checkbox-btn>
                            </template>
                            <v-list-item-title :style="{ color: group.color || undefined }">
                                <v-icon size="x-small" class="mr-1">mdi-account-group</v-icon>{{ group.id }}
                            </v-list-item-title>
                            <v-list-item-subtitle class="text-caption">{{ group.members.join(', ') }}</v-list-item-subtitle>
                        </v-list-item>
                        <v-divider></v-divider>
                    </template>
                    <v-list-item
                        v-for="character in menuCharacters"
                        :key="character.name"
                        density="compact"
                        @click="toggle(character.name)"
                    >
                        <template v-slot:prepend>
                            <v-checkbox-btn
                                :model-value="menu.selected.includes(character.name)"
                                density="compact"
                                @click.stop="toggle(character.name)"
                            ></v-checkbox-btn>
                        </template>
                        <v-list-item-title :style="{ color: character.color || undefined }">{{ character.name }}</v-list-item-title>
                    </v-list-item>
                    <v-list-item v-if="!menuCharacters.length" disabled density="compact">
                        <v-list-item-title class="text-caption">No one else is here.</v-list-item-title>
                    </v-list-item>
                </v-list>
                <div class="text-caption text-muted px-4 pb-2">
                    {{ menu.editing ? 'Deselect everyone to make it visible to all again.' : 'Click outside to apply.' }}
                </div>
            </v-card>
        </v-menu>

        <PrivateTooltip v-if="tooltip.visible" :x="tooltip.x" :y="tooltip.y" :text="tooltip.names" />
    </div>
</template>

<script>
import PrivateTooltip from './PrivateTooltip.vue';
import {
    CLIPBOARD_TYPE,
    adjustSpans,
    backdropHtml,
    joinNames,
    normalizeSpans,
    parsePrivateText,
    setSpanCharacters,
    sliceMarkup,
    spanAt,
} from '@/utils/privateText';

const COPIED_STYLES = [
    'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
    'borderTopWidth', 'borderRightWidth', 'borderBottomWidth', 'borderLeftWidth',
    'fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'fontVariant',
    'letterSpacing', 'lineHeight', 'textTransform', 'textIndent', 'tabSize',
    'wordSpacing', 'textAlign',
];

// A v-textarea where parts of the text can be made visible to some characters
// only (talemate.private_text): select text, right click, pick characters.
// The parts are kept as spans ({start, end, characters}, v-model:spans) and
// shown tinted behind the text.
export default {
    name: 'PrivateTextarea',
    components: { PrivateTooltip },
    inheritAttrs: false,
    props: {
        modelValue: { type: String, default: '' },
        spans: { type: Array, default: () => [] },
        // who parts can be made visible to: [{name, color}]
        characters: { type: Array, default: () => [] },
        // name -> color, for the tints
        colors: { type: Object, default: () => ({}) },
        // groups picking all their members at once: [{id, color, members}]
        groups: { type: Array, default: () => [] },
        privateEnabled: { type: Boolean, default: true },
    },
    emits: ['update:modelValue', 'update:spans'],
    data() {
        return {
            menu: {
                open: false,
                target: [0, 0],
                editing: false,
                start: 0,
                end: 0,
                selected: [],
                initial: [],
            },
            tooltip: { visible: false, x: 0, y: 0, names: '' },
            skipAdjust: false,
        };
    },
    computed: {
        menuCharacters() {
            const list = [...this.characters];
            // whoever already sees the part, even if no longer here
            for (const name of this.menu.selected.concat(this.menu.initial)) {
                if (!list.find(c => c.name === name)) {
                    list.push({ name, color: this.colors[name] });
                }
            }
            return list;
        },
        menuOpen() {
            return this.menu.open;
        },
    },
    watch: {
        modelValue(value, previous) {
            if (this.skipAdjust) {
                this.skipAdjust = false;
            } else if (this.spans.length) {
                const adjusted = adjustSpans(previous || '', value || '', this.spans);
                this.$emit('update:spans', normalizeSpans(adjusted, (value || '').length));
            }
            this.$nextTick(this.renderBackdrop);
        },
        spans: {
            deep: true,
            handler() {
                this.$nextTick(this.renderBackdrop);
            },
        },
        colors: {
            deep: true,
            handler() {
                this.$nextTick(this.renderBackdrop);
            },
        },
    },
    methods: {
        textarea() {
            return this.$refs.field?.$el?.querySelector('textarea') || null;
        },
        focus() {
            this.$refs.field?.focus();
        },
        // Replaces the text and its parts (e.g. browsing the input history)
        setContent(text, spans) {
            if ((text || '') !== (this.modelValue || '')) {
                this.skipAdjust = true;
            }
            this.$emit('update:modelValue', text || '');
            this.$emit('update:spans', normalizeSpans(spans || [], (text || '').length));
        },

        // backdrop: the text (invisible) with the parts tinted, behind the textarea
        setupBackdrop() {
            const textarea = this.textarea();
            if (!textarea || this._backdrop) {
                return;
            }
            const parent = textarea.parentElement;
            if (getComputedStyle(parent).position === 'static') {
                parent.style.position = 'relative';
            }
            const backdrop = document.createElement('div');
            backdrop.className = 'private-textarea-backdrop';
            backdrop.setAttribute('aria-hidden', 'true');
            parent.insertBefore(backdrop, textarea);
            textarea.classList.add('private-textarea-input');

            this._backdrop = backdrop;
            this._textarea = textarea;
            this._onScroll = () => {
                backdrop.scrollTop = textarea.scrollTop;
                backdrop.scrollLeft = textarea.scrollLeft;
            };
            this._onContextMenu = (event) => this.onContextMenu(event);
            this._onMouseMove = (event) => this.onMouseMove(event);
            this._onMouseLeave = () => { this.tooltip.visible = false; };
            this._onCopy = (event) => this.onCopy(event, false);
            this._onCut = (event) => this.onCopy(event, true);
            this._onPaste = (event) => this.onPaste(event);
            textarea.addEventListener('copy', this._onCopy);
            textarea.addEventListener('cut', this._onCut);
            textarea.addEventListener('paste', this._onPaste);
            textarea.addEventListener('scroll', this._onScroll);
            textarea.addEventListener('contextmenu', this._onContextMenu);
            textarea.addEventListener('mousemove', this._onMouseMove);
            textarea.addEventListener('mouseleave', this._onMouseLeave);
            this._resizeObserver = new ResizeObserver(() => this.renderBackdrop());
            this._resizeObserver.observe(textarea);
            this.renderBackdrop();
        },
        teardownBackdrop() {
            const textarea = this._textarea;
            if (textarea) {
                textarea.removeEventListener('scroll', this._onScroll);
                textarea.removeEventListener('contextmenu', this._onContextMenu);
                textarea.removeEventListener('mousemove', this._onMouseMove);
                textarea.removeEventListener('mouseleave', this._onMouseLeave);
                textarea.removeEventListener('copy', this._onCopy);
                textarea.removeEventListener('cut', this._onCut);
                textarea.removeEventListener('paste', this._onPaste);
            }
            this._resizeObserver?.disconnect();
            this._backdrop?.remove();
            this._backdrop = null;
            this._textarea = null;
        },
        renderBackdrop() {
            const textarea = this._textarea;
            const backdrop = this._backdrop;
            if (!textarea || !backdrop) {
                return;
            }
            const style = getComputedStyle(textarea);
            for (const key of COPIED_STYLES) {
                backdrop.style[key] = style[key];
            }
            // same size as the textarea, its scrollbar (if any) narrows the text
            backdrop.style.boxSizing = 'border-box';
            const borders = parseFloat(style.borderLeftWidth) + parseFloat(style.borderRightWidth);
            const scrollbar = Math.max(textarea.offsetWidth - textarea.clientWidth - borders, 0);
            backdrop.style.paddingRight = `${parseFloat(style.paddingRight) + scrollbar}px`;
            const parentRect = backdrop.parentElement.getBoundingClientRect();
            const rect = textarea.getBoundingClientRect();
            backdrop.style.left = `${rect.left - parentRect.left}px`;
            backdrop.style.top = `${rect.top - parentRect.top}px`;
            backdrop.style.width = `${textarea.offsetWidth}px`;
            backdrop.style.height = `${textarea.offsetHeight}px`;
            const text = this.modelValue || '';
            backdrop.innerHTML = this.spans.length ? backdropHtml(text, this.spans, this.colors) : '';
            backdrop.scrollTop = textarea.scrollTop;
        },

        onContextMenu(event) {
            if (!this.privateEnabled) {
                return;
            }
            const textarea = this._textarea;
            const start = textarea.selectionStart;
            const end = textarea.selectionEnd;
            const index = spanAt(this.spans, start, end);
            if (index < 0 && end <= start) {
                // nothing selected: the browser's own menu
                return;
            }
            event.preventDefault();
            const span = index >= 0 ? this.spans[index] : null;
            this.menu = {
                open: true,
                target: [event.clientX, event.clientY],
                editing: !!span,
                start: span ? span.start : start,
                end: span ? span.end : end,
                selected: span ? [...span.characters] : [],
                initial: span ? [...span.characters] : [],
                selectionStart: start,
                selectionEnd: end,
            };
            this.tooltip.visible = false;
        },
        groupState(group) {
            const selected = group.members.filter(name => this.menu.selected.includes(name));
            if (!selected.length) {
                return 'none';
            }
            return selected.length === group.members.length ? 'all' : 'some';
        },
        toggleGroup(group) {
            if (this.groupState(group) === 'all') {
                this.menu.selected = this.menu.selected.filter(name => !group.members.includes(name));
            } else {
                for (const name of group.members) {
                    if (!this.menu.selected.includes(name)) {
                        this.menu.selected.push(name);
                    }
                }
            }
        },
        toggle(name) {
            const index = this.menu.selected.indexOf(name);
            if (index >= 0) {
                this.menu.selected.splice(index, 1);
            } else {
                this.menu.selected.push(name);
            }
        },
        onMenuToggle(open) {
            if (open) {
                return;
            }
            const { editing, start, end, selected, initial } = this.menu;
            const changed =
                selected.length !== initial.length ||
                selected.some(name => !initial.includes(name));
            // a new part needs someone to be for, a changed one may have no one left
            if ((editing && changed) || (!editing && selected.length)) {
                const spans = setSpanCharacters(this.spans, start, end, selected);
                this.$emit('update:spans', normalizeSpans(spans, (this.modelValue || '').length));
            }
            // back to typing where the selection was
            this.$nextTick(() => {
                const textarea = this._textarea;
                if (textarea) {
                    textarea.focus();
                    textarea.setSelectionRange(this.menu.selectionStart ?? end, this.menu.selectionEnd ?? end);
                }
            });
        },

        // copying (or cutting) text with private parts: the marked up text
        // goes along, so pasting brings the parts back
        onCopy(event, cut) {
            const textarea = this._textarea;
            const start = textarea.selectionStart;
            const end = textarea.selectionEnd;
            if (end <= start || !event.clipboardData) {
                return;
            }
            if (!this.spans.some(span => span.end > start && span.start < end)) {
                return;
            }
            const text = this.modelValue || '';
            event.clipboardData.setData('text/plain', text.slice(start, end));
            event.clipboardData.setData(CLIPBOARD_TYPE, sliceMarkup(text, this.spans, start, end));
            event.preventDefault();
            if (cut && !textarea.disabled && !textarea.readOnly) {
                // deleting through the browser keeps undo working
                if (!document.execCommand('delete')) {
                    this.$emit('update:modelValue', text.slice(0, start) + text.slice(end));
                    this.$nextTick(() => textarea.setSelectionRange(start, start));
                }
            }
        },
        onPaste(event) {
            const markup = event.clipboardData && event.clipboardData.getData(CLIPBOARD_TYPE);
            if (!markup || !this.privateEnabled) {
                return;
            }
            event.preventDefault();
            const textarea = this._textarea;
            const { text, spans } = parsePrivateText(markup);
            const before = this.modelValue || '';
            const start = textarea.selectionStart;
            const end = textarea.selectionEnd;
            const after = before.slice(0, start) + text + before.slice(end);

            // the existing parts move along, the pasted ones are added
            let result = adjustSpans(before, after, this.spans);
            for (const span of spans) {
                result = setSpanCharacters(result, start + span.start, start + span.end, span.characters);
            }
            if (after !== before) {
                this.skipAdjust = true;
            }
            // inserting through the browser keeps undo working
            if (!document.execCommand('insertText', false, text)) {
                this.$emit('update:modelValue', after);
            }
            this.$emit('update:spans', normalizeSpans(result, after.length));
        },

        onMouseMove(event) {
            const backdrop = this._backdrop;
            if (!backdrop || !this.spans.length) {
                this.tooltip.visible = false;
                return;
            }
            for (const mark of backdrop.querySelectorAll('mark.private-span')) {
                for (const rect of mark.getClientRects()) {
                    if (
                        event.clientX >= rect.left && event.clientX <= rect.right &&
                        event.clientY >= rect.top && event.clientY <= rect.bottom
                    ) {
                        const names = (mark.dataset.characters || '').split('|').filter(Boolean);
                        this.tooltip = {
                            visible: true,
                            x: event.clientX,
                            y: event.clientY,
                            names: `${joinNames(names)} ${names.length === 1 ? 'sees' : 'see'} this`,
                        };
                        return;
                    }
                }
            }
            this.tooltip.visible = false;
        },
    },
    mounted() {
        this.$nextTick(this.setupBackdrop);
    },
    beforeUnmount() {
        this.teardownBackdrop();
    },
};
</script>

<style>
.private-textarea-backdrop {
    position: absolute;
    pointer-events: none;
    color: transparent;
    white-space: pre-wrap;
    overflow-wrap: break-word;
    word-wrap: break-word;
    overflow: hidden;
    border-style: solid;
    border-color: transparent;
    z-index: 0;
}

.private-textarea-backdrop mark.private-span {
    color: transparent;
    border-radius: 3px;
    padding: 0;
    margin: 0;
}

textarea.private-textarea-input {
    position: relative;
    z-index: 1;
    background: transparent;
}

.private-menu-list {
    max-height: 280px;
    overflow-y: auto;
}

/* private parts in messages in the chat */
.character-text .private-span {
    border-radius: 3px;
    padding: 0 1px;
}
</style>
