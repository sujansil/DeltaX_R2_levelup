(async function () {
  "use strict";

  const canvas = document.getElementById("three-canvas");
  if (!canvas) return;
  const visualPane = canvas.parentElement;

  let THREE;
  try {
    THREE = await import("/static/vendor/three.module.min.js");
  } catch (error) {
    return;
  }

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const mobile = window.matchMedia("(max-width: 820px)").matches;
  const sampleCount = mobile ? 150 : 300;
  const deltaValue = document.getElementById("delta-value");
  const separationValue = document.getElementById("separation-value");
  let renderer;
  let frameId = 0;

  try {
    renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: !mobile, powerPreference: "high-performance" });
  } catch (error) {
    return;
  }

  const initialBounds = visualPane.getBoundingClientRect();
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, mobile ? 1.25 : 1.75));
  renderer.setSize(initialBounds.width, initialBounds.height, false);
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(42, initialBounds.width / initialBounds.height, 0.1, 100);
  camera.position.set(0, 0, 10);

  const graph = new THREE.Group();
  const graphHome = new THREE.Vector3(0, mobile ? -0.08 : -0.05, 0);
  graph.position.copy(graphHome);
  graph.scale.setScalar(mobile ? 0.72 : 1.2);
  scene.add(graph);

  function createMobius(segments, widthSegments) {
    const positions = [];
    const indices = [];
    for (let i = 0; i <= segments; i += 1) {
      const u = (i / segments) * Math.PI * 2;
      for (let j = 0; j <= widthSegments; j += 1) {
        const v = (j / widthSegments - 0.5) * 0.88;
        const radius = 1.55 + v * Math.cos(u * 0.5);
        positions.push(radius * Math.cos(u), radius * Math.sin(u), v * Math.sin(u * 0.5));
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

  const ambientStructure = new THREE.Group();
  ambientStructure.position.set(mobile ? 0.8 : 0.65, mobile ? 0.1 : -0.05, -2.35);
  ambientStructure.scale.setScalar(mobile ? 1.05 : 1.55);
  const ribbon = new THREE.Mesh(
    createMobius(mobile ? 40 : 82, mobile ? 5 : 9),
    new THREE.MeshStandardMaterial({
      color: 0x5f6378,
      roughness: 0.82,
      metalness: 0.18,
      side: THREE.DoubleSide,
      transparent: true,
      opacity: mobile ? 0.07 : 0.13,
      depthWrite: false
    })
  );
  ambientStructure.add(ribbon);
  const ribbonWire = new THREE.LineSegments(
    new THREE.WireframeGeometry(ribbon.geometry),
    new THREE.LineBasicMaterial({ color: 0x858ba5, transparent: true, opacity: mobile ? 0.025 : 0.05, depthWrite: false })
  );
  ribbonWire.scale.setScalar(1.002);
  ambientStructure.add(ribbonWire);
  scene.add(ambientStructure);

  scene.add(new THREE.HemisphereLight(0xaeb6d0, 0x111713, 0.72));
  const ambientKey = new THREE.DirectionalLight(0xb5b9d4, 1.25);
  ambientKey.position.set(-3, 4, 5);
  scene.add(ambientKey);

  function makeGrid() {
    const positions = [];
    const xExtent = 3.15;
    const yExtent = 2.45;
    for (let i = -6; i <= 6; i += 1) {
      const x = i * 0.5;
      positions.push(x, -yExtent, -1.15, x, yExtent, -1.15);
    }
    for (let i = -5; i <= 5; i += 1) {
      const y = i * 0.48;
      positions.push(-xExtent, y, -1.15, xExtent, y, -1.15);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    return new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({
      color: 0x687065, transparent: true, opacity: mobile ? 0.08 : 0.13
    }));
  }

  const grid = makeGrid();
  graph.add(grid);

  const axesGeometry = new THREE.BufferGeometry();
  axesGeometry.setAttribute("position", new THREE.Float32BufferAttribute([
    -3.35, 0, -1.12, 3.35, 0, -1.12,
    0, -2.6, -1.12, 0, 2.6, -1.12
  ], 3));
  graph.add(new THREE.LineSegments(axesGeometry, new THREE.LineBasicMaterial({
    color: 0x8b9388, transparent: true, opacity: mobile ? 0.13 : 0.22
  })));

  const primaryPositions = new Float32Array(sampleCount * 3);
  const perturbedPositions = new Float32Array(sampleCount * 3);

  function makeTrajectory(positions, color, opacity) {
    const geometry = new THREE.BufferGeometry();
    const attribute = new THREE.BufferAttribute(positions, 3);
    attribute.setUsage(THREE.DynamicDrawUsage);
    geometry.setAttribute("position", attribute);
    return new THREE.Line(geometry, new THREE.LineBasicMaterial({ color, transparent: true, opacity }));
  }

  const primaryLine = makeTrajectory(primaryPositions, 0x303C50, mobile ? 0.6 : 0.82);
  const perturbedLine = makeTrajectory(perturbedPositions, 0x413557, mobile ? 0.58 : 0.84);
  primaryLine.position.z = 0.28;
  perturbedLine.position.z = 0.1;
  graph.add(primaryLine, perturbedLine);

  const tracerCount = mobile ? 4 : 7;
  const primaryTracerPositions = new Float32Array(tracerCount * 3);
  const perturbedTracerPositions = new Float32Array(tracerCount * 3);

  function makeTracers(positions, color) {
    const geometry = new THREE.BufferGeometry();
    const attribute = new THREE.BufferAttribute(positions, 3);
    attribute.setUsage(THREE.DynamicDrawUsage);
    geometry.setAttribute("position", attribute);
    return new THREE.Points(geometry, new THREE.PointsMaterial({
      color, size: mobile ? 0.055 : 0.07, transparent: true, opacity: 0.92, sizeAttenuation: true
    }));
  }

  const primaryTracers = makeTracers(primaryTracerPositions, 0x445164);
  const perturbedTracers = makeTracers(perturbedTracerPositions, 0x584d6b);
  primaryTracers.position.z = 0.32;
  perturbedTracers.position.z = 0.14;
  graph.add(primaryTracers, perturbedTracers);

  const fieldParticleCount = mobile ? 34 : 95;
  const fieldPositions = new Float32Array(fieldParticleCount * 3);
  for (let i = 0; i < fieldParticleCount; i += 1) {
    fieldPositions[i * 3] = (Math.random() - 0.5) * 6.6;
    fieldPositions[i * 3 + 1] = (Math.random() - 0.5) * 5.1;
    fieldPositions[i * 3 + 2] = -0.75 + Math.random() * 1.8;
  }
  const fieldGeometry = new THREE.BufferGeometry();
  fieldGeometry.setAttribute("position", new THREE.BufferAttribute(fieldPositions, 3));
  const fieldParticles = new THREE.Points(fieldGeometry, new THREE.PointsMaterial({
    color: 0x9aa9bd, size: mobile ? 0.022 : 0.03, transparent: true, opacity: 0.32, sizeAttenuation: true
  }));
  graph.add(fieldParticles);

  const influenceSegments = 48;
  const influencePositions = new Float32Array((influenceSegments + 1) * 3);
  const influenceGeometry = new THREE.BufferGeometry();
  influenceGeometry.setAttribute("position", new THREE.BufferAttribute(influencePositions, 3));
  const influenceMaterial = new THREE.LineBasicMaterial({ color: 0xb3a4dc, transparent: true, opacity: 0.28 });
  const influenceRing = new THREE.LineLoop(influenceGeometry, influenceMaterial);
  graph.add(influenceRing);

  const pointerTarget = { x: 0, y: 0 };
  const pointerCurrent = { x: 0, y: 0 };
  let pointerInside = false;

  function resetPointer() {
    pointerInside = false;
    pointerTarget.x = 0;
    pointerTarget.y = 0;
  }

  function updatePointer(event) {
    if (reducedMotion.matches || event.isPrimary === false) return;
    const paneBounds = visualPane.getBoundingClientRect();
    pointerInside = event.clientX >= paneBounds.left && event.clientX <= paneBounds.right
      && event.clientY >= paneBounds.top && event.clientY <= paneBounds.bottom;
    const mobileStrength = mobile ? 0.58 : 1;
    pointerTarget.x = THREE.MathUtils.clamp((event.clientX / window.innerWidth) * 2 - 1, -1, 1) * mobileStrength;
    pointerTarget.y = THREE.MathUtils.clamp(1 - (event.clientY / window.innerHeight) * 2, -1, 1) * mobileStrength;
  }

  window.addEventListener("pointermove", updatePointer, { passive: true });
  window.addEventListener("pointercancel", resetPointer, { passive: true });
  window.addEventListener("blur", resetPointer);
  document.documentElement.addEventListener("pointerleave", resetPointer, { passive: true });

  function applyCursorField(array, index, strength, cursorX, cursorY) {
    const dx = array[index] - cursorX;
    const dy = array[index + 1] - cursorY;
    const distance = Math.sqrt(dx * dx + dy * dy);
    const radius = 1.15;
    if (distance > 0.001 && distance < radius) {
      const force = (1 - distance / radius) ** 2 * strength;
      array[index] += (dx / distance) * force;
      array[index + 1] += (dy / distance) * force;
    }
  }

  function updateLorenz(array, initialOffset, rho, phase, cursorX, cursorY, influence) {
    let x = 0.1 + initialOffset;
    let y = phase;
    let z = 0;
    const dt = 0.012;
    for (let i = 0; i < sampleCount; i += 1) {
      const dx = 10 * (y - x);
      const dy = x * (rho - z) - y;
      const dz = x * y - (8 / 3) * z;
      x += dx * dt;
      y += dy * dt;
      z += dz * dt;
      const index = i * 3;
      array[index] = x * 0.105;
      array[index + 1] = (z - 22) * 0.09;
      array[index + 2] = y * 0.052;
      applyCursorField(array, index, influence, cursorX, cursorY);
    }
  }

  function copyTracerPositions(source, destination, elapsed, direction) {
    const travel = Math.floor(elapsed * (mobile ? 13 : 18));
    for (let i = 0; i < tracerCount; i += 1) {
      const sourcePoint = (travel * direction + i * Math.floor(sampleCount / tracerCount) + sampleCount * 10) % sampleCount;
      const sourceIndex = sourcePoint * 3;
      const targetIndex = i * 3;
      destination[targetIndex] = source[sourceIndex];
      destination[targetIndex + 1] = source[sourceIndex + 1];
      destination[targetIndex + 2] = source[sourceIndex + 2];
    }
  }

  function updateInfluenceRing(cursorX, cursorY, elapsed) {
    const radius = 0.56 + Math.sin(elapsed * 2) * 0.035;
    for (let i = 0; i <= influenceSegments; i += 1) {
      const angle = (i / influenceSegments) * Math.PI * 2;
      const index = i * 3;
      influencePositions[index] = cursorX + Math.cos(angle) * radius;
      influencePositions[index + 1] = cursorY + Math.sin(angle) * radius;
      influencePositions[index + 2] = 0.52;
    }
    influenceGeometry.attributes.position.needsUpdate = true;
    influenceMaterial.opacity = pointerInside ? 0.34 : 0.12;
  }

  const clock = new THREE.Clock();
  function render() {
    frameId = 0;
    const elapsed = clock.getElapsedTime();
    const motionScale = reducedMotion.matches ? 0 : 1;
    const damping = mobile ? 0.065 : 0.055;
    pointerCurrent.x = THREE.MathUtils.lerp(pointerCurrent.x, pointerTarget.x, damping);
    pointerCurrent.y = THREE.MathUtils.lerp(pointerCurrent.y, pointerTarget.y, damping);

    const horizontal = THREE.MathUtils.clamp((pointerCurrent.x + 1) * 0.5, 0, 1);
    const perturbation = 0.0012 + horizontal * horizontal * 0.078;
    const rho = 28 + pointerCurrent.y * 3.6 + Math.sin(elapsed * 0.16) * 0.32 * motionScale;
    const phase = Math.sin(elapsed * 0.21) * 0.035 * motionScale;
    const cursorX = pointerCurrent.x * 2.65;
    const cursorY = pointerCurrent.y * 2.05;

    updateLorenz(primaryPositions, 0, rho, phase, cursorX, cursorY, pointerInside ? 0.16 : 0.035);
    updateLorenz(perturbedPositions, perturbation, rho, phase, cursorX, cursorY, pointerInside ? 0.11 : 0.025);
    primaryLine.geometry.attributes.position.needsUpdate = true;
    perturbedLine.geometry.attributes.position.needsUpdate = true;

    copyTracerPositions(primaryPositions, primaryTracerPositions, elapsed, 1);
    copyTracerPositions(perturbedPositions, perturbedTracerPositions, elapsed, 1);
    primaryTracers.geometry.attributes.position.needsUpdate = true;
    perturbedTracers.geometry.attributes.position.needsUpdate = true;

    let separation = 0;
    const separationSamples = mobile ? 12 : 24;
    for (let i = sampleCount - separationSamples; i < sampleCount; i += 1) {
      const index = i * 3;
      const dx = primaryPositions[index] - perturbedPositions[index];
      const dy = primaryPositions[index + 1] - perturbedPositions[index + 1];
      const dz = primaryPositions[index + 2] - perturbedPositions[index + 2];
      separation += Math.sqrt(dx * dx + dy * dy + dz * dz);
    }
    separation /= separationSamples;
    if (deltaValue) deltaValue.textContent = perturbation.toFixed(4);
    if (separationValue) separationValue.textContent = separation.toFixed(2);

    updateInfluenceRing(cursorX, cursorY, elapsed);

    // Depth layers: near curve reacts most, points moderately, grid least.
    primaryLine.position.x = pointerCurrent.x * 0.22 * motionScale;
    primaryLine.position.y = pointerCurrent.y * 0.14 * motionScale;
    perturbedLine.position.x = pointerCurrent.x * 0.13 * motionScale;
    perturbedLine.position.y = pointerCurrent.y * 0.08 * motionScale;
    primaryTracers.position.copy(primaryLine.position);
    primaryTracers.position.z = 0.32;
    perturbedTracers.position.copy(perturbedLine.position);
    perturbedTracers.position.z = 0.14;
    fieldParticles.position.x = pointerCurrent.x * 0.1 * motionScale;
    fieldParticles.position.y = pointerCurrent.y * 0.07 * motionScale;
    fieldParticles.rotation.z = elapsed * 0.007 * motionScale;
    grid.position.x = pointerCurrent.x * 0.025 * motionScale;
    grid.position.y = pointerCurrent.y * 0.018 * motionScale;
    graph.rotation.y = pointerCurrent.x * 0.035 * motionScale;
    graph.rotation.x = -pointerCurrent.y * 0.025 * motionScale;

    // The older ambient ribbon remains autonomous and sits behind the graph.
    ambientStructure.rotation.x = -0.34 + Math.sin(elapsed * 0.12) * 0.05 * motionScale + pointerCurrent.y * 0.04 * motionScale;
    ambientStructure.rotation.y = elapsed * 0.038 * motionScale + pointerCurrent.x * 0.075 * motionScale;
    ambientStructure.rotation.z = 0.18 + Math.cos(elapsed * 0.1) * 0.035 * motionScale;
    ambientStructure.position.x = (mobile ? 0.8 : 0.65) + pointerCurrent.x * 0.08 * motionScale;
    ambientStructure.position.y = (mobile ? 0.1 : -0.05) + pointerCurrent.y * 0.055 * motionScale;

    renderer.render(scene, camera);
    if (!document.hidden && !reducedMotion.matches) frameId = requestAnimationFrame(render);
  }

  function requestRender() {
    if (!frameId && !document.hidden) frameId = requestAnimationFrame(render);
  }

  function resize() {
    const bounds = visualPane.getBoundingClientRect();
    camera.aspect = bounds.width / bounds.height;
    camera.updateProjectionMatrix();
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, mobile ? 1.25 : 1.75));
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

  document.documentElement.classList.add("webgl-ready");
  requestRender();
}());
