<template>
    <div :style="{ maxWidth: MAX_CONTENT_WIDTH }">
        <v-alert density="compact" variant="text" color="muted" class="mt-2 text-caption" icon="mdi-door">
            Rooms are places characters can be in. Characters only perceive what happens in the room they are in.
            Move characters with <strong>Control Scene</strong> in the chat tools.
        </v-alert>
        <v-row class="mt-2">
            <v-col cols="12" md="4">
                <v-list density="compact" nav>
                    <v-list-item
                        v-for="room in rooms"
                        :key="room.id"
                        :active="selected === room.id"
                        color="primary"
                        @click="select(room.id)"
                    >
                        <template v-slot:prepend>
                            <div class="room-swatch mr-3" :style="{ backgroundColor: room.color || 'transparent' }"></div>
                        </template>
                        <v-list-item-title>{{ room.name }}</v-list-item-title>
                        <v-list-item-subtitle>
                            <span v-if="room.id === MAIN_ROOM_ID" class="mr-1">Main room ·</span>
                            {{ occupantsLabel(room.id) }}
                        </v-list-item-subtitle>
                    </v-list-item>
                    <v-list-item :active="selected === NEW_ROOM" color="primary" prepend-icon="mdi-plus" @click="select(NEW_ROOM)">
                        <v-list-item-title>Add room</v-list-item-title>
                    </v-list-item>
                </v-list>

                <v-expansion-panels v-if="deletedRooms.length" class="mt-2" variant="accordion">
                    <v-expansion-panel>
                        <v-expansion-panel-title class="text-caption">
                            Deleted rooms ({{ deletedRooms.length }})
                        </v-expansion-panel-title>
                        <v-expansion-panel-text>
                            <div class="text-caption text-muted mb-2">
                                Their history is kept. Restoring a room (or adding one with the same name) brings it back.
                            </div>
                            <div v-for="room in deletedRooms" :key="room.id" class="d-flex align-center mb-1">
                                <span class="text-body-2">{{ room.name }}</span>
                                <v-spacer></v-spacer>
                                <v-btn size="small" variant="text" color="primary" prepend-icon="mdi-restore" @click="restore(room)">Restore</v-btn>
                            </div>
                        </v-expansion-panel-text>
                    </v-expansion-panel>
                </v-expansion-panels>
            </v-col>

            <v-col cols="12" md="8">
                <v-card v-if="form" variant="tonal">
                    <v-card-title class="text-subtitle-1">
                        {{ selected === NEW_ROOM ? 'New room' : form.name }}
                    </v-card-title>
                    <v-card-text>
                        <v-row>
                            <v-col cols="12" md="8">
                                <v-text-field
                                    v-model="form.name"
                                    label="Name"
                                    messages="Used as typed in messages, e.g. 'the Kitchen' or 'Outside'."
                                ></v-text-field>
                            </v-col>
                            <v-col cols="12" md="4">
                                <v-text-field
                                    v-model="form.label"
                                    label="Label"
                                    :placeholder="form.name"
                                    messages="Short label on chat messages from this room."
                                ></v-text-field>
                            </v-col>
                        </v-row>
                        <v-textarea
                            v-model="form.description"
                            label="Description"
                            rows="2"
                            auto-grow
                            class="mt-2"
                            messages="Optional. Shown to characters in this room."
                        ></v-textarea>
                        <v-row class="mt-2">
                            <v-col cols="12" md="6">
                                <v-text-field
                                    v-model="form.empty_enter_message"
                                    label="Empty when entering"
                                    :placeholder="DEFAULT_EMPTY_ENTER"
                                    messages="Shown when someone enters and no one else is here."
                                ></v-text-field>
                            </v-col>
                            <v-col cols="12" md="6">
                                <v-text-field
                                    v-model="form.empty_leave_message"
                                    label="Empty when leaving"
                                    :placeholder="DEFAULT_EMPTY_LEAVE"
                                    messages="Shown when someone leaves and no one stays behind."
                                ></v-text-field>
                            </v-col>
                        </v-row>
                        <div class="mt-4">
                            <div class="d-flex align-center mb-2">
                                <span class="text-body-2 mr-3">Chat tint</span>
                                <div class="tint-preview px-3 py-1" :style="{ backgroundColor: form.color || 'transparent' }">
                                    <v-chip v-if="previewBadge" size="x-small" label class="mr-2">{{ previewBadge }}</v-chip>
                                    <span class="text-caption">Message from {{ form.name || 'this room' }}</span>
                                </div>
                                <v-spacer></v-spacer>
                                <v-btn size="small" variant="text" color="muted" prepend-icon="mdi-water-off" @click="form.color = ''">No tint</v-btn>
                            </div>
                            <v-color-picker
                                :model-value="form.color || null"
                                @update:model-value="(value) => form.color = value || ''"
                                mode="hexa"
                                :modes="['hexa', 'rgba']"
                                hide-canvas
                                show-swatches
                                :swatches="swatches"
                                elevation="0"
                                width="100%"
                            ></v-color-picker>
                        </div>
                    </v-card-text>
                    <v-card-actions>
                        <ConfirmActionInline
                            v-if="selected !== NEW_ROOM && selected !== MAIN_ROOM_ID"
                            action-label="Delete room"
                            confirm-label="Confirm delete"
                            icon="mdi-delete"
                            @confirm="remove"
                        />
                        <span v-if="selected === MAIN_ROOM_ID" class="text-caption text-muted ml-2">
                            The main room can be renamed but not deleted.
                        </span>
                        <v-spacer></v-spacer>
                        <v-btn
                            color="primary"
                            variant="text"
                            :prepend-icon="selected === NEW_ROOM ? 'mdi-plus' : 'mdi-content-save'"
                            :disabled="!form.name || !form.name.trim() || busy"
                            @click="save"
                        >{{ selected === NEW_ROOM ? 'Add room' : 'Save' }}</v-btn>
                    </v-card-actions>
                </v-card>
                <v-alert v-if="error" type="error" variant="tonal" density="compact" class="mt-2">{{ error }}</v-alert>
            </v-col>
        </v-row>
    </div>
