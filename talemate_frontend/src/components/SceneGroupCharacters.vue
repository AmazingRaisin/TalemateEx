<template>
    <v-dialog :model-value="modelValue" @update:model-value="(value) => $emit('update:modelValue', value)" max-width="900">
        <v-card class="group-dialog" @dragover.prevent="onDialogDragOver" @drop.prevent="onDialogDrop">
            <v-card-title>
                <v-icon class="mr-2" size="small" color="primary">mdi-account-group</v-icon>
                Group Characters
            </v-card-title>
            <v-card-text class="pb-0">
                <v-alert v-if="locked" type="info" variant="tonal" density="compact" class="mb-2">{{ LOCKED_TEXT }}</v-alert>
                <v-row>
                    <v-col cols="12" sm="5">
                        <div class="text-caption text-muted mb-1">Characters</div>
                        <div class="pane">
                            <div v-for="room in characterRooms" :key="room.id" class="mb-2">
                                <div v-if="showRooms" class="text-caption text-muted mb-1">{{ room.name }}</div>
                                <div
                                    v-for="character in room.characters"
                                    :key="character.name"
                                    class="character-item d-flex align-center"
                                    :class="{ dragging: drag && drag.type === 'character' && drag.name === character.name, locked }"
                                    :draggable="!locked"
                                    @dragstart="dragCharacter($event, character.name)"
                                    @dragend="dragEnd"
                                >
                                    <v-icon size="small" class="mr-1" color="muted">mdi-drag</v-icon>
                                    <span :style="{ color: character.color }">{{ character.name }}</span>
                                    <v-spacer></v-spacer>
                                    <v-chip
                                        v-for="group in character.groups"
                                        :key="group.id"
                                        size="x-small"
                                        label
                                        class="ml-1"
                                        :color="group.color"
                                    >{{ group.id }}</v-chip>
                                </div>
                            </div>
                            <div v-if="!characterRooms.length" class="text-caption text-muted">No characters to group.</div>
                        </div>
                    </v-col>
                    <v-col cols="12" sm="7">
                        <div class="text-caption text-muted mb-1">Groups</div>
                        <div class="pane">
                            <div
                                v-for="group in groups"
                                :key="group.id"
                                class="group-card mb-2"
                                :class="{ 'drop-target': dropTarget === group.id, 'drop-refused': dropTarget === group.id && refused }"
                                :style="{ borderColor: group.color }"
                                @dragover.prevent.stop="onGroupDragOver($event, group)"
                                @dragleave="onGroupDragLeave(group)"
                                @drop.prevent.stop="onGroupDrop(group)"
                            >
                                <div class="d-flex align-center">
                                    <span class="group-swatch mr-2" :style="{ backgroundColor: group.color }"></span>
                                    <span class="text-body-2 font-weight-bold" :style="{ color: group.color }">{{ group.id }}</span>
                                    <span v-if="showRooms && group.room" class="text-caption text-muted ml-2">{{ roomName(group.room) }}</span>
                                    <v-spacer></v-spacer>
                                    <span class="text-caption text-muted">{{ groupStatus(group) }}</span>
                                </div>
                                <div class="mt-1">
                                    <v-chip
                                        v-for="name in group.members"
                                        :key="name"
                                        size="small"
                                        label
                                        class="mr-1 mb-1"
                                        :draggable="!locked"
                                        :color="colors[name]"
                                        :variant="group.present.includes(name) ? 'tonal' : 'outlined'"
                                        @dragstart="dragMember($event, group, name)"
                                        @dragend="dragEnd"
                                    >
                                        {{ name }}
                                        <v-icon v-if="group.present.includes(name) && !group.speakers.includes(name)" size="x-small" class="ml-1">mdi-volume-off</v-icon>
                                        <span v-if="!group.present.includes(name)" class="text-caption ml-1">({{ activeNames.includes(name) ? 'elsewhere' : 'inactive' }})</span>
                                    </v-chip>
                                    <span v-if="!group.members.length" class="text-caption text-muted">Drop characters here.</span>
                                </div>
                            </div>
                            <div v-if="!groups.length" class="text-caption text-muted">
                                No groups yet. Create them in the World Editor under Scene &gt; Groups.
                            </div>
                        </div>
                    </v-col>
                </v-row>
                <v-alert v-if="error" type="warning" variant="tonal" density="compact" class="mt-2">{{ error }}</v-alert>
            </v-card-text>
            <v-card-actions>
                <v-tooltip location="top" text="Shrink to a small floating window you can move around, to keep chatting while grouping characters.">
                    <template v-slot:activator="{ props }">
                        <v-btn v-bind="props" class="float-button" variant="text" color="primary" prepend-icon="mdi-dock-window" @click="float">Float</v-btn>
                    </template>
                </v-tooltip>
                <span class="text-caption text-muted ml-2">
                    Drag characters onto a group to add them, drag members out of a group to remove them.
                </span>
                <v-spacer></v-spacer>
                <v-tooltip location="top" text="Drop a character here to remove it from all its groups (a member: from that group).">
                    <template v-slot:activator="{ props }">
                        <div
                            v-bind="props"
                            class="trash mr-2"
                            :class="{ 'drop-target': dropTarget === 'trash' }"
                            @dragover.prevent.stop="onTrashDragOver"
                            @dragleave="dropTarget = null"
                            @drop.prevent.stop="onTrashDrop"
                        >
                            <v-icon :color="dropTarget === 'trash' ? 'delete' : 'muted'">mdi-delete</v-icon>
                        </div>
                    </template>
                </v-tooltip>
                <v-btn variant="text" color="cancel" prepend-icon="mdi-close" @click="close">Close</v-btn>
            </v-card-actions>
        </v-card>
    </v-dialog>

    <!-- the same as a small window, chatting goes on around it -->
    <Teleport :to="teleportTarget">
        <v-card
            v-if="floating"
            ref="floatCard"
            class="group-float"
            elevation="12"
            :style="{ left: position.x + 'px', top: position.y + 'px' }"
            @dragover.prevent="onDialogDragOver"
            @drop.prevent="onDialogDrop"
        >
            <div class="group-float-header d-flex align-center" @mousedown="startMove">
                <v-icon size="small" class="mr-1" color="primary">mdi-account-group</v-icon>
                <span class="text-body-2">Group Characters</span>
                <v-spacer></v-spacer>
                <v-tooltip location="top" text="Open the full menu (close it there to remove this)">
                    <template v-slot:activator="{ props }">
                        <v-btn v-bind="props" class="expand-button" icon size="x-small" variant="text" @mousedown.stop @click="expand">
                            <v-icon>mdi-arrow-expand</v-icon>
                        </v-btn>
                    </template>
                </v-tooltip>
            </div>
            <v-divider></v-divider>
            <div class="group-float-body">
                <v-alert v-if="locked" type="info" variant="tonal" density="compact" class="mb-2 text-caption">{{ LOCKED_TEXT }}</v-alert>
                <v-alert v-if="error" type="warning" variant="tonal" density="compact" class="mb-2 text-caption">{{ error }}</v-alert>

                <div class="text-caption text-muted mb-1">Groups</div>
                <div
                    v-for="group in groups"
                    :key="'float-' + group.id"
                    class="group-card group-card-small mb-1"
                    :class="{ 'drop-target': dropTarget === group.id, 'drop-refused': dropTarget === group.id && refused }"
                    :style="{ borderColor: group.color }"
                    @dragover.prevent.stop="onGroupDragOver($event, group)"
                    @dragleave="onGroupDragLeave(group)"
                    @drop.prevent.stop="onGroupDrop(group)"
                >
                    <div class="d-flex align-center">
                        <span class="text-caption font-weight-bold" :style="{ color: group.color }">{{ group.id }}</span>
                        <v-spacer></v-spacer>
                        <span class="text-caption text-muted">{{ groupStatus(group) }}</span>
                    </div>
                    <div>
                        <v-chip
                            v-for="name in group.members"
                            :key="name"
                            size="x-small"
                            label
                            class="mr-1 mb-1"
                            :draggable="!locked"
                            :color="colors[name]"
                            :variant="group.present.includes(name) ? 'tonal' : 'outlined'"
                            @dragstart="dragMember($event, group, name)"
                            @dragend="dragEnd"
                        >
                            {{ name }}
                            <v-icon v-if="group.present.includes(name) && !group.speakers.includes(name)" size="x-small" class="ml-1">mdi-volume-off</v-icon>
                        </v-chip>
                        <span v-if="!group.members.length" class="text-caption text-muted">Drop characters here.</span>
                    </div>
                </div>
                <div v-if="!groups.length" class="text-caption text-muted">No groups yet.</div>

                <div class="text-caption text-muted mt-2 mb-1">Characters</div>
                <div v-for="room in characterRooms" :key="'float-' + room.id" class="mb-1">
                    <div v-if="showRooms" class="text-caption text-muted">{{ room.name }}</div>
                    <v-chip
                        v-for="character in room.characters"
                        :key="character.name"
                        size="x-small"
                        label
                        variant="outlined"
                        class="mr-1 mb-1 character-chip"
                        :class="{ locked }"
                        :color="character.color"
                        :draggable="!locked"
                        @dragstart="dragCharacter($event, character.name)"
                        @dragend="dragEnd"
                    >{{ character.name }}</v-chip>
                </div>
            </div>
            <v-divider></v-divider>
            <div class="d-flex align-center group-float-footer">
                <span class="text-caption text-muted">Drag onto a group, out to remove</span>
                <v-spacer></v-spacer>
                <div
                    class="trash trash-small"
                    :class="{ 'drop-target': dropTarget === 'trash' }"
                    title="Drop a character here to remove it from all its groups (a member: from that group)."
                    @dragover.prevent.stop="onTrashDragOver"
                    @dragleave="dropTarget = null"
                    @drop.prevent.stop="onTrashDrop"
                >
                    <v-icon size="small" :color="dropTarget === 'trash' ? 'delete' : 'muted'">mdi-delete</v-icon>
                </div>
            </div>
        </v-card>
    </Teleport>
