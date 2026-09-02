/* ============================================================
   camera.js
   ------------------------------------------------------------
   Module 1 of the AI proctoring system.

   Responsibilities (ONLY these — detection logic lives in the
   other modules):
     1. Ask for webcam permission before the assessment starts.
     2. Block the assessment from starting until permission is
        granted (re-prompts if denied).
     3. Keep the camera stream alive for the whole assessment.
     4. Run a frame-capture loop (~every 500ms) and hand each
        frame to whatever detection modules have subscribed.
     5. Expose camera-connected / camera-disconnected state so
        the UI module can show the green/red indicator.
     6. Allow the video preview to be visually hidden while
        frames keep being processed underneath.

   This module does NOT touch:
     - the existing violation counter (state.violations)
     - fullscreen / tab-switch / clipboard blocking
     - MCQ/code round logic
   Those all stay exactly as they are in technical-assessment.html.

   Usage from technical-assessment.html (wired up in a later step):

       import { ProctorCamera } from './proctoring/camera.js';

       const cam = new ProctorCamera({
         videoElementId: 'proctorVideo',
         onFrame: (videoEl) => { ... feed frame to detectors ... },
         onStatusChange: (connected) => { ... paint HUD dot ... },
         frameIntervalMs: 500,
       });

       // Call this before startRound() is allowed to proceed:
       const granted = await cam.requestPermission();
if (!granted) {
    // keep showing the blocking prompt
}       cam.start();   // begins the capture loop
       cam.stop();    // call when the round ends / page unloads
       cam.hidePreview(); // keeps analyzing, just hides the <video>
============================================================ */

export class ProctorCamera {
  /**
   * @param {Object} opts
   * @param {string} opts.videoElementId - id of an existing <video> element
   *   in the page (created in the HTML step). Must be present in the DOM
   *   before you call start().
   * @param {(videoEl: HTMLVideoElement) => void} opts.onFrame - called on
   *   every capture tick with the live <video> element, so a detector can
   *   run inference directly against it (avoids extra canvas copies where
   *   possible; individual detectors can draw to their own canvas if they
   *   need one).
   * @param {(connected: boolean) => void} [opts.onStatusChange] - fired
   *   whenever the camera goes from connected -> disconnected or back.
   *   Used by the UI module to flip the green/red dot.
   * @param {(message: string) => void} [opts.onPermissionBlocked] - fired
   *   whenever permission is missing/denied, with a message to show the
   *   user. The caller decides how/where to render it.
   * @param {number} [opts.frameIntervalMs=500] - how often onFrame fires.
   */
  constructor(opts) {
    this.videoElementId = opts.videoElementId;
    this.onFrame = opts.onFrame || (() => {});
    this.onStatusChange = opts.onStatusChange || (() => {});
    this.onPermissionBlocked = opts.onPermissionBlocked || (() => {});
    this.frameIntervalMs = opts.frameIntervalMs || 500;

    /** @type {MediaStream|null} */
    this.stream = null;
    /** @type {HTMLVideoElement|null} */
    this.videoEl = null;

    this._rafId = null;
    this._lastFrameTime = 0;
    this._running = false;
    this._connected = false;

    // Bound once so we can add/remove the same listener reference.
    this._handleTrackEnded = this._handleTrackEnded.bind(this);
    this._tick = this._tick.bind(this);
  }

  /**
   * Requests webcam permission and attaches the resulting stream to the
   * <video> element. Does NOT start the frame loop — call start() after
   * this resolves true.
   *
   * Resolves:
   *   true  -> permission granted, stream attached, ready to start()
   *   false -> permission denied / no camera / other getUserMedia error.
   *            onPermissionBlocked() has already been called with a
   *            human-readable message in this case.
   */
  async requestPermission() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      this.onPermissionBlocked(
        'Camera access is mandatory to attend this assessment. ' +
        'Your browser does not support camera access — please use Chrome, Edge, or Brave.'
      );
      this._setConnected(false);
      return false;
    }

    this.videoEl = document.getElementById(this.videoElementId);
    if (!this.videoEl) {
      // Fail loudly during development — this means the HTML step
      // (adding <video id="proctorVideo">) hasn't been wired in yet.
      console.error(
        `[ProctorCamera] No element with id="${this.videoElementId}" found. ` +
        `Add the <video> element from the HTML integration step first.`
      );
      return false;
    }

    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        video: {
          width: { ideal: 320 },
          height: { ideal: 240 },
          facingMode: 'user',
        },
        audio: false,
      });
    } catch (err) {
      // Covers: user clicked "Block", no camera present, camera already
      // in use by another app, insecure context (non-HTTPS/non-localhost), etc.
      this.onPermissionBlocked(
        'Camera access is mandatory to attend this assessment.'
      );
      this._setConnected(false);
      return false;
    }

    this.videoEl.srcObject = this.stream;
    this.videoEl.muted = true;
    this.videoEl.playsInline = true;
    await this.videoEl.play().catch(() => {
      // Autoplay can be blocked in rare cases; play() will also be
      // retried implicitly once the frame loop starts and the video
      // element is visible/interacted with.
    });

    // Watch for the user revoking permission mid-assessment or the
    // camera being unplugged — both fire 'ended' on the track.
    this.stream.getVideoTracks().forEach((track) => {
      track.addEventListener('ended', this._handleTrackEnded);
    });

    this._setConnected(true);
    return true;
  }

  /** Starts the ~500ms frame capture loop. No-op if already running. */
  start() {
    if (this._running || !this.stream) return;
    this._running = true;
    this._lastFrameTime = 0;
    this._rafId = requestAnimationFrame(this._tick);
  }

  /** Stops the frame loop and releases the camera. Safe to call multiple times. */
  stop() {
    this._running = false;
    if (this._rafId) {
      cancelAnimationFrame(this._rafId);
      this._rafId = null;
    }
    if (this.stream) {
      this.stream.getTracks().forEach((track) => {
        track.removeEventListener('ended', this._handleTrackEnded);
        track.stop();
      });
      this.stream = null;
    }
    if (this.videoEl) {
      this.videoEl.srcObject = null;
    }
    this._setConnected(false);
  }

  /** Visually hides the preview but keeps frame processing running. */
  hidePreview() {
    if (this.videoEl) this.videoEl.style.display = 'none';
  }

  /** Shows the preview again. */
  showPreview() {
    if (this.videoEl) this.videoEl.style.display = '';
  }

  /** True if the camera is currently attached and streaming. */
  isConnected() {
    return this._connected;
  }

  // ---------------- internal ----------------

  _tick(now) {
    if (!this._running) return;

    if (now - this._lastFrameTime >= this.frameIntervalMs) {
      this._lastFrameTime = now;
      if (this.videoEl && this.videoEl.readyState >= this.videoEl.HAVE_CURRENT_DATA) {
        this.onFrame(this.videoEl);
      }
    }

    this._rafId = requestAnimationFrame(this._tick);
  }

  _handleTrackEnded() {
    // Camera was unplugged or permission was revoked mid-session.
    this._setConnected(false);
    this.onPermissionBlocked(
      'Camera access is mandatory to attend this assessment. ' +
      'Your camera was disconnected — please reconnect it to continue.'
    );
  }

  _setConnected(isConnected) {
    if (this._connected === isConnected) return;
    this._connected = isConnected;
    this.onStatusChange(isConnected);
  }
}