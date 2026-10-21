/* ============================================================
   violationManager.js
   ------------------------------------------------------------
   Module 5 of the AI proctoring system — the integration layer.

   Responsibilities (ONLY these):
     1. Own the single "bridge" function that all 4 detection
        modules (faceDetection.js, headPose.js, phoneDetection.js)
        call into when they fire a violation.
     2. Apply the correct weight per violation type:
           phone            -> +1
           no_face          -> +1
           looking_away     -> +1
           multiple_faces   -> +2
     3. Call the EXISTING registerViolation(reason) function that
        already lives in technical-assessment.html — this is the
        single violation counter (state.violations). This module
        does NOT create a second counter.
     4. Send each violation to the backend for logging into the
        new assessment_violations Supabase table (fire-and-forget,
        same pattern as the existing submitAssessmentResult()).
     5. Own the small "temporary warning popup" UI helper used by
        all 4 detectors' onWarning callbacks.

   This module does NOT:
     - do any detection itself (that's modules 1-4)
     - touch fullscreen/tab-switch/clipboard/devtools logic
     - touch MCQ/code round logic, timers, or auto-submit —
       auto-submit already happens inside the EXISTING
       registerViolation() once state.violations >= MAX_VIOLATIONS,
       so calling that function is all this module needs to do.

   IMPORTANT — how this plugs into technical-assessment.html:

     technical-assessment.html already defines, at page scope:
         let state = { ..., violations: 0, testActive: false, ... };
         const MAX_VIOLATIONS = 3;
         function registerViolation(reason) { ... state.violations++ ... }
         const token = sessionStorage.getItem('grove_token');

     Because those are plain globals (not ES module exports), this
     module reads them directly via `window.state`, `window.registerViolation`,
     and `window.sessionStorage` — no changes to technical-assessment.html's
     existing variable declarations are required. (Top-level `let`/`const`/
     `function` declared directly in a <script> tag attach to `window`
     automatically for `function`, and are accessible as globals for
     `let`/`const` within the same page — the HTML integration step
     will confirm this wiring with a short smoke test.)

   Usage (full wiring shown in the HTML integration step):

       import { ViolationManager } from './proctoring/violationManager.js';
       import { ProctorCamera } from './proctoring/camera.js';
       import { FaceDetectionMonitor } from './proctoring/faceDetection.js';
       import { HeadPoseMonitor } from './proctoring/headPose.js';
       import { PhoneDetectionMonitor } from './proctoring/phoneDetection.js';

       const violationManager = new ViolationManager({
         apiBase: API_BASE,          // reuse the existing API_BASE constant
         authHeaders: authHeaders,   // reuse the existing authHeaders() fn
         getAssessmentId: () => state.domain?.id || null,
       });

       const faceMonitor = new FaceDetectionMonitor({
         onNoFaceViolation: (meta) => violationManager.report('no_face', meta),
         onMultipleFacesViolation: (meta) => violationManager.report('multiple_faces', meta),
         onWarning: (msg) => violationManager.showWarning(msg),
       });
       // ...headPoseMonitor and phoneMonitor wired the same way...
============================================================ */

// Weight per violation type, per spec section 6.
const VIOLATION_WEIGHTS = {
  phone: 1,
  no_face: 1,
  looking_away: 1,
  multiple_faces: 2,
};

// Human-readable "reason" strings passed into the EXISTING
// registerViolation(reason) function — shown nowhere new, just kept
// consistent with the style of the existing reasons already used
// there (e.g. 'Exited fullscreen', 'Switched tab / minimized window').
const VIOLATION_REASONS = {
  phone: 'Mobile phone detected',
  no_face: 'No face detected',
  looking_away: 'Looking away from screen',
  multiple_faces: 'Multiple people detected',
};

