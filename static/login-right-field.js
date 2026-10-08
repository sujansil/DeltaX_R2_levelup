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
  const compact = window.matchMedia("(max-width: 1050px)").matches;
  let renderer;
  let frameId = 0;

  try {
    renderer = new THREE.WebGLRenderer({
      canvas,
      alpha: true,
      antialias: !compact,
      powerPreference: "high-performance"
    });
  } catch (error) {
    return;
  }

  renderer.setClearColor(0x000000, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 80);
  camera.position.set(0, 0, 9.2);

  const composition = new THREE.Group();
  composition.position.set(compact ? 0.82 : 1.08, 0.05, -0.45);
  composition.scale.setScalar(compact ? 0.68 : 0.72);
  composition.rotation.set(-0.18, 0.08, 0.1);
  scene.add(composition);

  function createMobius(segments, widthSegments) {
    const positions = [];
    const indices = [];
    for (let i = 0; i <= segments; i += 1) {
      const u = (i / segments) * Math.PI * 2;
      for (let j = 0; j <= widthSegments; j += 1) {
        const v = (j / widthSegments - 0.5) * 1.45;
        const radius = 2.05 + v * Math.cos(u * 0.5);
        positions.push(
          radius * Math.cos(u),
          radius * Math.sin(u),
          v * Math.sin(u * 0.5) * 1.25
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

  const ribbonGeometry = createMobius(compact ? 72 : 118, compact ? 8 : 15);
  const ribbon = new THREE.Mesh(ribbonGeometry, new THREE.MeshStandardMaterial({
    color: 0x59645b,
    roughness: 0.86,
    metalness: 0.14,
    side: THREE.DoubleSide,
    transparent: true,
    opacity: 0.62,
    depthWrite: false
  }));
  ribbon.scale.set(1.08, 1.08, 1.08);
  composition.add(ribbon);

  const ribbonWire = new THREE.LineSegments(
    new THREE.WireframeGeometry(ribbonGeometry),
    new THREE.LineBasicMaterial({
      color: 0x909b91,
      transparent: true,
      opacity: 0.14,
      depthWrite: false
    })
  );
  ribbonWire.scale.copy(ribbon.scale).multiplyScalar(1.002);
  composition.add(ribbonWire);

  scene.add(new THREE.HemisphereLight(0xb9c2b8, 0x101713, 1.15));
  const keyLight = new THREE.DirectionalLight(0xb5c1b3, 2.3);
  keyLight.position.set(-3.5, 4.5, 6);
  scene.add(keyLight);
  const rimLight = new THREE.DirectionalLight(0x6c708f, 1.3);
  rimLight.position.set(4, -1.5, 2);
  scene.add(rimLight);

  const orbitGroup = new THREE.Group();
  composition.add(orbitGroup);
  const orbitCurves = [];
  const orbitCount = compact ? 4 : 6;

  function makeOrbit(index) {
    const points = [];
    const samples = compact ? 72 : 104;
    const phase = index * 0.71;
    const radiusX = 2.75 + (index % 3) * 0.19;
    const radiusY = 1.28 + (index % 4) * 0.18;
    for (let i = 0; i < samples; i += 1) {
      const angle = (i / samples) * Math.PI * 2;
      points.push(new THREE.Vector3(
        Math.cos(angle + phase * 0.08) * radiusX + Math.sin(angle * 3 + phase) * 0.13,
        Math.sin(angle) * radiusY,
        Math.sin(angle * (1.5 + (index % 2) * 0.5) + phase) * (0.9 + index * 0.035)
      ));
    }
    const curve = new THREE.CatmullRomCurve3(points, true, "catmullrom", 0.45);
    const geometry = new THREE.BufferGeometry().setFromPoints(curve.getPoints(compact ? 130 : 210));
    const line = new THREE.LineLoop(geometry, new THREE.LineBasicMaterial({
      color: index % 2 ? 0x89819f : 0x708998,
      transparent: true,
      opacity: compact ? 0.32 : 0.43,
      depthWrite: false
    }));
    line.rotation.set(
      -0.72 + index * 0.17,
      phase * 0.24,
      -0.42 + index * 0.13
    );
    orbitGroup.add(line);
    orbitCurves.push({ curve, line });
  }

  for (let i = 0; i < orbitCount; i += 1) makeOrbit(i);

  const tracerCount = compact ? 10 : 20;
  const tracerPositions = new Float32Array(tracerCount * 3);
  const tracerGeometry = new THREE.BufferGeometry();
  const tracerAttribute = new THREE.BufferAttribute(tracerPositions, 3);
  tracerAttribute.setUsage(THREE.DynamicDrawUsage);
  tracerGeometry.setAttribute("position", tracerAttribute);
  const tracers = new THREE.Points(tracerGeometry, new THREE.PointsMaterial({
    color: 0xc0bad4,
    size: compact ? 0.042 : 0.056,
    transparent: true,
    opacity: 0.88,
    sizeAttenuation: true,
    depthWrite: false
  }));
  orbitGroup.add(tracers);

  const depthParticleCount = compact ? 38 : 74;
  const depthPositions = new Float32Array(depthParticleCount * 3);
  for (let i = 0; i < depthParticleCount; i += 1) {
    const radius = 2.5 + Math.random() * 2.8;
    const angle = Math.random() * Math.PI * 2;
    depthPositions[i * 3] = Math.cos(angle) * radius;
    depthPositions[i * 3 + 1] = (Math.random() - 0.5) * 5.7;
    depthPositions[i * 3 + 2] = Math.sin(angle) * radius - 0.9;
  }
  const depthGeometry = new THREE.BufferGeometry();
  depthGeometry.setAttribute("position", new THREE.BufferAttribute(depthPositions, 3));
  const depthParticles = new THREE.Points(depthGeometry, new THREE.PointsMaterial({
    color: 0xa9b1c3,
    size: compact ? 0.029 : 0.038,
    transparent: true,
    opacity: 0.5,
    sizeAttenuation: true,
    depthWrite: false
  }));
  scene.add(depthParticles);

  const pointerTarget = { x: 0, y: 0 };
  const pointerCurrent = { x: 0, y: 0 };

  function updatePointer(event) {
    if (reducedMotion.matches || event.isPrimary === false) return;
    const bounds = canvas.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right
      || event.clientY < bounds.top || event.clientY > bounds.bottom) return;
    pointerTarget.x = THREE.MathUtils.clamp(((event.clientX - bounds.left) / bounds.width) * 2 - 1, -1, 1);
    pointerTarget.y = THREE.MathUtils.clamp(1 - ((event.clientY - bounds.top) / bounds.height) * 2, -1, 1);
  }

  function resetPointer() {
    pointerTarget.x = 0;
    pointerTarget.y = 0;
  }

  window.addEventListener("pointermove", updatePointer, { passive: true });
  window.addEventListener("blur", resetPointer);
  document.documentElement.addEventListener("pointerleave", resetPointer, { passive: true });

  const tracerPoint = new THREE.Vector3();
  const clock = new THREE.Clock();

  function render() {
    frameId = 0;
    const elapsed = clock.getElapsedTime();
    const motionScale = reducedMotion.matches ? 0 : 1;
    pointerCurrent.x = THREE.MathUtils.lerp(pointerCurrent.x, pointerTarget.x, 0.045);
    pointerCurrent.y = THREE.MathUtils.lerp(pointerCurrent.y, pointerTarget.y, 0.045);

    composition.rotation.x = -0.18 + Math.sin(elapsed * 0.13) * 0.035 * motionScale + pointerCurrent.y * 0.1 * motionScale;
    composition.rotation.y = 0.08 + elapsed * 0.035 * motionScale + pointerCurrent.x * 0.14 * motionScale;
    composition.rotation.z = 0.1 + Math.cos(elapsed * 0.11) * 0.03 * motionScale;
    orbitGroup.rotation.x = elapsed * 0.013 * motionScale - pointerCurrent.y * 0.045 * motionScale;
    orbitGroup.rotation.y = -elapsed * 0.017 * motionScale + pointerCurrent.x * 0.065 * motionScale;
    depthParticles.rotation.y = elapsed * 0.005 * motionScale;
    depthParticles.position.x = pointerCurrent.x * 0.16 * motionScale;
    depthParticles.position.y = pointerCurrent.y * 0.1 * motionScale;
    camera.position.x = pointerCurrent.x * 0.18 * motionScale;
    camera.position.y = pointerCurrent.y * 0.12 * motionScale;
    camera.lookAt(pointerCurrent.x * 0.035 * motionScale, pointerCurrent.y * 0.025 * motionScale, -0.4);

    for (let i = 0; i < tracerCount; i += 1) {
      const orbit = orbitCurves[i % orbitCurves.length];
      const progress = (elapsed * (0.025 + (i % 5) * 0.003) + i / tracerCount) % 1;
      orbit.curve.getPointAt(progress, tracerPoint);
      tracerPoint.applyEuler(orbit.line.rotation);
      const index = i * 3;
      tracerPositions[index] = tracerPoint.x;
      tracerPositions[index + 1] = tracerPoint.y;
      tracerPositions[index + 2] = tracerPoint.z;
    }
    tracerAttribute.needsUpdate = true;

    renderer.render(scene, camera);
    if (!document.hidden && !reducedMotion.matches) frameId = requestAnimationFrame(render);
  }

  function requestRender() {
    if (!frameId && !document.hidden) frameId = requestAnimationFrame(render);
  }

  function resize() {
    const bounds = canvas.getBoundingClientRect();
    camera.aspect = bounds.width / bounds.height;
    camera.updateProjectionMatrix();
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, compact ? 1.25 : 1.5));
    renderer.setSize(bounds.width, bounds.height, false);
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

  resize();
  document.documentElement.classList.add("right-field-ready");
  requestRender();
}());
