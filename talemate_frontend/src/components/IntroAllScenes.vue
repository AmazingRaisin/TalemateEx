<template>
    <section class="all-scenes-page">
        <div class="all-scenes-heading" ref="top">
            <div>
                <div class="text-h6">All Scenes</div>
                <div class="text-caption text-medium-emphasis">{{ countLabel }}</div>
            </div>
        </div>

        <div class="all-scenes-toolbar">
            <v-text-field
                v-model="search"
                label="Search scenes"
                prepend-inner-icon="mdi-magnify"
                variant="outlined"
                density="compact"
                clearable
                hide-details
                class="all-scenes-search"
            />
            <v-select
                v-model="pageSize"
                :items="pageSizes"
                label="Per page"
                variant="outlined"
                density="compact"
                hide-details
                class="all-scenes-page-size"
            />
            <v-btn
                icon
                variant="text"
                color="primary"
                :loading="loading"
                @click="requestScenes"
            >
                <v-icon>mdi-refresh</v-icon>
                <v-tooltip activator="parent" location="bottom">Refresh</v-tooltip>
            </v-btn>
        </div>

        <v-alert v-if="!loading && filteredScenes.length === 0" color="muted" variant="tonal" class="ma-2">
            No scenes found.
        </v-alert>

        <v-pagination
            v-if="pageCount > 1"
            v-model="page"
            :length="pageCount"
            :total-visible="7"
            density="compact"
            class="all-scenes-pagination mb-2"
        />

        <div class="all-scenes-grid">
            <v-card
                v-for="scene in pagedScenes"
                :key="scene.path"
                class="scene-card"
                color="grey-darken-3"
                variant="outlined"
                :disabled="!sceneLoadingAvailable || sceneIsLoading"
            >
                <div class="scene-card-content">
                    <div class="scene-cover scene-load-target" @click="loadScene(scene)">
                        <img
                            v-if="thumbnailSrc(scene)"
                            :src="thumbnailSrc(scene)"
                            alt=""
                            loading="lazy"
                            decoding="async"
                            class="scene-cover-image"
                        />
                    </div>

                    <div class="scene-details">
                        <div class="scene-title-row">
                            <div class="scene-title text-primary scene-load-target" @click="loadScene(scene)">{{ sceneTitle(scene) }}</div>
                            <v-chip v-if="scene.modified" size="x-small" label color="muted" variant="tonal">
                                {{ prettyDate(scene.modified) }}
                            </v-chip>
                        </div>
                        <div class="scene-path text-caption text-medium-emphasis">{{ scene.label }}</div>
                        <div class="scene-description">
                            {{ scene.description || 'No scene description.' }}
                        </div>
                    </div>
                </div>
            </v-card>
        </div>

        <v-pagination
            v-if="pageCount > 1"
            v-model="page"
            :length="pageCount"
            :total-visible="7"
            class="all-scenes-pagination mt-4"
        />
    </section>
</template>

<script>

// Many scenes with large cover images were too much to show at once: the list
// is paged, and covers are small thumbnails (made and cached by the backend)
// of the scenes on the current page, kept as blob URLs (not base64 strings).

const PAGE_SIZES = [12, 24, 48, 96];
const DEFAULT_PAGE_SIZE = 24;
const PAGE_SIZE_STORAGE_KEY = 'talemate.allScenes.pageSize';
// thumbnails kept for going back to pages seen before
const MAX_THUMBNAILS = 300;

function storedPageSize() {
    try {
        const value = parseInt(localStorage.getItem(PAGE_SIZE_STORAGE_KEY), 10);
        return PAGE_SIZES.includes(value) ? value : DEFAULT_PAGE_SIZE;
    } catch {
        return DEFAULT_PAGE_SIZE;
    }
}

