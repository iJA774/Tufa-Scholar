"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import styles from "./LaunchPage.module.css";

type Dot = { x: number; y: number; color: number; variation: number; delay: number };

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));

function smooth(start: number, end: number, value: number) {
  const t = clamp((value - start) / (end - start), 0, 1);
  return t * t * (3 - 2 * t);
}

function noise(x: number, y: number) {
  const value = Math.sin(x * 127.1 + y * 311.7) * 43758.5453123;
  return value - Math.floor(value);
}

function hueAt(position: number) {
  const stops = [185, 226, 280, 327, 390];
  const scaled = clamp(position, 0, 0.99999) * 4;
  const index = Math.floor(scaled);
  return (stops[index] + (stops[index + 1] - stops[index]) * (scaled - index)) % 360;
}

function makeSprite(hue: number) {
  const sprite = document.createElement("canvas");
  sprite.width = sprite.height = 80;
  const context = sprite.getContext("2d");
  if (!context) return null;

  const glow = context.createRadialGradient(40, 40, 0, 40, 40, 39);
  glow.addColorStop(0, `hsla(${hue}, 100%, 94%, 1)`);
  glow.addColorStop(0.105, `hsla(${hue}, 100%, 75%, .98)`);
  glow.addColorStop(0.175, `hsla(${hue}, 100%, 62%, .88)`);
  glow.addColorStop(0.245, `hsla(${hue}, 100%, 57%, .48)`);
  glow.addColorStop(0.42, `hsla(${hue}, 100%, 55%, .17)`);
  glow.addColorStop(0.68, `hsla(${hue}, 100%, 48%, .035)`);
  glow.addColorStop(1, `hsla(${hue}, 100%, 48%, 0)`);
  context.fillStyle = glow;
  context.fillRect(0, 0, 80, 80);
  context.beginPath();
  context.arc(40, 40, 6.2, 0, Math.PI * 2);
  context.fillStyle = `hsla(${hue}, 100%, 69%, .96)`;
  context.fill();
  context.beginPath();
  context.arc(39.3, 39.2, 2.5, 0, Math.PI * 2);
  context.fillStyle = "rgba(252, 251, 255, .87)";
  context.fill();
  return sprite;
}

