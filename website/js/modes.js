// The product variants (see variants/README.md) as the website offers them.

import { makeSettings } from './detect.js';

export const MODES = {
  angled_camera: {
    id: 'angled_camera',
    title: 'One camera at 45°',
    badge: 'Shelved',
    summary: 'One webcam, you turned about 45° to it. Set aside by the team in favour of the glove: hooks were too often read as crosses or uppercuts. The code is kept in archive/angled_camera.',
    needs: 'Webcam 1.5 to 2 m away, aimed at your upper body.',
    detects: 'Jab, cross, hook, uppercut',
    settingsUrl: 'settings/angled_camera.json',
    angleGuide: true,
    glove: false,
    available: false,  // to bring it back: set this to true and the badge to 'Ready' (see archive/angled_camera/README.md)
  },
  front_glove: {
    id: 'front_glove',
    title: 'Front camera + glove',
    badge: 'Needs glove',
    summary: 'Webcam in front of you, plus a glove with a motion sensor. The camera picks the punch and the glove confirms the hand really punched. This is the earlier rule-based version (controller.py --glove). The trained model used by play.bat is not in the website yet.',
    needs: 'The key bridge running with --glove, and the glove sending to this laptop.',
    detects: 'Hook, uppercut (a webcam facing you cannot see straight punches)',
    settingsUrl: null,  // the detector defaults, as controller.py uses them
    angleGuide: false,
    glove: true,
    available: true,
  },
  two_cameras: {
    id: 'two_cameras',
    title: 'Front + side camera',
    badge: 'Not built yet',
    summary: 'Two webcams, no gloves: the side camera sees what the front one cannot.',
    needs: 'Two webcams.',
    detects: 'Not built yet',
    settingsUrl: null,
    angleGuide: false,
    glove: false,
    available: false,
  },
};

/**
 * Detector settings for a mode. Returns { settings, source }.
 * The bridge serves the live variants/<mode>/settings.json, so tuning there is picked up on reload;
 * the hosted site uses the copy in website/settings/.
 */
export async function loadSettings(mode, lead = 'L') {
  let values = {}, source = 'detector defaults';
  if (mode.settingsUrl) {
    const response = await fetch(mode.settingsUrl, { cache: 'no-store' });
    if (!response.ok) throw new Error(`could not load ${mode.settingsUrl} (${response.status})`);
    values = await response.json();
    source = response.headers.get('X-Settings-Source') || 'bundled copy (website/settings)';
  }
  return { settings: makeSettings({ ...values, lead }), source };
}
