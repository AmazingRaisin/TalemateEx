<template>
    <v-card elevation="7" class="my-4" density="compact">
        <v-card-title class="text-body-2 text-time">
            <v-icon class="mr-2" size="small">mdi-clock-fast</v-icon>
            Time Passage
        </v-card-title>
        <v-card-text>
            <div v-if="!editing" class="d-flex align-center"
                 @dblclick="setEditing(true)"
                 @mouseenter="hovered = true"
                 @mouseleave="hovered = false">
                <span class="text-body-1">{{ passage.human }}</span>
                <span v-if="hovered" class="text-caption text-muted ml-2">
                    <v-icon size="small">mdi-pencil</v-icon> double-click to edit
                </span>
                <v-spacer />
                <ConfirmActionInline
                    v-if="hovered"
                    :disabled="locked"
                    icon="mdi-close-box-outline"
                    color="delete"
                    action-label="Delete"
                    confirm-label="Confirm"
                    @confirm="deletePassage"
                />
            </div>
            <div v-else>
                <TimeDurationInput v-model="editDuration" />
                <div class="d-flex align-center mt-2">
                    <span class="text-caption">{{ editLabel }}</span>
                    <v-spacer />
                    <v-btn class="ml-2" color="success" variant="text" size="small"
                        prepend-icon="mdi-content-save" @click="save" :disabled="locked || !editIsoDuration">Save</v-btn>
                    <v-btn class="ml-1" color="cancel" variant="text" size="small"
                        prepend-icon="mdi-cancel" @click="cancel">Cancel</v-btn>
                </div>
            </div>
        </v-card-text>
    </v-card>
</template>

<script>
import ConfirmActionInline from './ConfirmActionInline.vue';
import TimeDurationInput from './TimeDurationInput.vue';
import {
    componentsToIsoDuration,
    durationComponentsToHuman,
    emptyDurationComponents,
    isoDurationToComponents,
} from '@/utils/time';

export default {
    name: 'WorldStateManagerTimePassageEntry',
    components: { ConfirmActionInline, TimeDurationInput },
    props: {
        passage: Object,
        appBusy: Boolean,
        appReady: {
            type: Boolean,
            default: true,
        },
        busy: Boolean,
    },
    inject: ['getWebsocket'],
    data() {
        return {
            editing: false,
            hovered: false,
            editDuration: emptyDurationComponents(),
        };
    },
    computed: {
        locked() {
            return this.appBusy || !this.appReady || this.busy;
        },
        editIsoDuration() {
            return componentsToIsoDuration(this.editDuration);
        },
        editLabel() {
            return durationComponentsToHuman(this.editDuration);
        },
    },
    methods: {
        setEditing(val) {
            if (this.locked) return;
            this.editing = val;
            if (val) {
                this.editDuration = isoDurationToComponents(this.passage.ts) || emptyDurationComponents();
            }
        },
        save() {
            const duration = this.editIsoDuration;
            if (!duration) return;
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'update_time_passage',
                history_index: this.passage.history_index,
                duration: duration,
            }));
            this.editing = false;
        },
        cancel() {
            this.editing = false;
        },
        deletePassage() {
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'delete_time_passage',
                history_index: this.passage.history_index,
            }));
        },
    },
};
</script>
