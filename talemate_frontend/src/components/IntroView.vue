<template>
  <v-tabs v-model="tab" color="primary" class="mb-2">
    <v-tab value="quick" prepend-icon="mdi-folder-clock">Quick Load</v-tab>
    <v-tab value="all" prepend-icon="mdi-view-grid-outline">All Scenes</v-tab>
  </v-tabs>

  <v-window v-model="tab">
    <v-window-item value="quick">
      <IntroRecentScenes :config="config" :scene-is-loading="sceneIsLoading" :scene-loading-available="sceneLoadingAvailable"  @request-scene-load="requestSceneLoad" @request-backup-restore="requestBackupRestore"/>
    </v-window-item>
    <v-window-item value="all">
      <IntroAllScenes :scene-is-loading="sceneIsLoading" :scene-loading-available="sceneLoadingAvailable" @request-scene-load="requestSceneLoad" />
    </v-window-item>
  </v-window>

  <WhatsNew />
</template>

<script>

import IntroAllScenes from './IntroAllScenes.vue';
import IntroRecentScenes from './IntroRecentScenes.vue';
import WhatsNew from './WhatsNew.vue';

export default {
  name: 'IntroView',
  components: {
    IntroAllScenes,
    IntroRecentScenes,
    WhatsNew,
  },
  props: {
    version: String,
    sceneLoadingAvailable: Boolean,
    sceneIsLoading: Boolean,
    config: Object,
  },
  emits: ['request-scene-load', 'request-backup-restore'],
  data() {
    return {
      tab: 'quick',
      changelog: [
        "This screen was added",
        "Item 2",
        "Item 3",
        "Item 4",
      ]
    }
  },
  methods: {
    requestSceneLoad(scene) {
      this.$emit('request-scene-load', scene);
    },
    requestBackupRestore(restoreInfo) {
      this.$emit('request-backup-restore', restoreInfo);
    }
  }
}

</script>
