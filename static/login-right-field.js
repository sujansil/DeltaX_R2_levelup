(async function () {
  "use strict";

  const canvas = document.getElementById("right-field-canvas");
  const narrowScreen = window.matchMedia("(max-width: 820px)");
  if (!canvas || narrowScreen.matches) return;

  let THREE;
  try {
    THREE = await import("/static/vendor/three.module.min.js");
  } catch (error) {
    return;
  }

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  let renderer;
  let frameId = 0;

  try {
    renderer = new THREE.WebGLRenderer({
      canvas,
      alpha: true,
      antialias: true,
      powerPreference: "high-performance"
    });
  } catch (error) {
    return;
  }

  renderer.setClearColor(0x000000, 0);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  renderer.setSize(window.innerWidth, window.innerHeight, false);
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(42, window.innerWidth / window.innerHeight, 0.1, 100);
  camera.position.set(0, 0.15, 8.4);

  const structure = new THREE.Group();
  structure.position.set(2.25, 0.1, -0.5);
  structure.rotation.set(-0.22, 0.12, 0.16);
  scene.add(structure);

  function createMobius(segments, widthSegments) {
    const positions = [];
    const indices = [];
    for (let i = 0; i <= segments; i += 1) {
      const u = (i / segments) * Math.PI * 2;
      for (let j = 0; j <= widthSegments; j += 1) {
        const v = (j / widthSegments - 0.5) * 1.25;
        const radius = 2.05 + v * Math.cos(u * 0.5);
        positions.push(
          radius * Math.cos(u),
          radius * Math.sin(u),
          v * Math.sin(u * 0.5) * 1.35
        );
      }
    }
    for (let i = 0; i < segments; i += 1) {
      for (let j = 0; j < widthSegments; j += 1) {
        const a = i * (widthSegments + 1) + j;
        const b = a + widthSegments + 1;
        indices.push(a, b, a + 1, b, b + 1, a + 1);
      }
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    geometry.setIndex(indices);
    geometry.computeVertexNormals();
    return geometry;
  }

  const ribbon = new THREE.Mesh(
    createMobius(112, 14),
    new THREE.MeshStandardMaterial({
      color: 0x697064,
      roughness: 0.72,
      metalness: 0.28,
      side: THREE.DoubleSide,
      transparent: true,
      opacity: 0.78
    })
  );
  structure.add(ribbon);

  const wire = new THREE.LineSegments(
    new THREE.WireframeGeometry(ribbon.geometry),
    new THREE.LineBasicMaterial({ color: 0xc4c8bc, transparent: true, opacity: 0.075 })
  );
  wire.scale.setScalar(1.002);
  structure.add(wire);

  scene.add(new THREE.HemisphereLight(0xdadbd2, 0x171b17, 1.05));
  const keyLight = new THREE.DirectionalLight(0xc3ccb8, 2.2);
  keyLight.position.set(3, 5, 6);
  scene.add(keyLight);
  const rimLight = new THREE.DirectionalLight(0x687365, 1.1);
  rimLight.position.set(-4, -2, 1);
  scene.add(rimLight);

  const trajectoryGroup = new THREE.Group();
  structure.add(trajectoryGroup);
  const trajectoryCount = 6;
  for (let pathIndex = 0; pathIndex < trajectoryCount; pathIndex += 1) {
    const points = [];
    const phase = pathIndex * 0.93;
    const samples = 150;
    for (let i = 0; i < samples; i += 1) {
      const t = (i / (samples - 1)) * Math.PI * 3.55;
      const radius = 2.65 + 0.22 * Math.sin(t * 2.3 + phase);
      points.push(new THREE.Vector3(
        Math.cos(t + phase * 0.18) * radius,
        Math.sin(t * 1.04 + phase) * (1.36 + pathIndex * 0.055),
        Math.sin(t * 1.71 + phase) * 1.42
      ));
    }
    const curve = new THREE.CatmullRomCurve3(points);
    const geometry = new THREE.BufferGeometry().setFromPoints(curve.getPoints(210));
    const line = new THREE.Line(geometry, new THREE.LineBasicMaterial({
      color: pathIndex % 2 ? 0x7f8b78 : 0xa7aaa1,
      transparent: true,
      opacity: 0.18
    }));
    line.rotation.set(phase * 0.09, phase * 0.13, phase * 0.05);
    trajectoryGroup.add(line);
  }

  const particleCount = 180;
  const particlePositions = new Float32Array(particleCount * 3);
  for (let i = 0; i < particleCount; i += 1) {
    const radius = 3.2 + Math.random() * 5.8;
    const angle = Math.random() * Math.PI * 2;
    particlePositions[i * 3] = Math.cos(angle) * radius + 1.2;
    particlePositions[i * 3 + 1] = (Math.random() - 0.5) * 7;
    particlePositions[i * 3 + 2] = Math.sin(angle) * radius - 1.5;
  }
  const particlesGeometry = new THREE.BufferGeometry();
  particlesGeometry.setAttribute("position", new THREE.BufferAttribute(particlePositions, 3));
  const particles = new THREE.Points(particlesGeometry, new THREE.PointsMaterial({
    color: 0xbfc4b8,
    size: 0.032,
    transparent: true,
    opacity: 0.32,
    sizeAttenuation: true
  }));
  scene.add(particles);

  const loginCard = document.querySelector(".login-card");
  const pointerTarget = { x: 0, y: 0 };
  const pointerCurrent = { x: 0, y: 0 };
  let lastPointerMove = 0;

  function resetPointer() {
    pointerTarget.x = 0;
    pointerTarget.y = 0;
  }

  function updatePointer(event) {
    if (reducedMotion.matches || event.isPrimary === false) return;
    pointerTarget.x = THREE.MathUtils.clamp((event.clientX / window.innerWidth) * 2 - 1, -1, 1);
    pointerTarget.y = THREE.MathUtils.clamp(1 - (event.clientY / window.innerHeight) * 2, -1, 1);
    lastPointerMove = performance.now();
  }

  window.addEventListener("pointermove", updatePointer, { passive: true });
  window.addEventListener("pointercancel", resetPointer, { passive: true });
  window.addEventListener("blur", resetPointer);
  document.documentElement.addEventListener("pointerleave", resetPointer, { passive: true });

  const clock = new THREE.Clock();
  function render() {
    frameId = 0;
    const elapsed = clock.getElapsedTime();
    const motionScale = reducedMotion.matches ? 0 : 1;

    if (lastPointerMove && performance.now() - lastPointerMove > 900) resetPointer();
    pointerCurrent.x = THREE.MathUtils.lerp(pointerCurrent.x, pointerTarget.x, 0.04);
    pointerCurrent.y = THREE.MathUtils.lerp(pointerCurrent.y, pointerTarget.y, 0.04);

    structure.rotation.x = -0.22 + pointerCurrent.y * 0.045 * motionScale;
    structure.rotation.y = 0.12 + elapsed * 0.045 * motionScale + pointerCurrent.x * 0.075 * motionScale;
    structure.rotation.z = 0.16 + Math.sin(elapsed * 0.16) * 0.035 * motionScale - pointerCurrent.x * 0.018 * motionScale;

    trajectoryGroup.rotation.x = elapsed * 0.016 * motionScale + pointerCurrent.y * 0.018 * motionScale;
    trajectoryGroup.rotation.y = pointerCurrent.x * 0.022 * motionScale;
    trajectoryGroup.rotation.z = -elapsed * 0.011 * motionScale - pointerCurrent.x * 0.012 * motionScale;

    particles.rotation.y = elapsed * 0.004 * motionScale;
    particles.position.x = pointerCurrent.x * 0.32 * motionScale;
    particles.position.y = pointerCurrent.y * 0.22 * motionScale;

    camera.position.x = pointerCurrent.x * 0.2 * motionScale;
    camera.position.y = 0.15 + pointerCurrent.y * 0.13 * motionScale;
    camera.lookAt(pointerCurrent.x * 0.055 * motionScale, pointerCurrent.y * 0.035 * motionScale, 0);

    if (loginCard) {
      loginCard.style.setProperty("--card-tilt-x", `${(-pointerCurrent.y * 0.65 * motionScale).toFixed(3)}deg`);
      loginCard.style.setProperty("--card-tilt-y", `${(pointerCurrent.x * 0.8 * motionScale).toFixed(3)}deg`);
    }

    renderer.render(scene, camera);
    if (!document.hidden && !reducedMotion.matches) frameId = requestAnimationFrame(render);
  }

  function requestRender() {
    if (!frameId && !document.hidden) frameId = requestAnimationFrame(render);
  }

  function resize() {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    renderer.setSize(window.innerWidth, window.innerHeight, false);
    requestRender();
  }

  window.addEventListener("resize", resize, { passive: true });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden && frameId) {
      cancelAnimationFrame(frameId);
      frameId = 0;
    } else {
      clock.getDelta();
      requestRender();
    }
  });
  reducedMotion.addEventListener("change", () => {
    if (reducedMotion.matches) resetPointer();
    requestRender();
  });

  document.documentElement.classList.add("right-field-ready");
  requestRender();
}());
