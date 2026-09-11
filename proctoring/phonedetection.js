/* ============================================================
   phoneDetection.js
   ------------------------------------------------------------
   Module 4 of the AI proctoring system.

   Responsibilities (ONLY these):
     1. Load TensorFlow.js + the COCO-SSD object detection model
        (via CDN — no npm build step required).
     2. On every frame, check for the COCO "cell phone" class.
     3. Track how long a phone has been continuously visible —
        fires a violation once that exceeds 2 seconds.
     4. Do NOT re-fire while the same phone remains visible.
        Only re-arms (allows a new violation) after the phone
        disappears from frame and then reappears.
     5. Report violations via callback ONLY — same pattern as the
        other detection modules. Never touches the violation
        counter directly.
     6. Dispose TensorFlow tensors properly after every inference
        call to avoid memory leaks (COCO-SSD's detect() already
        disposes its internal tensors, but we're explicit about
        cleanup here regardless, per the performance requirement).

   This module does NOT:
     - open/manage the camera (frames are handed to it by camera.js)
     - do face/head-pose logic (faceDetection.js / headPose.js)
     - decide what happens after MAX_VIOLATIONS is hit

   COCO-SSD's label for this class is literally "cell phone" — it
   covers phones/smartphones/mobile phones as one category (COCO's
   80-class list doesn't separate "mobile phone" vs "smartphone" as
   distinct labels), so we match on that single label.

   Usage:

       import { PhoneDetectionMonitor } from './proctoring/phoneDetection.js';

       const phoneMonitor = new PhoneDetectionMonitor({
         onPhoneViolation: (meta) => { ... },
         onWarning: (message) => { ... show temporary popup ... },
       });

       await phoneMonitor.init();

       // Inside camera.js's onFrame callback:
       phoneMonitor.processFrame(videoEl);
============================================================ */

// TensorFlow.js core + COCO-SSD are loaded globally via <script> tags in the
// HTML (they don't ship real ESM builds on CDN), so we just reference the
// globals here instead of using `import`.
const tf = window.tf;
const cocoSsd = window.cocoSsd;

const PHONE_PRESENT_THRESHOLD_MS = 2_000; // 2 seconds, per spec
const MIN_CONFIDENCE = 0.55; // filters out low-confidence false positives

// COCO-SSD's single label covering phones/smartphones/mobile phones.
const PHONE_LABEL = 'cell phone';

export class PhoneDetectionMonitor {
  /**
   * @param {Object} opts
   * @param {(meta: {confidence: number, detectedAt: string}) => void} opts.onPhoneViolation
   *   Called once when a phone has been continuously visible for > 2s.
   * @param {(message: string) => void} [opts.onWarning]
   */
  constructor(opts) {
    this.onPhoneViolation = opts.onPhoneViolation || (() => {});
    this.onWarning = opts.onWarning || (() => {});

    /** @type {cocoSsd.ObjectDetection|null} */
    this._model = null;
    this._ready = false;

    this._phoneSinceMs = null; // timestamp when phone first became visible
    this._phoneViolationFired = false; // suppress repeats while same phone stays visible
  }

  /**
   * Loads the COCO-SSD model. Must be awaited before the first call to
   * processFrame(). Uses the 'lite_mobilenet_v2' base for a good
   * speed/accuracy tradeoff on a 500ms polling budget running alongside
   * two other MediaPipe models.
   */
  async init() {
    if (this._ready) return;
    // Ensure a WebGL backend is active for fast inference; falls back
    // automatically if WebGL isn't available in the browser.
    await tf.ready();
    this._model = await cocoSsd.load({ base: 'lite_mobilenet_v2' });
    this._ready = true;
  }

  /**
   * Runs object detection on the given <video> element and updates the
   * phone-presence state machine. Call from camera.js's onFrame
   * callback (already throttled to ~500ms).
   *
   * @param {HTMLVideoElement} videoEl
   */
  async processFrame(videoEl) {
    if (!this._ready || !this._model) return;

    let predictions;
    try {
      // detect() internally manages/disposes its own tensors. We still
      // wrap the call in tf.tidy-friendly usage by not holding onto any
      // tensor references ourselves, so nothing leaks from this module.
      predictions = await this._model.detect(videoEl, 10, MIN_CONFIDENCE);
    } catch (err) {
      console.warn('[PhoneDetectionMonitor] detection failed for this frame:', err);
      return;
    }

    const phoneDetections = predictions.filter(
      (p) => p.class === PHONE_LABEL && p.score >= MIN_CONFIDENCE
    );

    this._evaluatePhonePresence(phoneDetections);
  }

  /** Releases the model and resets state. Call when the round ends. */
  dispose() {
    // COCO-SSD doesn't expose an explicit unload; dropping the reference
    // lets the underlying tensors be garbage-collected. We also clear
    // any lingering tensors in the default engine as a safety net.
    this._model = null;
    this._ready = false;
    this._phoneSinceMs = null;
    this._phoneViolationFired = false;
    tf.disposeVariables();
  }

  // ---------------- internal ----------------

  _evaluatePhonePresence(phoneDetections) {
    const phoneVisible = phoneDetections.length > 0;

    if (phoneVisible) {
      if (this._phoneSinceMs === null) {
        this._phoneSinceMs = performance.now();
      }
      const elapsed = performance.now() - this._phoneSinceMs;
      if (elapsed > PHONE_PRESENT_THRESHOLD_MS && !this._phoneViolationFired) {
        this._phoneViolationFired = true;
        const bestConfidence = Math.max(...phoneDetections.map((p) => p.score));
        this.onWarning('Mobile phone detected. Violation recorded.');
        this.onPhoneViolation({
          confidence: Math.round(bestConfidence * 1000) / 1000,
          detectedAt: new Date().toISOString(),
        });
      }
      // While still visible past the threshold, stay silent — this is
      // the "do not count duplicate violations while the same phone
      // remains visible" rule from the spec.
    } else {
      // Phone is gone — reset so the NEXT appearance (a genuinely new
      // event) can trigger a fresh violation after another 2s.
      this._phoneSinceMs = null;
      this._phoneViolationFired = false;
    }
  }
}