export class ViolationManager {
  /**
   * @param {Object} opts
   * @param {string} opts.apiBase - same API_BASE constant already
   *   defined in technical-assessment.html.
   * @param {(extra?: object) => object} opts.authHeaders - the existing
   *   authHeaders() helper already defined in technical-assessment.html.
   * @param {() => string|null} opts.getAssessmentId - returns the current
   *   domain/assessment id (e.g. state.domain.id) at the moment a
   *   violation fires, so the log entry can be tied to the right attempt.
   * @param {string} [opts.warningContainerId='proctorWarnings'] - id of
   *   a container element (added in the HTML step) that temporary
   *   warning popups get appended to.
   */
  constructor(opts) {
    this.apiBase = opts.apiBase;
    this.authHeaders = opts.authHeaders;
    this.getAssessmentId = opts.getAssessmentId || (() => null);
    this.warningContainerId = opts.warningContainerId || 'proctorWarnings';

    // Guard against reporting violations before the round is actually
    // active (e.g. a stray late frame arriving just after a round ends).
    this._enabled = false;
  }

  /** Call when a proctored round starts (alongside camera.start()). */
  enable() {
    this._enabled = true;
  }

  /** Call when a round ends/is submitted (alongside camera.stop()). */
  disable() {
    this._enabled = false;
  }

  /**
   * The single entry point every detection module calls into.
   *
   * @param {'phone'|'no_face'|'looking_away'|'multiple_faces'} type
   * @param {object} meta - whatever metadata the detector captured
   *   (confidence, direction, faceCount, detectedAt, ...).
   */
  report(type, meta) {
    if (!this._enabled) return;

    const weight = VIOLATION_WEIGHTS[type];
    const reason = VIOLATION_REASONS[type];
    if (!weight || !reason) {
      console.warn(`[ViolationManager] unknown violation type: ${type}`);
      return;
    }

    // Route into the EXISTING single counter. registerViolation() already
    // increments state.violations by 1 and handles MAX_VIOLATIONS ->
    // auto-submit/flag internally — so for a 2-point violation
    // (multiple_faces) we simply call it twice. This keeps
    // registerViolation() itself completely unmodified.
    for (let i = 0; i < weight; i++) {
      if (typeof window.registerViolation === 'function') {
        window.registerViolation(reason);
      } else {
        console.error(
          '[ViolationManager] window.registerViolation is not defined — ' +
          'make sure this script loads AFTER technical-assessment.html\'s ' +
          'main <script> block, or that registerViolation is exposed on window.'
        );
      }
    }

    this._logToBackend(type, meta);
  }

  /**
   * Shows a temporary warning popup. Shared by all 4 detectors'
   * onWarning callbacks, plus usable standalone (e.g. camera.js's
   * onPermissionBlocked message can also route through this).
   *
   * @param {string} message
   * @param {number} [durationMs=3500]
   */
  showWarning(message, durationMs = 3500) {
    let container = document.getElementById(this.warningContainerId);
    if (!container) {
      // Fail soft — log to console rather than throwing, so a missing
      // HTML element never breaks the actual proctoring/violation logic.
      console.warn(
        `[ViolationManager] No #${this.warningContainerId} element found — ` +
        `add it in the HTML integration step. Warning was: ${message}`
      );
      return;
    }
    const popup = document.createElement('div');
    popup.className = 'proctor-warning-popup';
    popup.textContent = message;
    container.appendChild(popup);
    setTimeout(() => popup.remove(), durationMs);
  }

  // ---------------- internal ----------------

  async _logToBackend(type, meta) {
    const assessmentId = this.getAssessmentId();
    const token =
      (typeof window.sessionStorage !== 'undefined' &&
        window.sessionStorage.getItem('grove_token')) ||
      null;

    if (!token) {
      console.warn('[ViolationManager] no auth token available — skipping backend log.');
      return;
    }

    try {
      await fetch(`${this.apiBase}/api/student/assessment/violation`, {
        method: 'POST',
        headers: this.authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          assessment_id: assessmentId,
          violation_type: type,
          confidence: meta?.confidence ?? null,
          detected_at: meta?.detectedAt ?? new Date().toISOString(),
        }),
      });
    } catch (err) {
      // Fire-and-forget, same tolerance as the existing
      // submitAssessmentResult() — a logging failure must never
      // interrupt the assessment itself.
      console.warn('[ViolationManager] failed to log violation to backend:', err);
    }
  }
}