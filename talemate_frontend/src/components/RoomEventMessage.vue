<template>
    <div class="room-event-container">
        <v-alert color="muted" :icon="icon" variant="text" density="compact">
            <template v-slot:close>
                <v-tooltip location="top" text="Delete (deleting a character's latest move undoes it)">
                    <template v-slot:activator="{ props }">
                        <v-btn v-bind="props" size="small" icon variant="text" class="close-button" @click="requestDeleteMessage(message_id)" :disabled="uxLocked">
                            <v-icon>mdi-close</v-icon>
                        </v-btn>
                    </template>
                </v-tooltip>
            </template>
            <span class="room-event-text">{{ text }}</span>
            <v-tooltip v-if="private" location="top" :text="'Only ' + audienceLabel + ' noticed this.'">
                <template v-slot:activator="{ props }">
                    <v-chip v-bind="props" size="x-small" variant="text" color="muted" class="ml-2" prepend-icon="mdi-eye-off">unnoticed</v-chip>
                </template>
            </v-tooltip>
        </v-alert>
    </div>
</template>

<script>
export default {
    name: 'RoomEventMessage',
    props: ['text', 'message_id', 'event', 'audience', 'private', 'uxLocked'],
    inject: ['requestDeleteMessage'],
    computed: {
        icon() {
            if (this.event === 'exit') {
                return 'mdi-door-closed';
            }
            if (this.event === 'sneak') {
                return 'mdi-shoe-print';
            }
            return 'mdi-door-open';
        },
        audienceLabel() {
            const names = this.audience || [];
            if (names.length <= 1) {
                return names.join('');
            }
            return names.slice(0, -1).join(', ') + ' and ' + names[names.length - 1];
        },
    },
};
</script>

<style scoped>
.room-event-text {
    font-style: italic;
    opacity: 0.85;
}
.close-button {
    opacity: 0.5;
}
.close-button:hover {
    opacity: 1;
}
</style>
