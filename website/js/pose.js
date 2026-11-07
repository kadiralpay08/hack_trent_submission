// Camera, MediaPipe pose model, the frame loop, and the picture drawn in the side panel.
//
// The model is the same one pose_tracker.py uses (pose_landmarker_full, 0.4 confidence thresholds).
// Everything is loaded from website/vendor/ when it is there, so the site works offline; otherwise
// from a CDN.

import { CONNECTIONS, NOSE, L_SH, R_SH, L_EL, R_EL, L_WR, R_WR } from './detect.js';

const VERSION = '1.2.2';
const CDN = `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${VERSION}`;
const MODEL_CDN = 'https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task';
const LOCAL = { lib: 'vendor/mediapipe/vision_bundle.mjs', wasm: 'vendor/mediapipe/wasm', model: 'vendor/models/pose_landmarker_full.task' };

const page = (path) => new URL(path, document.baseURI).href;

async function exists(url) {
  try {
    const r = await fetch(url, { method: 'HEAD', cache: 'no-store' });
    return r.ok && !(r.headers.get('content-type') || '').includes('text/html');
  } catch { return false; }
}

/** Load the pose model. Returns { landmarker, delegate, local }. Tries the GPU, then the CPU. */
export async function loadPose({ cpu = false, onStatus = () => {} } = {}) {
  const local = await exists(page(`${LOCAL.wasm}/vision_wasm_internal.wasm`));
  onStatus(local ? 'Loading the pose model…' : 'Downloading the pose model (about 25 MB)…');
  const lib = await import(local ? page(LOCAL.lib) : `${CDN}/vision_bundle.mjs`);
  const fileset = await lib.FilesetResolver.forVisionTasks(local ? page(LOCAL.wasm) : `${CDN}/wasm`);
  const modelAssetPath = (await exists(page(LOCAL.model))) ? page(LOCAL.model) : MODEL_CDN;

  const make = async (delegate) => {
    const landmarker = await lib.PoseLandmarker.createFromOptions(fileset, {
      baseOptions: { modelAssetPath, delegate },
      runningMode: 'VIDEO',
      numPoses: 1,
      minPoseDetectionConfidence: 0.4,
      minPosePresenceConfidence: 0.4,
      minTrackingConfidence: 0.4,
    });
    // creation can succeed on a machine whose GPU then fails on the first picture, so try one now
    const blank = document.createElement('canvas');
    blank.width = blank.height = 64;
    landmarker.detectForVideo(blank, 1);
    return landmarker;
  };

  if (!cpu) {
    try { return { landmarker: await make('GPU'), delegate: 'GPU', local }; }
    catch (e) { console.warn('GPU pose model failed, using the CPU', e); }
  }
  return { landmarker: await make('CPU'), delegate: 'CPU', local };
}

// --- camera ---

export async function listCameras() {
  const devices = await navigator.mediaDevices.enumerateDevices();
  return devices.filter((d) => d.kind === 'videoinput');
}

export async function openCamera(video, deviceId = '') {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: false,
    video: {
      width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 30 },
      ...(deviceId ? { deviceId: { exact: deviceId } } : { facingMode: 'user' }),
    },
  });
  video.srcObject = stream;
  await video.play();
  return stream;
}

export function closeCamera(video) {
  video.srcObject?.getTracks().forEach((t) => t.stop());
  video.srcObject = null;
}

// --- frame loop ---

/** Runs the model on every new camera picture and hands the result to onResult(seconds, result). */
export class PoseLoop {
  constructor(video, landmarker, { onResult, onStall }) {
    Object.assign(this, { video, landmarker, onResult, onStall });
    this.running = false;
    this.lastVideoTime = -1;
    this.lastTs = 0;
    this.lastResultAt = 0;
  }

  start() {
    this.running = true;
    this.lastResultAt = performance.now();
    this._schedule();
    // requestAnimationFrame stops while the window is hidden or fully covered; timers still run
    this.watch = setInterval(() => {
      if (this.running && performance.now() - this.lastResultAt > 1000) {
        this.lastResultAt = performance.now();
        this.onStall?.();
      }
    }, 500);
  }

  stop() { this.running = false; clearInterval(this.watch); }

  _schedule() { requestAnimationFrame(() => this._tick()); }