</template>

<script>
import { activeRooms, characterRoom, roomName, roomsShown } from '@/utils/rooms';
import { groupsOf, sceneGroups } from '@/utils/groups';

const POSITION_KEY = 'talemate.groupCharacters.floatPosition';
const FLOAT_WIDTH = 300;

// open across the chat tools being shown again (e.g. after the World Editor)
let floatingOpen = false;

// Control Scene > Group Characters: drag characters into groups
// (talemate.groups), out of them or onto the trash can. As a dialog, or a
// small floating window to keep chatting while grouping.
export default {
    name: 'SceneGroupCharacters',
    props: {
        modelValue: Boolean,
        scene: Object,
        // agents are working: no grouping until they are done
        locked: Boolean,
    },
    emits: ['update:modelValue'],
    inject: ['getWebsocket', 'registerMessageHandler', 'unregisterMessageHandler'],
    data() {
        return {
            LOCKED_TEXT: 'Agents are working, characters can be grouped again once they are done.',
            // what is being dragged: {type: 'character' | 'member', name, groupId}
            drag: null,
            dropTarget: null,
            refused: false,
            error: null,
            waiting: 0,
            floating: floatingOpen,
            position: this.savedPosition(),
            // moving the floating window: where the mouse grabbed it
            moving: null,
            // inside the app once it's there, so it gets the theme
            teleportTarget: 'body',
        };
    },
    computed: {
        sceneData() {
            return (this.scene && this.scene.data) || {};
        },
        groups() {
            return sceneGroups(this.sceneData);
        },
        colors() {
            return this.sceneData.character_colors || {};
        },
        showRooms() {
            return roomsShown(this.sceneData);
        },
        activeNames() {
            return (this.sceneData.characters || []).map(c => c.name);
        },
        // the active characters (not the player character) by room
        characterRooms() {
            const characters = (this.sceneData.characters || []).filter(c => !c.is_player);
            return activeRooms(this.sceneData)
                .map(room => ({
                    id: room.id,
                    name: room.name,
                    characters: characters
                        .filter(c => characterRoom(this.sceneData, c.name) === room.id)
                        .map(c => ({
                            name: c.name,
                            color: c.color,
                            groups: groupsOf(this.sceneData, c.name),
                        })),
                }))
                .filter(room => room.characters.length);
        },
    },
    watch: {
        modelValue(open) {
            if (open) {
                this.error = null;
                // the full menu again (e.g. from Control Scene)
                this.floating = false;
            }
        },
        locked(locked) {
            if (locked) {
                this.dragEnd();
            }
        },
        floating(value) {
            floatingOpen = value;
        },
    },
    methods: {
        roomName(roomId) {
            return roomName(this.sceneData, roomId);
        },
        groupStatus(group) {
            if (group.takes_turns) {
                return `${group.speakers.length} speaking`;
            }
            if (group.speakers.length === 1) {
                return `only ${group.speakers[0]} can speak`;
            }
            return 'takes no turns';
        },
        close() {
            this.$emit('update:modelValue', false);
        },
        // the dialog becomes the floating window
        float() {
            this.floating = true;
            this.position = this.clamped(this.position);
            this.$emit('update:modelValue', false);
        },
        // and back
        expand() {
            this.floating = false;
            this.$emit('update:modelValue', true);
        },
        savedPosition() {
            try {
                const saved = JSON.parse(localStorage.getItem(POSITION_KEY));
                if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) {
                    return saved;
                }
            } catch (e) {
                // not saved yet
            }
            // bottom left, above the chat input
            return { x: 16, y: Math.max(window.innerHeight - 520, 64) };
        },
        clamped({ x, y }) {
            const card = this.$refs.floatCard && this.$refs.floatCard.$el;
            const width = card ? card.offsetWidth : FLOAT_WIDTH;
            const height = card ? card.offsetHeight : 120;
            return {
                x: Math.min(Math.max(x, 0), Math.max(window.innerWidth - width, 0)),
                y: Math.min(Math.max(y, 0), Math.max(window.innerHeight - Math.min(height, 48), 0)),
            };
        },
        startMove(event) {
            if (event.button !== 0) {
                return;
            }
            event.preventDefault();
            this.moving = { dx: event.clientX - this.position.x, dy: event.clientY - this.position.y };
            window.addEventListener('mousemove', this.onMove);
            window.addEventListener('mouseup', this.endMove);
        },
        onMove(event) {
            if (!this.moving) {
                return;
            }
            this.position = this.clamped({
                x: event.clientX - this.moving.dx,
                y: event.clientY - this.moving.dy,
            });
        },
        endMove() {
            this.moving = null;
            window.removeEventListener('mousemove', this.onMove);
            window.removeEventListener('mouseup', this.endMove);
            try {
                localStorage.setItem(POSITION_KEY, JSON.stringify(this.position));
            } catch (e) {
                // not kept then
            }
        },
        onResize() {
            if (this.floating) {
                this.position = this.clamped(this.position);
            }
        },
        send(action, data) {
            this.waiting += 1;
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action,
                ...data,
            }));
        },
        // why a character can't join a group (null: it can)
        refusal(group, name) {
            if (group.members.includes(name)) {
                return `${name} is in ${group.id} already.`;
            }
            const room = characterRoom(this.sceneData, name);
            if (group.room && room !== group.room) {
                return `${group.id} is in ${this.roomName(group.room)}, ${name} is in ${this.roomName(room)}.`;
            }
            return null;
        },
        dragCharacter(event, name) {
            if (this.locked) {
                event.preventDefault();
                return;
            }
            this.error = null;
            this.drag = { type: 'character', name };
            event.dataTransfer.effectAllowed = 'move';
            event.dataTransfer.setData('text/plain', name);
        },
        dragMember(event, group, name) {
            if (this.locked) {
                event.preventDefault();
                return;
            }
            this.error = null;
            this.drag = { type: 'member', name, groupId: group.id };
            event.dataTransfer.effectAllowed = 'move';
            event.dataTransfer.setData('text/plain', name);
        },
        dragEnd() {
            this.drag = null;
            this.dropTarget = null;
            this.refused = false;
        },
        onGroupDragOver(event, group) {
            if (!this.drag || this.locked) {
                return;
            }
            this.dropTarget = group.id;
            const own = this.drag.type === 'member' && this.drag.groupId === group.id;
            const refusal = own ? null : this.refusal(group, this.drag.name);
            this.refused = !!refusal;
            // a refused drop never happens, say why while hovering
            this.error = refusal;
            event.dataTransfer.dropEffect = this.refused ? 'none' : 'move';
        },
        onGroupDragLeave(group) {
            if (this.dropTarget === group.id) {
                this.dropTarget = null;
                this.refused = false;
            }
        },
        onGroupDrop(group) {
            const drag = this.drag;
            this.dragEnd();
            if (!drag || this.locked) {
                return;
            }
            if (drag.type === 'member' && drag.groupId === group.id) {
                // dropped back where it was
                return;
            }
            const refusal = this.refusal(group, drag.name);
            if (refusal) {
                this.error = refusal;
                return;
            }
            this.send('add_group_member', { group_id: group.id, name: drag.name });
            if (drag.type === 'member') {
                // moved from one group to another
                this.send('remove_group_member', { group_id: drag.groupId, name: drag.name });
            }
        },
        onTrashDragOver(event) {
            if (!this.drag || this.locked) {
                return;
            }
            this.dropTarget = 'trash';
            event.dataTransfer.dropEffect = 'move';
        },
        onTrashDrop() {
            const drag = this.drag;
            this.dragEnd();
            if (!drag || this.locked) {
                return;
            }
            if (drag.type === 'member') {
                this.send('remove_group_member', { group_id: drag.groupId, name: drag.name });
            } else {
                this.send('ungroup_character', { name: drag.name });
            }
        },
        onDialogDragOver(event) {
            if (this.drag && this.drag.type === 'member' && !this.locked) {
                event.dataTransfer.dropEffect = 'move';
            }
        },
        // a member dragged out of its group
        onDialogDrop() {
            const drag = this.drag;
            this.dragEnd();
            if (drag && drag.type === 'member' && !this.locked) {
                this.send('remove_group_member', { group_id: drag.groupId, name: drag.name });
            }
        },
        handleMessage(message) {
            if (message.type !== 'world_state_manager' || message.action !== 'operation_done' || !this.waiting) {
                return;
            }
            this.waiting -= 1;
            if (message.error) {
                this.error = message.error.message;
            }
        },
    },
    mounted() {
        if (document.querySelector('.v-application')) {
            this.teleportTarget = '.v-application';
        }
        this.registerMessageHandler(this.handleMessage);
        window.addEventListener('resize', this.onResize);
    },
    unmounted() {
        this.unregisterMessageHandler(this.handleMessage);
        window.removeEventListener('resize', this.onResize);
        this.endMove();
    },
};
</script>