function useLedLogo(canvasRef: React.RefObject<HTMLCanvasElement | null>, onUnavailable: () => void) {
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const context = canvas.getContext("2d", { alpha: true });
    if (!context) {
      onUnavailable();
      return;
    }

    const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    let reduceMotion = motionQuery.matches;
    let isVisible = !document.hidden;
    let points: Dot[] = [];
    let sprites: HTMLCanvasElement[] = [];
    let baseLayer: HTMLCanvasElement | null = null;
    let width = 0;
    let height = 0;
    let gap = 10;
    let start = performance.now();
    let animationFrame = 0;
    let resizeFrame = 0;
    let failed = false;
    let observer: ResizeObserver | undefined;
    const LIGHT_UP_MS = 3850;
    const HUES = 42;

    const fail = () => {
      if (failed) return;
      failed = true;
      cancelAnimationFrame(animationFrame);
      cancelAnimationFrame(resizeFrame);
      onUnavailable();
    };

    const render = (now: number) => {
      if (failed) return;
      context.clearRect(0, 0, width, height);
      if (baseLayer) context.drawImage(baseLayer, 0, 0, width, height);
      const elapsed = now - start;
      const overall = reduceMotion ? 1 : elapsed / LIGHT_UP_MS;
      const spriteSize = gap * 3.9;
      const half = spriteSize / 2;
      const breathing = reduceMotion ? 1 : 0.84 + 0.16 * Math.sin(elapsed * 0.00142 - 0.2);

      for (const point of points) {
        const progress = (point.x / width) * 0.98 + point.delay;
        const ignition = reduceMotion ? 1 : smooth(progress - 0.075, progress + 0.09, overall);
        if (ignition <= 0.006) continue;
        const sparkle = reduceMotion ? 1 : 0.96 + 0.04 * Math.sin(elapsed * 0.002 + point.variation * 22);
        context.globalAlpha = ignition * breathing * sparkle;
        context.drawImage(sprites[point.color], point.x - half, point.y - half, spriteSize, spriteSize);
      }
      context.globalAlpha = 1;
      if (!reduceMotion && isVisible) animationFrame = requestAnimationFrame(render);
    };

    const rebuild = () => {
      if (failed) return;
      const bounds = canvas.getBoundingClientRect();
      width = Math.max(1, bounds.width);
      height = Math.max(1, bounds.height);
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      gap = clamp(width / 110, 6.7, 11.5);

      const mask = document.createElement("canvas");
      mask.width = Math.ceil(width);
      mask.height = Math.ceil(height);
      const maskContext = mask.getContext("2d", { willReadFrequently: true });
      if (!maskContext) {
        fail();
        return;
      }

      const fontSize = height * 0.88;
      maskContext.font = `900 ${fontSize}px "Arial Black", "DejaVu Sans", sans-serif`;
      maskContext.textAlign = "center";
      maskContext.textBaseline = "middle";
      const measure = maskContext.measureText("TUFA").width;
      const xScale = (width * 0.91) / measure;
      maskContext.save();
      maskContext.translate(width / 2, height / 2 + fontSize * 0.039);
      maskContext.scale(xScale, 1);
      maskContext.fillStyle = "#fff";
      maskContext.fillText("TUFA", 0, 0);
      maskContext.restore();

      let pixels: Uint8ClampedArray;
      try {
        pixels = maskContext.getImageData(0, 0, mask.width, mask.height).data;
      } catch {
        fail();
        return;
      }

      points = [];
      let xIndex = 0;
      for (let x = gap / 2; x < width - gap / 2; x += gap) {
        let yIndex = 0;
        for (let y = gap / 2; y < height - gap / 2; y += gap) {
          const offset = (Math.floor(y) * mask.width + Math.floor(x)) * 4 + 3;
          if (pixels[offset] > 140) {
            const variation = noise(xIndex, yIndex);
            const colorPosition = clamp(x / width + (variation - 0.5) * 0.032, 0, 1);
            points.push({
              x,
              y,
              color: Math.min(HUES - 1, Math.floor(colorPosition * HUES)),
              variation,
              delay: (variation - 0.5) * 0.1,
            });
          }
          yIndex++;
        }
        xIndex++;
      }

      baseLayer = document.createElement("canvas");
      baseLayer.width = canvas.width;
      baseLayer.height = canvas.height;
      const baseContext = baseLayer.getContext("2d");
      if (!baseContext) {
        fail();
        return;
      }
      baseContext.setTransform(dpr, 0, 0, dpr, 0, 0);
      for (const point of points) {
        baseContext.beginPath();
        baseContext.arc(point.x, point.y, gap * 0.29, 0, Math.PI * 2);
        baseContext.fillStyle = "rgba(45, 44, 60, .47)";
        baseContext.fill();
        baseContext.beginPath();
        baseContext.arc(point.x - gap * 0.05, point.y - gap * 0.065, gap * 0.13, 0, Math.PI * 2);
        baseContext.fillStyle = "rgba(102, 99, 131, .12)";
        baseContext.fill();
      }

      if (!sprites.length) {
        const generated = Array.from({ length: HUES }, (_, index) => makeSprite(hueAt(index / (HUES - 1))));
        if (generated.some(sprite => sprite === null)) {
          fail();
          return;
        }
        sprites = generated as HTMLCanvasElement[];
      }
      if (reduceMotion) render(performance.now());
    };

    const scheduleResize = () => {
      if (!isVisible) return;
      cancelAnimationFrame(resizeFrame);
      resizeFrame = requestAnimationFrame(() => {
        resizeFrame = 0;
        rebuild();
      });
    };

    const onVisibilityChange = () => {
      isVisible = !document.hidden;
      if (!isVisible) {
        cancelAnimationFrame(animationFrame);
        cancelAnimationFrame(resizeFrame);
        animationFrame = 0;
        resizeFrame = 0;
        return;
      }
      if (reduceMotion) render(performance.now());
      else animationFrame = requestAnimationFrame(render);
    };

    const onMotionChange = (event: MediaQueryListEvent) => {
      reduceMotion = event.matches;
      cancelAnimationFrame(animationFrame);
      animationFrame = 0;
      if (!isVisible) return;
      if (reduceMotion) render(performance.now());
      else {
        start = performance.now();
        animationFrame = requestAnimationFrame(render);
      }
    };

    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(scheduleResize);
      observer.observe(canvas);
    } else {
      window.addEventListener("resize", scheduleResize, { passive: true });
    }
    document.addEventListener("visibilitychange", onVisibilityChange);
    motionQuery.addEventListener("change", onMotionChange);
    rebuild();
    if (!reduceMotion && isVisible && !failed) animationFrame = requestAnimationFrame(render);

    return () => {
      cancelAnimationFrame(animationFrame);
      cancelAnimationFrame(resizeFrame);
      observer?.disconnect();
      window.removeEventListener("resize", scheduleResize);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      motionQuery.removeEventListener("change", onMotionChange);
    };
  }, [canvasRef, onUnavailable]);
}

export default function LaunchPage() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [canvasUnavailable, setCanvasUnavailable] = useState(false);
  const markCanvasUnavailable = useCallback(() => setCanvasUnavailable(true), []);
  useLedLogo(canvasRef, markCanvasUnavailable);

  return (
    <div className={styles.stage}>
      <header className={styles.topbar}>
        <div className={styles.brand} aria-label="TUFA — Traceable Unified Fact-finding Agent">
          <span className={styles.brandMonogram} aria-hidden="true"><span>T</span><span>U</span><span>F</span><span>A</span></span>
          <span className={styles.brandRule} aria-hidden="true" />
          <span className={styles.brandName}>Traceable Unified<br />Fact-finding Agent</span>
        </div>
      </header>

      <main className={styles.content}>
        <section className={styles.hero} aria-labelledby="hero-title">
          <h1 className={styles.claim} id="hero-title">学术，触手可及</h1>
          <div className={styles.logoArea} role="img" aria-label="由彩色 LED 灯珠组成的 TUFA 字样，从左向右点亮并缓慢呼吸">
            <canvas ref={canvasRef} className={styles.ledLogo} aria-hidden="true" />
            {canvasUnavailable && <span className={styles.logoFallback} aria-hidden="true">TUFA</span>}
          </div>
          <div className={styles.actions}>
            <Link className={`${styles.action} ${styles.actionPrimary}`} href="/workspace">
              开始使用<span className={styles.arrow} aria-hidden="true">↗</span>
            </Link>
            <a className={`${styles.action} ${styles.actionSecondary}`} href="https://github.com/iJA774/Tufa-Scholar" target="_blank" rel="noopener noreferrer">
              <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.65 7.65 0 0 1 2-.27c.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8Z" /></svg>
              GitHub
            </a>
          </div>
        </section>
      </main>

    </div>
  );
}
