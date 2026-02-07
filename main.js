// BASIC SETUP
const canvas = document.getElementById("webgl");
const scene = new THREE.Scene();

const camera = new THREE.PerspectiveCamera(
  35,
  window.innerWidth / window.innerHeight,
  0.1,
  100
);
camera.position.z = 5;

const renderer = new THREE.WebGLRenderer({
  canvas,
  alpha: true,
  antialias: true
});
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

// LIGHTING (STUDIO)
const keyLight = new THREE.DirectionalLight(0xffffff, 1.1);
keyLight.position.set(2, 2, 3);
scene.add(keyLight);

const fillLight = new THREE.DirectionalLight(0xffffff, 0.4);
fillLight.position.set(-2, 1, 2);
scene.add(fillLight);

// FABRIC PLANE
const geometry = new THREE.PlaneGeometry(3, 4, 40, 60);

const material = new THREE.MeshStandardMaterial({
  color: 0xf5f5f4,
  roughness: 0.65,
  metalness: 0.05
});

const fabric = new THREE.Mesh(geometry, material);
scene.add(fabric);

// SEAM LINE
const seamPoints = [];
for (let i = -2; i <= 2; i += 0.1) {
  seamPoints.push(new THREE.Vector3(
    Math.sin(i) * 0.2,
    i,
    0.01
  ));
}

const seamGeometry = new THREE.BufferGeometry().setFromPoints(seamPoints);
const seamMaterial = new THREE.LineDashedMaterial({
  color: 0x111111,
  dashSize: 0.1,
  gapSize: 0.15
});

const seam = new THREE.Line(seamGeometry, seamMaterial);
seam.computeLineDistances();
scene.add(seam);

// INTERACTION
let mouseX = 0;
let mouseY = 0;

window.addEventListener("mousemove", e => {
  mouseX = (e.clientX / window.innerWidth - 0.5) * 0.3;
  mouseY = (e.clientY / window.innerHeight - 0.5) * 0.3;
});

// SCROLL TENSION
window.addEventListener("scroll", () => {
  const progress = window.scrollY /
    (document.body.scrollHeight - window.innerHeight);
  seamMaterial.dashSize = 0.05 + progress * 0.15;
});

// ANIMATE
function animate() {
  fabric.rotation.y += (mouseX - fabric.rotation.y) * 0.05;
  fabric.rotation.x += (mouseY - fabric.rotation.x) * 0.05;

  renderer.render(scene, camera);
  requestAnimationFrame(animate);
}

animate();

// RESIZE
window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});
