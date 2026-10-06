<template>
  <div class="talemate-root">
    <TalemateApp />
  </div>
</template>

<script>
import TalemateApp from './components/TalemateApp.vue'

export default {
  name: 'App',
  components: {
    TalemateApp
  }
}
</script>

<style>
html,
body,
#app {
  min-height: 100%;
  overflow-x: hidden !important;
  overflow-y: auto !important;
}

body {
  margin: 0;
}
</style>

<style scoped>
.talemate-root {
  min-height: 100%;
}
</style>
