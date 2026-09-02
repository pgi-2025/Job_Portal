/* ============================================================
   faceDetection.js
   ------------------------------------------------------------
   Module 2 of the AI proctoring system.

   Responsibilities (ONLY these):
     1. Load MediaPipe's Face Detector (via @mediapipe/tasks-vision,
        loaded from CDN — no npm build step required).
     2. On every frame handed to it by camera.js, count how many
        faces are visible.
     3. Track "no face" duration — fires a violation once the face
        has been continuously absent for > 10 seconds.
     4. Track "multiple faces" — fires a violation immediately when
        a second face appears, and does not re-fire again until the
        extra person leaves (count drops back to <= 1) and then
        reappears.
     5. Report violations via callbacks ONLY. This module never
        touches the violation counter directly — violationManager.js
        (built next) is the single place that talks to the existing
        state.violations counter in technical-assessment.html.

   This module does NOT:
     - open/manage the camera (camera.js owns the <video> element
       and the capture loop — this module is just handed frames)
     - decide what happens after MAX_VIOLATIONS is hit
     - touch fullscreen/tab-switch/clipboard logic

   Usage (wired up fully once violationManager.js exists):

       import { FaceDetectionMonitor } from './proctoring/faceDetection.js';

       const faceMonitor = new FaceDetectionMonitor({
         onNoFaceViolation: (meta) => { ... },
         onMultipleFacesViolation: (meta) => { ... },
         onWarning: (message) => { ... show temporary popup ... },
       });

       await faceMonitor.init();               // load the model once

       // Then, inside camera.js's onFrame callback:
       faceMonitor.processFrame(videoEl);
============================================================ */

// Loaded from CDN — no bundler/npm install required, matches the
// "keep it simple, don't break the existing plain-HTML/JS setup" goal.
import {
  FaceDetector,
  FilesetResolver,
} from 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.mjs';

const NO_FACE_THRESHOLD_MS = 10_000; // 10 seconds, per spec
const MODEL_URL =
  'https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite';
const WASM_BASE_URL =
  'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm';

export class FaceDetectionMonitor {
  /**
   * @param {Object} opts
   * @param {(meta: {confidence: number, detectedAt: string}) => void} opts.onNoFaceViolation
   *   Called once when no face has been visible for > 10s continuously.
   * @param {(meta: {faceCount: number, confidence: number, detectedAt: string}) => void} opts.onMultipleFacesViolation
   *   Called once when 2+ faces appear (fires immediately per spec).
   * @param {(message: string) => void} [opts.onWarning] - fired alongside
   *   each violation with the human-readable message to show in the UI.
   */
  constructor(opts) {
    this.onNoFaceViolation = opts.onNoFaceViolation || (() => {});
    this.onMultipleFacesViolation = opts.onMultipleFacesViolation || (() => {});
    this.onWarning = opts.onWarning || (() => {});

    /** @type {FaceDetector|null} */
    this._detector = null;
    this._ready = false;

    // --- "no face" tracking ---
    this._noFaceSinceMs = null; // timestamp when face count first became 0
    this._noFaceViolationFired = false; // suppress repeats until a face is seen again

    // --- "multiple faces" tracking ---
    this._multipleFacesActive = false; // suppress repeats until count drops back to <=1
  }

  /**
   * Loads the MediaPipe face detector. Must be awaited before the first
   * call to processFrame(). Safe to call once during assessment setup
   * (e.g. right after camera permission is granted).
   */
  async init() {
    if (this._ready) return;
    const filesetResolver = await FilesetResolver.forVisionTasks(WASM_BASE_URL);
    this._detector = await FaceDetector.createFromOptions(filesetResolver, {
      baseOptions: {
        modelAssetPath: MODEL_URL,
        delegate: 'GPU',
      },
      runningMode: 'VIDEO',
      minDetectionConfidence: 0.5,
    });
    this._ready = true;
  }

  /**
   * Runs face detection on the given <video> element and updates the
   * no-face / multiple-faces state machines. Call this from camera.js's
   * onFrame callback (already throttled to ~500ms there, so no extra
   * throttling is needed here).
   *
   * @param {HTMLVideoElement} videoEl
   */
  processFrame(videoEl) {
    if (!this._ready || !this._detector) return;

    const nowMs = performance.now();
    let result;
    try {
      result = this._detector.detectForVideo(videoEl, nowMs);
    } catch (err) {
      // A transient failure (e.g. video not ready) shouldn't crash the
      // assessment — just skip this tick and try again on the next frame.
      console.warn('[FaceDetectionMonitor] detection failed for this frame:', err);
      return;
    }

    const faces = result.detections || [];
    const faceCount = faces.length;

    this._evaluateNoFace(faceCount, faces);
    this._evaluateMultipleFaces(faceCount, faces);
  }

  /** Stops tracking and releases the model. Call when the round ends. */
  dispose() {
    if (this._detector) {
      this._detector.close();
      this._detector = null;
    }
    this._ready = false;
    this._noFaceSinceMs = null;
    this._noFaceViolationFired = false;
    this._multipleFacesActive = false;
  }

  // ---------------- internal ----------------

  _evaluateNoFace(faceCount, faces) {
    if (faceCount === 0) {
      if (this._noFaceSinceMs === null) {
        this._noFaceSinceMs = performance.now();
      }
      const elapsed = performance.now() - this._noFaceSinceMs;
      if (elapsed > NO_FACE_THRESHOLD_MS && !this._noFaceViolationFired) {
        this._noFaceViolationFired = true;
        this.onWarning('No face detected. Please stay in front of the camera.');
        this.onNoFaceViolation({
          confidence: 1.0, // absence isn't a model confidence score; 1.0 = certain
          detectedAt: new Date().toISOString(),
        });
      }
    } else {
      // A face is visible again — reset the absence timer/flag so a
      // future disappearance can trigger a fresh violation.
      this._noFaceSinceMs = null;
      this._noFaceViolationFired = false;
    }
  }

  _evaluateMultipleFaces(faceCount, faces) {
    if (faceCount > 1) {
      if (!this._multipleFacesActive) {
        this._multipleFacesActive = true;
        const bestConfidence = Math.max(
          ...faces.map((f) => f.categories?.[0]?.score ?? 0.5)
        );
        this.onWarning('Multiple people detected.');
        this.onMultipleFacesViolation({
          faceCount,
          confidence: bestConfidence,
          detectedAt: new Date().toISOString(),
        });
      }
      // While still > 1, do nothing further — suppressed until it drops.
    } else {
      // Back to 0 or 1 face — the extra person left. Re-arm for next time.
      this._multipleFacesActive = false;
    }
  }
}