</template>

<script>
import ConfirmActionInline from './ConfirmActionInline.vue';
import { MAX_CONTENT_WIDTH } from '@/constants';
import { MAIN_ROOM_ID, characterRoom } from '@/utils/rooms';

const NEW_ROOM = '__new__';
const FIELDS = ['name', 'label', 'description', 'empty_enter_message', 'empty_leave_message', 'color'];

export default {
    name: 'WorldStateManagerSceneRooms',
    components: {
        ConfirmActionInline,
    },
    props: {
        scene: Object,
    },
    inject: [
        'getWebsocket',
        'registerMessageHandler',
        'unregisterMessageHandler',
    ],
    data() {
        return {
            MAX_CONTENT_WIDTH,
            MAIN_ROOM_ID,
            NEW_ROOM,
            DEFAULT_EMPTY_ENTER: 'No one else is here.',
            DEFAULT_EMPTY_LEAVE: 'No one stays behind.',
            selected: MAIN_ROOM_ID,
            form: null,
            busy: false,
            error: null,
            pendingSelectName: null,
            // the room as it was when the form was filled
            synced: null,
            swatches: [
                ['#00000000', '#7E57C233', '#42A5F533'],
                ['#26A69A33', '#66BB6A33', '#D4E15733'],
                ['#FFCA2833', '#FF704333', '#EC407A33'],
                ['#8D6E6333', '#78909C33', '#5C6BC033'],
            ],
        };
    },
    computed: {
        allRooms() {
            return (this.scene && this.scene.data && this.scene.data.rooms) || [];
        },
        rooms() {
            return this.allRooms.filter(room => !room.deleted);
        },
        deletedRooms() {
            return this.allRooms.filter(room => room.deleted);
        },
        previewBadge() {
            if (this.selected === MAIN_ROOM_ID) {
                return null;
            }
            return (this.form.label || '').trim() || this.form.name;
        },
    },
    watch: {
        allRooms: {
            deep: true,
            handler() {
                if (this.pendingSelectName) {
                    const match = this.rooms.find(room => room.name === this.pendingSelectName);
                    if (match) {
                        this.pendingSelectName = null;
                        this.select(match.id);
                        return;
                    }
                }
                if (this.selected !== NEW_ROOM && !this.rooms.find(room => room.id === this.selected)) {
                    this.select(MAIN_ROOM_ID);
                    return;
                }
                if (this.selected !== NEW_ROOM) {
                    const room = this.rooms.find(r => r.id === this.selected);
                    // only refill the form when the room itself changed
                    if (room && JSON.stringify(room) !== this.synced) {
                        this.select(this.selected);
                    }
                }
            },
        },
    },
    methods: {
        occupantsLabel(roomId) {
            const characters = ((this.scene && this.scene.data && this.scene.data.characters) || [])
                .filter(c => characterRoom(this.scene.data, c.name) === roomId)
                .map(c => c.name);
            return characters.length ? characters.join(', ') : 'Empty';
        },
        select(roomId) {
            this.selected = roomId;
            this.error = null;
            if (roomId === NEW_ROOM) {
                this.form = { name: '', label: '', description: '', empty_enter_message: '', empty_leave_message: '', color: '#7E57C233' };
                return;
            }
            const room = this.allRooms.find(r => r.id === roomId);
            if (!room) {
                this.form = null;
                return;
            }
            this.synced = JSON.stringify(room);
            this.form = {};
            for (const key of FIELDS) {
                this.form[key] = room[key] || '';
            }
        },
        payload() {
            const data = {};
            for (const key of FIELDS) {
                data[key] = (this.form[key] || '').toString();
            }
            data.name = data.name.trim();
            return data;
        },
        save() {
            this.error = null;
            this.busy = true;
            if (this.selected === NEW_ROOM) {
                const data = this.payload();
                this.pendingSelectName = data.name;
                this.getWebsocket().send(JSON.stringify({
                    type: 'world_state_manager',
                    action: 'add_room',
                    ...data,
                }));
            } else {
                this.getWebsocket().send(JSON.stringify({
                    type: 'world_state_manager',
                    action: 'update_room',
                    room_id: this.selected,
                    ...this.payload(),
                }));
            }
        },
        restore(room) {
            this.error = null;
            this.busy = true;
            this.pendingSelectName = room.name;
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'add_room',
                name: room.name,
            }));
        },
        remove() {
            this.error = null;
            this.busy = true;
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'delete_room',
                room_id: this.selected,
            }));
        },
        handleMessage(message) {
            if (message.type !== 'world_state_manager') {
                return;
            }
            if (message.action === 'operation_done' && this.busy) {
                this.busy = false;
                if (message.error) {
                    this.error = message.error.message;
                    this.pendingSelectName = null;
                }
            }
        },
    },
    mounted() {
        this.select(MAIN_ROOM_ID);
        this.registerMessageHandler(this.handleMessage);
    },
    unmounted() {
        this.unregisterMessageHandler(this.handleMessage);
    },
};
</script>

<style scoped>
.room-swatch {
    width: 14px;
    height: 14px;
    border-radius: 3px;
    border: 1px solid rgba(255, 255, 255, 0.3);
}
.tint-preview {
    border-radius: 4px;
    border: 1px dashed rgba(255, 255, 255, 0.2);
}
</style>
