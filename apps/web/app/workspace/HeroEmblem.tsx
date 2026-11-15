import { useEffect, useId, useRef } from "react";
import styles from "./ResearchChat.module.css";

const vertices = [[19, 2], [23, 14.6], [36, 19], [23, 23.3], [19, 36], [14.8, 23.3], [2, 19], [14.8, 14.6]];
const outline = `M${vertices.map(point => point.join(" ")).join("L")}Z`;
const outlinePalette = [[105, 195, 240], [110, 144, 236], [160, 127, 222], [225, 140, 194], [152, 139, 224]];
const colorCycleSeconds = 18;

const edges = vertices.map(([x, y], index) => {
  const [endX, endY] = vertices[(index + 1) % vertices.length];
  const dx = endX - x;
  const dy = endY - y;
  return { x, y, dx, dy, length: Math.hypot(dx, dy) };
});
const perimeter = edges.reduce((sum, edge) => sum + edge.length, 0);
let edgeOffset = 0;
const outlineSegments = edges.flatMap(({ x, y, dx, dy, length }) => {
  const count = Math.ceil(length / 1.05);
  const segments = Array.from({ length: count }, (_, index) => {
    const start = index / count;
    const end = (index + 1) / count;
    return {
      d: `M${(x + dx * start).toFixed(3)} ${(y + dy * start).toFixed(3)}L${(x + dx * end).toFixed(3)} ${(y + dy * end).toFixed(3)}`,
      position: (edgeOffset + length * (start + end) / 2) / perimeter,
    };
  });
  edgeOffset += length;
  return segments;
});

function outlineColor(position: number) {
  const step = ((position % 1 + 1) % 1) * outlinePalette.length;
  const index = Math.floor(step);
  const first = outlinePalette[index];
  const last = outlinePalette[(index + 1) % outlinePalette.length];
  return `rgb(${first.map((channel, i) => Math.round(channel + (last[i] - channel) * (step - index))).join(",")})`;
}

const fieldSize = 176;
const fieldCenter = fieldSize / 2;
const particleCount = 13;
const particleLimit = 77;
const fullCircle = 2 * Math.PI;
const fireflyColors = ["114, 196, 255", "173, 126, 255", "255, 139, 198", "196, 164, 255"];
const random = (min: number, max: number) => min + Math.random() * (max - min);
const ease = (t: number) => t * t * (3 - 2 * t);

function spawnFirefly(angle = random(0, fullCircle), stagger = false) {
  const distance = random(42, 69);
  const heading = random(0, fullCircle);
  const speed = random(0.55, 1.75);
  const fadeIn = random(1.4, 2.4);
  const holdUntil = fadeIn + random(4.5, 8);
  const fadeUntil = holdUntil + random(1.7, 2.8);
  const lifetime = fadeUntil + random(3.5, 7);
  return {
    x: Math.cos(angle) * distance,
    y: Math.sin(angle) * distance,
    vx: Math.cos(heading) * speed,
    vy: Math.sin(heading) * speed,
    radius: random(0.85, 1.65),
    color: fireflyColors[Math.floor(random(0, fireflyColors.length))],
    brightness: random(0.8, 0.96),
    fadeIn, holdUntil, fadeUntil, lifetime,
    age: stagger ? random(0, lifetime) : 0,
  };
}

function paintFirefly(context: CanvasRenderingContext2D, firefly: ReturnType<typeof spawnFirefly>, delta: number) {
  firefly.age += delta;
  if (firefly.age >= firefly.lifetime) Object.assign(firefly, spawnFirefly());
  firefly.x += firefly.vx * delta;
  firefly.y += firefly.vy * delta;
  const distance = Math.hypot(firefly.x, firefly.y);
  if (distance > particleLimit) {
    const nx = firefly.x / distance;
    const ny = firefly.y / distance;
    firefly.x = nx * particleLimit;
    firefly.y = ny * particleLimit;
    const outward = firefly.vx * nx + firefly.vy * ny;
    if (outward > 0) {
      firefly.vx -= 2 * outward * nx;
      firefly.vy -= 2 * outward * ny;
    }
  }

  const { age, fadeIn, holdUntil, fadeUntil, radius, color } = firefly;
  if (age >= fadeUntil) return;
  let opacity = firefly.brightness;
  if (age < fadeIn) opacity *= ease(age / fadeIn);
  if (age > holdUntil) opacity *= 1 - ease((age - holdUntil) / (fadeUntil - holdUntil));
  const x = fieldCenter + firefly.x;
  const y = fieldCenter + firefly.y;
  context.shadowBlur = 5;
  context.shadowColor = `rgba(${color}, ${0.64 * opacity})`;
  context.fillStyle = `rgba(${color}, ${0.9 * opacity})`;
  context.beginPath();
  context.arc(x, y, radius, 0, fullCircle);
  context.fill();

  context.shadowBlur = 0;
  context.fillStyle = `rgba(245, 240, 255, ${0.55 * opacity})`;
  context.beginPath();
  context.arc(x, y, radius * 0.42, 0, fullCircle);
  context.fill();
}

