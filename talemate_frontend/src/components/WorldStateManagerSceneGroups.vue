<template>
    <div :style="{ maxWidth: MAX_CONTENT_WIDTH }">
        <v-alert density="compact" variant="text" color="muted" class="mt-2 text-caption" icon="mdi-account-group">
            Groups are characters that speak and act through one turn together, saving a conversation call per character.
            Add and remove members with <strong>Control Scene &gt; Group Characters</strong> in the chat tools.
        </v-alert>
        <v-row class="mt-2">
            <v-col cols="12" md="4">
                <v-list density="compact" nav>
                    <v-list-item
                        v-for="group in groups"
                        :key="group.id"
                        :active="selected === group.id"
                        color="primary"
                        @click="select(group.id)"
                    >
                        <template v-slot:prepend>
                            <div class="group-swatch mr-3" :style="{ backgroundColor: group.color }"></div>
                        </template>
                        <v-list-item-title>{{ group.id }}</v-list-item-title>
                        <v-list-item-subtitle>{{ membersLabel(group) }}</v-list-item-subtitle>
                    </v-list-item>
                    <v-list-item :active="selected === NEW_GROUP" color="primary" prepend-icon="mdi-plus" @click="select(NEW_GROUP)">
                        <v-list-item-title>Add group</v-list-item-title>
                    </v-list-item>
                </v-list>
            </v-col>

            <v-col cols="12" md="8">
                <v-card v-if="form" variant="tonal">
                    <v-card-title class="text-subtitle-1">
                        <span :style="{ color: form.color }">{{ selected === NEW_GROUP ? 'New group' : selected }}</span>
                    </v-card-title>
                    <v-card-text>
                        <v-text-field
                            v-model="form.id"
                            label="ID"
                            messages="Names the group in the menus. The chat names its members."
                        ></v-text-field>

                        <div v-if="currentGroup" class="mt-4">
                            <div class="text-caption text-muted mb-1">Members</div>
                            <div v-if="currentGroup.members.length">
                                <v-chip
                                    v-for="name in currentGroup.members"
                                    :key="name"
                                    size="small"
                                    label
                                    class="mr-1 mb-1"
                                    :color="memberColor(name)"
                                    :variant="currentGroup.present.includes(name) ? 'tonal' : 'outlined'"
                                >
                                    {{ name }}
                                    <span v-if="!currentGroup.present.includes(name)" class="text-muted ml-1">(not taking part)</span>
                                    <v-icon v-else-if="!currentGroup.speakers.includes(name)" size="x-small" class="ml-1">mdi-volume-off</v-icon>
                                </v-chip>
                            </div>
                            <div v-else class="text-caption text-muted">No members yet.</div>
                            <div class="text-caption text-muted mt-1">{{ turnsLabel(currentGroup) }}</div>
                        </div>

                        <v-checkbox
                            v-model="form.share_history"
                            label="All characters must share messages / history"
                            density="compact"
                            hide-details
                            class="mt-4"
                        ></v-checkbox>
                        <div class="text-caption text-muted ml-10 mb-2">
                            On: the group's prompts only get what every member perceived (messages, private parts, other characters' private info, lore, where people are).
                            Off: what any of them perceived.
                        </div>

                        <v-row class="mt-2">
                            <v-col cols="12" md="6">
                                <v-number-input
                                    v-model="form.converse_length_override"
                                    label="Converse Length Override"
                                    :min="0"
                                    :step="32"
                                    control-variant="split"
                                    messages="Generation length (tokens) of the group's turns, 0 uses the conversation agent's."
                                ></v-number-input>
                            </v-col>
                            <v-col cols="12" md="6">
                                <v-number-input
                                    v-model="form.background_turn_count"
                                    label="Background Turn Count"
                                    :min="1"
                                    control-variant="split"
                                    messages="Away from the player character's room, the group takes every Nth turn."
                                ></v-number-input>
                            </v-col>
                        </v-row>

                        <v-row class="mt-2">
                            <v-col cols="12" lg="6">
                                <v-textarea
                                    v-model="form.scene_description_override"
                                    label="Scene Description Override"
                                    rows="3"
                                    auto-grow
                                    clearable
                                    messages="Blank uses the global scene description for this group."
                                ></v-textarea>
                            </v-col>
                            <v-col cols="12" lg="6">
                                <v-textarea
                                    v-model="form.scene_intent_override"
                                    label="Overall Intention Override"
                                    rows="3"
                                    auto-grow
                                    clearable
                                    messages="Blank uses the global overall intention for this group."
                                ></v-textarea>
                            </v-col>
                        </v-row>

                        <div class="mt-4">
                            <div class="d-flex align-center mb-2">
                                <span class="text-body-2 mr-3">Color</span>
                                <span class="text-body-2" :style="{ color: form.color }">{{ previewLabel }}</span>
                            </div>
                            <v-color-picker
                                :model-value="form.color || null"
                                @update:model-value="(value) => form.color = value || DEFAULT_COLOR"
                                mode="hex"
                                :modes="['hex']"
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
                            v-if="selected !== NEW_GROUP"
                            action-label="Delete group"
                            confirm-label="Confirm delete"
                            icon="mdi-delete"
                            @confirm="remove"
                        />
                        <v-spacer></v-spacer>
                        <v-btn
                            color="primary"
                            variant="text"
                            :prepend-icon="selected === NEW_GROUP ? 'mdi-plus' : 'mdi-content-save'"
                            :disabled="!form.id || !form.id.trim() || busy"
                            @click="save"
                        >{{ selected === NEW_GROUP ? 'Add group' : 'Save' }}</v-btn>
                    </v-card-actions>
                </v-card>
                <div v-else class="text-caption text-muted mt-4">
                    Add a group, then pick its members in the chat with Control Scene &gt; Group Characters.
                </div>
                <v-alert v-if="error" type="error" variant="tonal" density="compact" class="mt-2">{{ error }}</v-alert>
            </v-col>
        </v-row>
    </div>