  _tick() {
    if (!this.running) return;
    const v = this.video;
    if (v.readyState >= 2 && v.currentTime !== this.lastVideoTime) {
      this.lastVideoTime = v.currentTime;
      const nowMs = performance.now();
      const ts = Math.max(Math.round(nowMs), this.lastTs + 1);  // the model needs strictly rising times
      this.lastTs = ts;
      try {
        const result = this.landmarker.detectForVideo(v, ts);
        this.lastResultAt = performance.now();
        this.onResult(nowMs / 1000, result);
      } catch (e) {
        console.error('pose model error', e);
      }
    }
    this._schedule();
  }
}

// --- drawing ---

const COLORS = { good: '#3dff7a', off: '#ffb020', lost: '#ff3355' };
const KEY_POINTS = [NOSE, L_SH, R_SH, L_EL, R_EL, L_WR, R_WR];

/**
 * Draw the mirrored camera picture, the skeleton and the border.
 * view: { body, img, vis, flash: {L: [text, time], R: [...]}, now, level, showGuide }
 */
export function drawCamera(ctx, video, view) {
  const { width: w, height: h } = ctx.canvas;
  ctx.save();
  ctx.translate(w, 0);
  ctx.scale(-1, 1);  // mirror, like looking in a mirror; the landmarks are already mirrored
  ctx.drawImage(video, 0, 0, w, h);
  ctx.restore();

  if (view.showGuide) {  // where to stand
    ctx.save();
    ctx.setLineDash([14, 10]);
    ctx.strokeStyle = 'rgba(255,255,255,0.55)';
    ctx.lineWidth = 3;
    ctx.strokeRect(w * 0.12, h * 0.06, w * 0.76, h * 0.88);
    ctx.restore();
  }

  if (view.body) {
    const { img, vis } = view;
    const px = img.map(([x, y]) => [x * w, y * h]);
    ctx.save();
    ctx.lineCap = 'round';
    ctx.strokeStyle = COLORS.good;
    ctx.shadowColor = 'rgba(0,0,0,0.8)';
    ctx.shadowBlur = 6;
    ctx.lineWidth = Math.max(3, w / 180);
    for (const [a, b] of CONNECTIONS) {
      if (vis[a] > 0.4 && vis[b] > 0.4) {
        ctx.beginPath();
        ctx.moveTo(px[a][0], px[a][1]);
        ctx.lineTo(px[b][0], px[b][1]);
        ctx.stroke();
      }
    }
    for (const i of KEY_POINTS) {
      if (vis[i] < 0.4) continue;
      const wrist = i === L_WR || i === R_WR;
      ctx.beginPath();
      ctx.arc(px[i][0], px[i][1], (wrist ? 1.9 : 1.2) * w / 110, 0, Math.PI * 2);
      ctx.fillStyle = wrist ? '#ffffff' : COLORS.good;
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = COLORS.good;
      ctx.stroke();
    }
    ctx.restore();

    // the name of a punch, next to the hand that threw it
    ctx.save();
    ctx.font = `800 ${Math.round(w / 16)}px system-ui, sans-serif`;
    ctx.lineJoin = 'round';
    for (const [side, idx] of [['L', L_WR], ['R', R_WR]]) {
      const [text, t] = view.flash[side];
      const age = view.now - t;
      if (!text || age > 0.6) continue;
      ctx.globalAlpha = 1 - age / 0.6;
      ctx.lineWidth = 6;
      ctx.strokeStyle = '#000';
      ctx.strokeText(text, px[idx][0] + 12, px[idx][1] - 12);
      ctx.fillStyle = '#ff3355';
      ctx.fillText(text, px[idx][0] + 12, px[idx][1] - 12);
    }
    ctx.restore();
  }

  ctx.strokeStyle = COLORS[view.level] || COLORS.lost;  // the border says whether you are in a good place
  ctx.lineWidth = Math.max(6, w / 90);
  ctx.strokeRect(0, 0, w, h);
}

/** Whether the head, shoulders and both wrists are visible and inside the picture. Returns a hint, or '' if fine. */
export function framingHint(img, vis) {
  const need = [NOSE, L_SH, R_SH, L_WR, R_WR];
  if (need.some((i) => vis[i] < 0.5)) return 'Your head and both hands must stay in view';
  const margin = 0.02;
  const out = need.filter((i) => img[i][0] < margin || img[i][0] > 1 - margin || img[i][1] < margin || img[i][1] > 1 - margin);
  if (out.length) return 'You are touching the edge of the picture: step back or re-aim the camera';
  return '';
}
