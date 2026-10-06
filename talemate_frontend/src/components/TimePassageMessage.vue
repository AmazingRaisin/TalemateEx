<template>
  <div class="time-container" v-if="show && minimized"
       @mouseenter="hovered = true" @mouseleave="hovered = false">

    <v-alert color="time" icon="mdi-clock-outline" variant="text" @dblclick="!editing && startEdit()">
      <template v-slot:close>
        <v-btn v-if="!editing" size="small" icon variant="text" class="close-button" @click="deletePassage" :disabled="uxLocked">
          <v-icon>mdi-close</v-icon>
        </v-btn>
      </template>
      <span v-if="!editing">{{ text }}</span>
      <v-chip v-if="hovered && !editing" size="x-small" color="grey-lighten-1" variant="text" class="ml-2">
        <v-icon>mdi-pencil</v-icon>
        Double-click to edit.
      </v-chip>
      <div v-if="editing">
        <TimeDurationInput v-model="editDuration" />
        <div class="d-flex align-center mt-2">
          <span class="text-caption">{{ editLabel }}</span>
          <v-spacer></v-spacer>
          <v-btn class="ml-2" color="success" variant="text" size="small"
              prepend-icon="mdi-content-save" @click="saveEdit" :disabled="uxLocked || !editIsoDuration">Save</v-btn>
          <v-btn class="ml-1" color="cancel" variant="text" size="small"
              prepend-icon="mdi-cancel" @click="cancelEdit">Cancel</v-btn>
        </div>
      </div>
    </v-alert>

    <v-divider class="mb-4"></v-divider>

  </div>
</template>

<script>
import TimeDurationInput from './TimeDurationInput.vue';
import {
  componentsToIsoDuration,
  durationComponentsToHuman,
  emptyDurationComponents,
  isoDurationToComponents,
} from '@/utils/time';

export default {
  components: { TimeDurationInput },
  data() {
    return {
      show: true,
      minimized: true,
      hovered: false,
      editing: false,
      editDuration: emptyDurationComponents(),
    }
  },
  props: ['text', 'message_id', 'ts', 'uxLocked', 'isLastMessage'],
  inject: ['getWebsocket', 'getMessageStyle', 'getMessageColor'],
  computed: {
    editIsoDuration() {
      return componentsToIsoDuration(this.editDuration);
    },
    editLabel() {
      return durationComponentsToHuman(this.editDuration);
    },
  },
  methods: {
    toggle() {
      this.minimized = !this.minimized;
    },
    deletePassage() {
      this.getWebsocket().send(JSON.stringify({
        type: 'time_passage',
        action: 'delete',
        message_id: this.message_id,
      }));
    },
    startEdit() {
      this.editDuration = isoDurationToComponents(this.ts) || emptyDurationComponents();
      this.editing = true;
    },
    saveEdit() {
      const duration = this.editIsoDuration;
      if (!duration) {
        return;
      }
      this.getWebsocket().send(JSON.stringify({
        type: 'time_passage',
        action: 'update',
        message_id: this.message_id,
        duration: duration,
      }));
      this.editing = false;
    },
    cancelEdit() {
      this.editing = false;
    },
  }
}
</script>

<style scoped>
.close-button {
  opacity: 0.4;
  color: rgba(255, 255, 255, 0.6) !important;
  transition: opacity 0.2s ease;
}

.close-button:hover {
  opacity: 1;
  color: rgba(255, 255, 255, 0.9) !important;
}
</style>
