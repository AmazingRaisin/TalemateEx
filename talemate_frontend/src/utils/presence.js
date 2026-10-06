// Layered history layers a presence threshold can be set for (the
// summarizer's maximum number of layers)
export const PRESENCE_THRESHOLD_LAYERS = 5;

/**
 * One threshold (percent) per layer, missing layers continue 10% below the
 * previous one. Mirrors talemate.history.normalize_presence_thresholds.
 */
export function normalizePresenceThresholds(values) {
    const thresholds = (values || [])
        .slice(0, PRESENCE_THRESHOLD_LAYERS)
        .map((value) => Math.min(Math.max(parseInt(value) || 0, 0), 100));

    while (thresholds.length < PRESENCE_THRESHOLD_LAYERS) {
        const previous = thresholds.length ? thresholds[thresholds.length - 1] : 100;
        thresholds.push(Math.max(previous - 10, 0));
    }

    return thresholds;
}