<style scoped>
.pane {
    max-height: 420px;
    min-height: 120px;
    overflow-y: auto;
}
.character-item {
    padding: 4px 8px;
    border-radius: 4px;
    cursor: grab;
}
.character-item:hover {
    background: rgba(255, 255, 255, 0.05);
}
.character-item.dragging {
    opacity: 0.5;
}
.character-item.locked,
.character-chip.locked {
    cursor: default;
    opacity: 0.6;
}
.character-chip {
    cursor: grab;
}
.group-card {
    border: 1px solid;
    border-left-width: 4px;
    border-radius: 4px;
    padding: 6px 8px;
    transition: background-color 0.1s;
}
.group-card-small {
    padding: 3px 6px;
}
.group-card.drop-target {
    background: rgba(255, 255, 255, 0.08);
}
.group-card.drop-refused {
    background: rgba(244, 67, 54, 0.12);
}
.group-swatch {
    display: inline-block;
    width: 10px;
    height: 10px;
    border-radius: 50%;
}
.trash {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 40px;
    height: 36px;
    border: 1px dashed rgba(255, 255, 255, 0.25);
    border-radius: 4px;
}
.trash-small {
    width: 32px;
    height: 26px;
}
.trash.drop-target {
    border-color: rgb(var(--v-theme-delete, 244, 67, 54));
    background: rgba(244, 67, 54, 0.12);
}
.group-float {
    position: fixed;
    width: 300px;
    z-index: 1500;
    display: flex;
    flex-direction: column;
}
.group-float-header {
    padding: 4px 4px 4px 10px;
    cursor: move;
    user-select: none;
}
.group-float-body {
    max-height: 45vh;
    overflow-y: auto;
    overscroll-behavior: contain;
    padding: 6px 10px;
}
.group-float-footer {
    padding: 4px 10px;
}
</style>
