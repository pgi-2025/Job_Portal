/* ============================================================
   headPose.js
   ------------------------------------------------------------
   Module 3 of the AI proctoring system.

   Responsibilities (ONLY these):
     1. Load MediaPipe's Face Landmarker (Face Mesh) with facial
        transformation matrix output enabled — this gives us head
        rotation (yaw/pitch) without hand-rolling geometry math.
     2. On every frame, estimate whether the person is looking
        left, right, up, or down (vs. facing the screen).
     3. Track how long they've been continuously looking away —
        fires a violation once that exceeds 5 seconds.
     4. Reset the timer the instant they look back at the screen.
     5. Report violations via callback ONLY — same pattern as
        faceDetection.js. This module never touches the violation
        counter directly.

   This module does NOT:
     - open/manage the camera (frames are handed to it by camera.js)
     - do face-count / no-face / multiple-faces logic (faceDetection.js)
     - do phone detection (phoneDetection.js)
     - decide what happens after MAX_VIOLATIONS is hit

   Note: this module runs its OWN MediaPipe model (Face Landmarker),
   separate from faceDetection.js's Face Detector model. They are
   different MediaPipe tasks tuned for different jobs (fast face
   presence/count vs. detailed landmark/pose estimation), which is
   also why the spec lists them as separate detectors. Running both
   is normal for proctoring systems and stays within the 500ms frame
   budget set by camera.js.

   Usage:

       import { HeadPoseMonitor } from './proctoring/headPose.js';

       const headPoseMonitor = new HeadPoseMonitor({
         onLookingAwayViolation: (meta) => { ... },
         onWarning: (message) => { ... show temporary popup ... },
       });

       await headPoseMonitor.init();

       // Inside camera.js's onFrame callback:
       headPoseMonitor.processFrame(videoEl);
============================================================ */

import {
  FaceLandmarker,
  FilesetResolver,
} from 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.mjs';

const LOOKING_AWAY_THRESHOLD_MS = 5_000; // 5 seconds, per spec

// Degrees of rotation beyond which we consider the person "looking away"
// rather than just naturally shifting slightly while reading the screen.
const YAW_THRESHOLD_DEG = 22; // left/right
const PITCH_THRESHOLD_DEG = 18; // up/down

const MODEL_URL =
  'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task';
const WASM_BASE_URL =
  'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm';

export class HeadPoseMonitor {
  /**
   * @param {Object} opts
   * @param {(meta: {direction: string, yawDeg: number, pitchDeg: number, confidence: number, detectedAt: string}) => void} opts.onLookingAwayViolation
   *   Called once when looking-away has been continuous for > 5s.
   * @param {(message: string) => void} [opts.onWarning]
   */
  constructor(opts) {
    this.onLookingAwayViolation = opts.onLookingAwayViolation || (() => {});
    this.onWarning = opts.onWarning || (() => {});

    /** @type {FaceLandmarker|null} */
    this._landmarker = null;
    this._ready = false;

    this._awaySinceMs = null;
    this._awayViolationFired = false;
  }

  /**
   * Loads the MediaPipe Face Landmarker. Must be awaited before the
   * first call to processFrame().
   */
  async init() {
    if (this._ready) return;
    const filesetResolver = await FilesetResolver.forVisionTasks(WASM_BASE_URL);
    this._landmarker = await FaceLandmarker.createFromOptions(filesetResolver, {
      baseOptions: {
        modelAssetPath: MODEL_URL,
        delegate: 'GPU',
      },
      runningMode: 'VIDEO',
      numFaces: 1,
      outputFacialTransformationMatrixes: true, // gives us head rotation directly
      outputFaceBlendshapes: false, // not needed here — keeps inference lighter
    });
    this._ready = true;
  }

