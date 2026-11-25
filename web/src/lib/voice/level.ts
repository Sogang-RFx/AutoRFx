/**
 * Loudness of the microphone or of the playback as a number from 0 to 1, for the sphere. Only real samples go in:
 * silence stays 0. Fast attack and slow release keep the sphere from flickering with every syllable.
 */

/** Time-domain bytes of an analyser are centred on 128. */
const CENTRE = 128;
/** Plain speech has an RMS of roughly 0.05 to 0.25 on that scale. */
const GAIN = 4;
/** RMS under this is room noise. */
const NOISE_GATE = 0.012;

/** Per second: how fast the shown level follows a louder (attack) and a quieter (release) input. */
export const ATTACK_RATE = 30;
export const RELEASE_RATE = 5;

const clamp01 = (v: number): number => (v < 0 ? 0 : v > 1 ? 1 : v);

export function inputLevel(samples: Uint8Array): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (const v of samples) {
    const x = (v - CENTRE) / CENTRE;
    sum += x * x;
  }
  const rms = Math.sqrt(sum / samples.length);
  return rms < NOISE_GATE ? 0 : clamp01(rms * GAIN);
}

/** Moves `current` towards `target` over `dt` seconds. A missing or negative step changes nothing. */
export function smoothInputLevel(current: number, target: number, dt: number): number {
  if (!(dt > 0)) return current;
  const rate = target > current ? ATTACK_RATE : RELEASE_RATE;
  return current + (target - current) * (1 - Math.exp(-rate * dt));
}