export default function HeroEmblem({ ambient }: { ambient: boolean }) {
  const gradientId = useId();
  const flowLayer = useRef<SVGGElement>(null);
  const particleLayer = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = particleLayer.current;
    const flow = flowLayer.current;
    if (!ambient || !canvas || !flow) return;

    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const strokes = Array.from(flow.children);
    const context = canvas.getContext("2d");
    const offset = random(0, fullCircle);
    const fireflies = Array.from({ length: particleCount }, (_, index) =>
      spawnFirefly(offset + fullCircle * index / particleCount + random(-0.16, 0.16), true),
    );

    let frame: number | undefined;
    let lastDraw: number | undefined;
    let canvasScale = 0;
    let time = 0;
    const draw = (timestamp: number) => {
      frame = window.requestAnimationFrame(draw);
      if (lastDraw !== undefined && timestamp - lastDraw < 30) return;
      const delta = lastDraw === undefined ? 0 : Math.min(0.06, (timestamp - lastDraw) / 1000);
      lastDraw = timestamp;
      time = (time + delta) % colorCycleSeconds;

      outlineSegments.forEach(({ position }, index) => {
        strokes[index].setAttribute("stroke", outlineColor(position - time / colorCycleSeconds));
      });
      if (context) {
        const scale = Math.min(2, window.devicePixelRatio || 1);
        if (canvasScale !== scale) {
          canvasScale = scale;
          canvas.width = canvas.height = Math.round(fieldSize * scale);
          context.setTransform(scale, 0, 0, scale, 0, 0);
        }
        context.clearRect(0, 0, fieldSize, fieldSize);
        fireflies.forEach(firefly => paintFirefly(context, firefly, delta));
      }
    };

    function stop() {
      if (frame !== undefined) window.cancelAnimationFrame(frame);
      frame = undefined;
      lastDraw = undefined;
    }

    function updatePlayback() {
      if (document.hidden || motion.matches) stop();
      else if (frame === undefined) frame = window.requestAnimationFrame(draw);
    }

    updatePlayback();
    motion.addEventListener("change", updatePlayback);
    document.addEventListener("visibilitychange", updatePlayback);
    return () => {
      stop();
      motion.removeEventListener("change", updatePlayback);
      document.removeEventListener("visibilitychange", updatePlayback);
    };
  }, [ambient]);

  return <div className={`${styles.heroEmblem} ${ambient ? "" : styles.heroEmblemMuted}`} aria-hidden="true">
    {ambient && <canvas ref={particleLayer} className={styles.emblemParticles} style={{ width: fieldSize, height: fieldSize }} />}
    <svg className={styles.emblemSvg} viewBox="0 0 38 38" fill="none">
      <defs>
        <linearGradient id={gradientId} gradientUnits="userSpaceOnUse" x1="2" y1="13" x2="36" y2="26">
          <stop stopColor="#68DDEB" /><stop offset=".48" stopColor="#A89DFF" /><stop offset="1" stopColor="#F89AC3" />
        </linearGradient>
      </defs>
      <g className={styles.emblemStar}>
        <path d={outline} stroke={`url(#${gradientId})`} strokeWidth="1.5" strokeLinejoin="round" />
        {ambient && <g ref={flowLayer} className={styles.emblemFlow} strokeWidth="1.8" strokeLinecap="round">
          {outlineSegments.map(({ d, position }) => <path key={d} d={d} stroke={outlineColor(position)} />)}
        </g>}
      </g>
      <circle className={styles.emblemCore} cx="19" cy="19" r="3.1" fill="#B7A6FF" />
    </svg>
  </div>;
}