  /**
   * Runs head-pose estimation on the given <video> element and updates
   * the looking-away state machine. Call from camera.js's onFrame
   * callback (already throttled to ~500ms).
   *
   * @param {HTMLVideoElement} videoEl
   */
  processFrame(videoEl) {
    if (!this._ready || !this._landmarker) return;

    const nowMs = performance.now();
    let result;
    try {
      result = this._landmarker.detectForVideo(videoEl, nowMs);
    } catch (err) {
      console.warn('[HeadPoseMonitor] detection failed for this frame:', err);
      return;
    }

    const matrices = result.facialTransformationMatrixes;
    if (!matrices || matrices.length === 0) {
      // No face to estimate pose from right now — faceDetection.js
      // already owns the "no face" violation, so we just idle here
      // rather than double-counting. Also reset our own timer so we
      // don't fire a stale "looking away" the moment the face returns.
      this._awaySinceMs = null;
      this._awayViolationFired = false;
      return;
    }

    const { yawDeg, pitchDeg } = this._extractYawPitch(matrices[0].data);
    const direction = this._classifyDirection(yawDeg, pitchDeg);

    this._evaluateLookingAway(direction, yawDeg, pitchDeg);
  }

  /** Releases the model and resets state. Call when the round ends. */
  dispose() {
    if (this._landmarker) {
      this._landmarker.close();
      this._landmarker = null;
    }
    this._ready = false;
    this._awaySinceMs = null;
    this._awayViolationFired = false;
  }

  // ---------------- internal ----------------

  /**
   * The facial transformation matrix is a 4x4 row-major rotation+translation
   * matrix describing the face's orientation relative to the camera.
   * We only need yaw (rotation around Y) and pitch (rotation around X),
   * extracted the standard way from a rotation matrix.
   */
  _extractYawPitch(m) {
    // m is a flat 16-element Float32Array, row-major 4x4.
    // Rotation submatrix (top-left 3x3):
    //   [ m0  m1  m2 ]
    //   [ m4  m5  m6 ]
    //   [ m8  m9  m10]
    const r00 = m[0], r02 = m[2];
    const r10 = m[4], r11 = m[5], r12 = m[6];
    const r20 = m[8], r22 = m[10];

    const yawRad = Math.atan2(r02, r00);
    const pitchRad = Math.atan2(-r12, Math.sqrt(r10 * r10 + r11 * r11));
    // (roll is available via atan2(r10, r00) if ever needed — not used here)

    return {
      yawDeg: (yawRad * 180) / Math.PI,
      pitchDeg: (pitchRad * 180) / Math.PI,
    };
  }

  _classifyDirection(yawDeg, pitchDeg) {
    if (Math.abs(yawDeg) >= YAW_THRESHOLD_DEG) {
      return yawDeg > 0 ? 'left' : 'right';
      // Note: sign convention depends on the model's coordinate system;
      // verify left/right feel correct during testing and flip the
      // comparison here if they appear reversed for your camera setup.
    }
    if (Math.abs(pitchDeg) >= PITCH_THRESHOLD_DEG) {
      return pitchDeg > 0 ? 'up' : 'down';
    }
    return 'center';
  }

  _evaluateLookingAway(direction, yawDeg, pitchDeg) {
    if (direction !== 'center') {
      if (this._awaySinceMs === null) {
        this._awaySinceMs = performance.now();
      }
      const elapsed = performance.now() - this._awaySinceMs;
      if (elapsed > LOOKING_AWAY_THRESHOLD_MS && !this._awayViolationFired) {
        this._awayViolationFired = true;
        this.onWarning('Please look at the screen.');
        this.onLookingAwayViolation({
          direction,
          yawDeg: Math.round(yawDeg * 10) / 10,
          pitchDeg: Math.round(pitchDeg * 10) / 10,
          confidence: 0.8, // pose estimation doesn't yield a single
                           // "confidence" score the way object detection
                           // does; fixed value kept for schema consistency.
          detectedAt: new Date().toISOString(),
        });
      }
    } else {
      // Looking at the screen again — reset so a future look-away
      // starts a fresh 5-second timer.
      this._awaySinceMs = null;
      this._awayViolationFired = false;
    }
  }
}