</template>

<script>
import ConfirmActionInline from './ConfirmActionInline.vue';
import { MAX_CONTENT_WIDTH } from '@/constants';
import { groupLabel, sceneGroups } from '@/utils/groups';

const NEW_GROUP = '__new__';
const DEFAULT_COLOR = '#b39ddb';
const FIELDS = [
    'color',
    'share_history',
    'converse_length_override',
    'background_turn_count',
    'scene_description_override',
    'scene_intent_override',
];

export default {
    name: 'WorldStateManagerSceneGroups',
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
            NEW_GROUP,
            DEFAULT_COLOR,
            selected: null,
            form: null,
            busy: false,
            error: null,
            pendingSelectId: null,
            // the group as it was when the form was filled
            synced: null,
            swatches: [
                ['#b39ddb', '#9fa8da', '#90caf9'],
                ['#80cbc4', '#a5d6a7', '#e6ee9c'],
                ['#ffe082', '#ffab91', '#f48fb1'],
                ['#bcaaa4', '#b0bec5', '#ce93d8'],
            ],
        };
    },
    computed: {
        groups() {
            return sceneGroups(this.scene && this.scene.data);
        },
        currentGroup() {
            return this.groups.find(group => group.id === this.selected) || null;
        },
        previewLabel() {
            const group = this.currentGroup;
            if (group && group.speakers.length > 1) {
                return groupLabel(group.speakers);
            }
            return 'Frieren, Fern, and Stark';
        },
    },
    watch: {
        groups: {
            deep: true,
            handler() {
                if (this.pendingSelectId) {
                    const match = this.groups.find(group => group.id === this.pendingSelectId);
                    if (match) {
                        this.pendingSelectId = null;
                        this.select(match.id);
                        return;
                    }
                }
                if (this.selected !== NEW_GROUP && !this.currentGroup) {
                    this.select(this.groups.length ? this.groups[0].id : null);
                    return;
                }
                if (this.selected !== NEW_GROUP && this.currentGroup) {
                    // only refill the form when the group itself changed
                    if (JSON.stringify(this.currentGroup) !== this.synced) {
                        this.select(this.selected);
                    }
                }
            },
        },
    },
    methods: {
        membersLabel(group) {
            return group.members.length ? group.members.join(', ') : 'No members';
        },
        turnsLabel(group) {
            if (group.takes_turns) {
                return `Takes turns for ${groupLabel(group.speakers)}.`;
            }
            if (group.speakers.length === 1) {
                return `Only ${group.speakers[0]} can speak right now, so they take their own turns.`;
            }
            return 'No one in it can speak right now, the group takes no turns.';
        },
        memberColor(name) {
            const colors = (this.scene && this.scene.data && this.scene.data.character_colors) || {};
            return colors[name] || undefined;
        },
        select(groupId) {
            this.selected = groupId;
            this.error = null;
            if (groupId === null) {
                this.form = null;
                return;
            }
            if (groupId === NEW_GROUP) {
                this.form = {
                    id: '',
                    color: DEFAULT_COLOR,
                    share_history: false,
                    converse_length_override: 0,
                    background_turn_count: 1,
                    scene_description_override: '',
                    scene_intent_override: '',
                };
                return;
            }
            const group = this.groups.find(g => g.id === groupId);
            if (!group) {
                this.form = null;
                return;
            }
            this.synced = JSON.stringify(group);
            this.form = { id: group.id };
            for (const key of FIELDS) {
                this.form[key] = group[key];
            }
        },
        payload() {
            return {
                color: this.form.color || DEFAULT_COLOR,
                share_history: !!this.form.share_history,
                converse_length_override: Math.max(parseInt(this.form.converse_length_override) || 0, 0),
                background_turn_count: Math.max(parseInt(this.form.background_turn_count) || 1, 1),
                scene_description_override: this.form.scene_description_override || '',
                scene_intent_override: this.form.scene_intent_override || '',
            };
        },
        save() {
            this.error = null;
            this.busy = true;
            const id = this.form.id.trim();
            this.pendingSelectId = id;
            if (this.selected === NEW_GROUP) {
                this.getWebsocket().send(JSON.stringify({
                    type: 'world_state_manager',
                    action: 'add_character_group',
                    group_id: id,
                    ...this.payload(),
                }));
            } else {
                this.getWebsocket().send(JSON.stringify({
                    type: 'world_state_manager',
                    action: 'update_character_group',
                    group_id: this.selected,
                    new_id: id,
                    ...this.payload(),
                }));
            }
        },
        remove() {
            this.error = null;
            this.busy = true;
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'delete_character_group',
                group_id: this.selected,
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
                    this.pendingSelectId = null;
                }
            }
        },
    },
    mounted() {
        this.select(this.groups.length ? this.groups[0].id : null);
        this.registerMessageHandler(this.handleMessage);
    },
    unmounted() {
        this.unregisterMessageHandler(this.handleMessage);
    },
};
</script>

<style scoped>
.group-swatch {
    width: 14px;
    height: 14px;
    border-radius: 50%;
    border: 1px solid rgba(255, 255, 255, 0.3);
}
</style>