export default {
    name: 'IntroAllScenes',
    props: {
        sceneIsLoading: Boolean,
        sceneLoadingAvailable: Boolean,
    },
    inject: [
        'getWebsocket',
        'registerMessageHandler',
        'unregisterMessageHandler',
    ],
    emits: ['request-scene-load'],
    data() {
        return {
            scenes: [],
            // cover image id -> blob URL
            thumbnails: {},
            loading: false,
            search: '',
            page: 1,
            pageSize: storedPageSize(),
            pageSizes: PAGE_SIZES,
        }
    },
    watch: {
        sceneLoadingAvailable(available) {
            if (available && this.scenes.length === 0) {
                this.requestScenes();
            }
        },
        search() {
            this.page = 1;
        },
        pageSize(value) {
            this.page = 1;
            try {
                localStorage.setItem(PAGE_SIZE_STORAGE_KEY, String(value));
            } catch {
                // not remembered, still used
            }
        },
        page() {
            const top = this.$refs.top;
            if (top && top.getBoundingClientRect().top < 0) {
                top.scrollIntoView({ block: 'start' });
            }
        },
        pageCount(count) {
            if (this.page > count) {
                this.page = Math.max(1, count);
            }
        },
        pagedScenes() {
            this.requestThumbnails();
        },
    },
    computed: {
        sortedScenes() {
            return [...this.scenes].sort((a, b) => (b.modified || 0) - (a.modified || 0));
        },
        filteredScenes() {
            const query = (this.search || '').trim().toLowerCase();
            const scenes = this.sortedScenes;

            if (!query) {
                return scenes;
            }

            return scenes.filter(scene => {
                return [
                    scene.name,
                    scene.title,
                    scene.filename,
                    scene.label,
                    scene.description,
                ].some(value => String(value || '').toLowerCase().includes(query));
            });
        },
        pageCount() {
            return Math.max(1, Math.ceil(this.filteredScenes.length / this.pageSize));
        },
        pagedScenes() {
            const start = (this.page - 1) * this.pageSize;
            return this.filteredScenes.slice(start, start + this.pageSize);
        },
        countLabel() {
            const total = this.scenes.length;
            const found = this.filteredScenes.length;
            const count = found === total ? `${total} scenes` : `${found} of ${total} scenes`;

            if (this.pageCount <= 1) {
                return count;
            }

            const start = (this.page - 1) * this.pageSize + 1;
            const end = Math.min(found, start + this.pageSize - 1);
            return `${count} · showing ${start}–${end}`;
        },
    },
    methods: {
        requestScenes() {
            if (!this.getWebsocket()) {
                this.loading = false;
                return;
            }

            this.loading = true;
            this.getWebsocket().send(JSON.stringify({
                type: 'request_scenes_list',
                query: '',
                list_images: false,
                include_metadata: true,
            }));
        },
        requestThumbnails() {
            // the thumbnails of the current page that are missing (a new
            // request replaces the one before, so this asks for all of them)
            const websocket = this.getWebsocket();
            if (!websocket) {
                return;
            }

            const assets = [];
            const seen = new Set();

            for (const scene of this.pagedScenes) {
                const id = scene.cover_image && scene.cover_image.id;
                if (!id || this.thumbnails[id] || seen.has(id)) {
                    continue;
                }

                seen.add(id);
                assets.push({
                    path: scene.path,
                    id: id,
                    file_type: scene.cover_image.file_type,
                });
            }

            if (assets.length > 0) {
                websocket.send(JSON.stringify({ type: 'request_scene_thumbnails', assets }));
            }
        },
        thumbnailSrc(scene) {
            return scene.cover_image ? this.thumbnails[scene.cover_image.id] || null : null;
        },
        addThumbnail(id, base64, mediaType) {
            if (!id || !base64 || this.thumbnails[id]) {
                return;
            }

            const binary = atob(base64);
            const bytes = new Uint8Array(binary.length);
            for (let i = 0; i < binary.length; i++) {
                bytes[i] = binary.charCodeAt(i);
            }
            this.thumbnails[id] = URL.createObjectURL(new Blob([bytes], { type: mediaType }));
            this.dropOldThumbnails();
        },
        dropOldThumbnails() {
            const ids = Object.keys(this.thumbnails);
            if (ids.length <= MAX_THUMBNAILS) {
                return;
            }

            const shown = new Set(this.pagedScenes.map(scene => scene.cover_image && scene.cover_image.id));
            let excess = ids.length - MAX_THUMBNAILS;

            // oldest first
            for (const id of ids) {
                if (excess <= 0) {
                    break;
                }
                if (shown.has(id)) {
                    continue;
                }
                URL.revokeObjectURL(this.thumbnails[id]);
                delete this.thumbnails[id];
                excess--;
            }
        },
        loadScene(scene) {
            if (!this.sceneLoadingAvailable || this.sceneIsLoading) {
                return;
            }
            this.$emit('request-scene-load', scene.path);
        },
        sceneTitle(scene) {
            return scene.title || scene.name || this.filenameToTitle(scene.filename || '');
        },
        filenameToTitle(filename) {
            return filename
                .replace('.json', '')
                .replace(/_/g, ' ')
                .replace(/-/g, ' ')
                .replace(/\b\w/g, letter => letter.toUpperCase());
        },
        prettyDate(timestamp) {
            return new Date(timestamp * 1000).toLocaleDateString();
        },
        handleMessage(data) {
            if (data.type === 'scenes_list' && data.metadata) {
                this.scenes = data.data || [];
                this.loading = false;
                return;
            }

            if (data.type === 'scene_thumbnail') {
                this.addThumbnail(data.id, data.base64, data.media_type);
            }
        },
    },
    mounted() {
        this.requestScenes();
    },
    created() {
        this.registerMessageHandler(this.handleMessage);
    },
    unmounted() {
        this.unregisterMessageHandler(this.handleMessage);
        for (const id in this.thumbnails) {
            URL.revokeObjectURL(this.thumbnails[id]);
        }
        this.thumbnails = {};
    },
}

</script>

<style scoped>
.all-scenes-page {
    padding: 8px 12px 24px 12px;
}

.all-scenes-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;
}

.all-scenes-toolbar {
    display: flex;
    align-items: center;
    gap: 8px;
    max-width: 920px;
    margin-bottom: 12px;
}

.all-scenes-search {
    max-width: 640px;
}

.all-scenes-page-size {
    flex: 0 0 120px;
}

.all-scenes-pagination {
    justify-content: flex-start;
}

.all-scenes-pagination :deep(.v-pagination__list) {
    justify-content: flex-start;
}

.all-scenes-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(520px, 1fr));
    gap: 12px;
}

.scene-card {
    min-height: 220px;
    overflow: hidden;
}

.scene-card-content {
    display: grid;
    grid-template-columns: 160px minmax(0, 1fr);
    height: 100%;
    min-height: 220px;
    min-width: 0;
    align-items: stretch;
}

.scene-cover {
    position: relative;
    min-height: 220px;
    background-color: transparent;
    background-image: url('/src/assets/logo-13.1-backdrop.png');
    background-repeat: no-repeat;
    background-position: center;
    background-size: cover;
    overflow: hidden;
}

.scene-load-target {
    cursor: pointer;
}

.scene-cover-image {
    position: absolute;
    inset: 0;
    display: block;
    width: 100%;
    height: 100%;
    object-fit: cover;
    object-position: top center;
}

.scene-details {
    display: flex;
    flex-direction: column;
    min-width: 0;
    min-height: 0;
    height: 100%;
    overflow: hidden;
    padding: 12px;
}

.scene-title-row {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
}

.scene-title {
    font-size: 1rem;
    font-weight: 600;
    line-height: 1.25;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.scene-path {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    margin-top: 2px;
}

.scene-description {
    flex: 1 1 0;
    height: 0;
    min-height: 0;
    overflow-y: auto;
    margin-top: 10px;
    padding: 10px;
    border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
    background: rgba(0, 0, 0, 0.16);
    color: rgba(255, 255, 255, 0.76);
    white-space: pre-wrap;
    line-height: 1.45;
    font-size: 0.9rem;
}

@media (max-width: 700px) {
    .all-scenes-grid {
        grid-template-columns: 1fr;
    }

    .scene-card-content {
        grid-template-columns: 1fr;
    }

    .scene-details {
        height: auto;
    }

    .scene-description {
        flex: 0 1 auto;
        height: auto;
        max-height: 180px;
    }

    .scene-cover {
        min-height: 180px;
    }
}
</style>
