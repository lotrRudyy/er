// pendulum_scene.js
import * as THREE from "https://unpkg.com/three@0.160.0/build/three.module.js";
import { OrbitControls } from "https://unpkg.com/three@0.160.0/examples/jsm/controls/OrbitControls.js";

// (rest of file unchanged from what I sent)

// ---------- Pendulum class ----------
class Pendulum {
  constructor(scene, { rodColor, bobColor }) {
    this.group = new THREE.Group();

    const rodMat = new THREE.MeshStandardMaterial({ color: rodColor });
    const rodGeo = new THREE.CylinderGeometry(0.003, 0.003, 0.4, 12);
    this.rod = new THREE.Mesh(rodGeo, rodMat);
    this.rod.geometry.translate(0, -0.2, 0); // pivot at top
    this.group.add(this.rod);

    const bobMat = new THREE.MeshStandardMaterial({
      color: bobColor,
      metalness: 0.3,
      roughness: 0.4
    });
    const bobGeo = new THREE.SphereGeometry(0.02, 24, 24);
    this.bob = new THREE.Mesh(bobGeo, bobMat);
    this.group.add(this.bob);

    const ringGeo = new THREE.TorusGeometry(0.01, 0.002, 8, 16);
    const ringMat = new THREE.MeshStandardMaterial({
      color: 0xfacc15,
      metalness: 0.5,
      roughness: 0.4
    });
    this.ring = new THREE.Mesh(ringGeo, ringMat);
    this.ring.rotation.x = Math.PI / 2;
    this.group.add(this.ring);

    scene.add(this.group);

    this.pivotIndex = 3;
    this.lenIndex = 3;
    this.pivotX = 0;
    this.length = 0.3;
    this.theta = 0;
    this.omega = 0;
  }

  setConfig({ pivotIndex, lenIndex, pivotBaseX, pivotStep, pivotY }) {
    this.pivotIndex = pivotIndex;
    this.lenIndex = lenIndex;

    const baseLen = 0.25;
    const stepLen = 0.05;
    this.length = baseLen + (this.lenIndex - 1) * stepLen;

    this.pivotX = pivotBaseX + (this.pivotIndex - 3) * pivotStep;
    this.group.position.set(this.pivotX, pivotY, 0);

    this.updateGeometry();
    this.applyAngle();
  }

  updateGeometry() {
    const L = this.length;
    this.rod.scale.set(1, L / 0.4, 1);
    this.bob.position.set(0, -L, 0);
    this.ring.position.set(0, -(L * 0.4), 0);
  }

  setAngle(theta) {
    this.theta = theta;
    this.applyAngle();
  }

  applyAngle() {
    this.group.rotation.z = this.theta;
  }

  step(dt, g = 9.81) {
    const L = this.length;
    const alpha = -(g / L) * Math.sin(this.theta);
    this.omega += alpha * dt;
    this.omega *= 0.999;       // tiny damping
    this.theta += this.omega * dt;
  }
}

// ---------- Scene controller ----------
class PendulumScene {
  constructor() {
    this.container = document.getElementById("three-container");

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x020617);

    this.camera = new THREE.PerspectiveCamera(
      45,
      window.innerWidth / window.innerHeight,
      0.01,
      100
    );
    this.camera.position.set(0.5, 0.35, 0.8);
    this.camera.lookAt(0, 0.3, 0);

    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(window.devicePixelRatio || 1);
    this.renderer.setSize(window.innerWidth, window.innerHeight);
    this.container.appendChild(this.renderer.domElement);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;

    this.buildStaticWorld();
    this.createPendulums();
    this.setupUI();

    this.running = false;
    this.latched = true;
    this.resetActive = false;
    this.resetT = 0;
    this.resetDur = 3.0; // 3 seconds
    this.resetStartLeft = 0;
    this.resetStartRight = 0;

    // fixed start angles (can later depend on "start index")
    this.resetTargetLeft = -0.6;
    this.resetTargetRight = 0.6;

    this.lastTime = null;

    window.addEventListener("resize", () => this.onResize());
    this.animate = this.animate.bind(this);
    requestAnimationFrame(this.animate);
  }

  buildStaticWorld() {
    const wallGap = 0.02;
    const wallWidth = 1.0;
    const wallHeight = 0.8;
    const wallThickness = 0.005;

    this.wallGap = wallGap;
    this.wallHeight = wallHeight;

    const wallMat = new THREE.MeshStandardMaterial({
      color: 0x0b1120,
      metalness: 0.1,
      roughness: 0.9,
      transparent: true,
      opacity: 0.35,
      depthWrite: false
    });

    const wallGeo = new THREE.BoxGeometry(wallWidth, wallHeight, wallThickness);
    const leftWall = new THREE.Mesh(wallGeo, wallMat);
    const rightWall = new THREE.Mesh(wallGeo, wallMat);
    leftWall.position.set(0, wallHeight / 2, -wallGap / 2);
    rightWall.position.set(0, wallHeight / 2, wallGap / 2);
    this.scene.add(leftWall, rightWall);

    const floorGeo = new THREE.PlaneGeometry(2, 2);
    const floorMat = new THREE.MeshStandardMaterial({
      color: 0x020617,
      metalness: 0,
      roughness: 1,
      side: THREE.DoubleSide
    });
    const floor = new THREE.Mesh(floorGeo, floorMat);
    floor.rotation.x = -Math.PI / 2;
    floor.position.y = 0;
    this.scene.add(floor);

    this.pivotY = 0.7;
    this.latchY = 0.5;
    this.pivotBaseXLeft = -0.3;
    this.pivotBaseXRight = 0.3;
    this.pivotStep = 0.06;

    // pivot holes
    const pivotHoleGeo = new THREE.SphereGeometry(0.005, 12, 12);
    const pivotHoleMat = new THREE.MeshStandardMaterial({ color: 0x9ca3af });

    for (let i = 0; i < 5; i++) {
      const xL = this.pivotBaseXLeft + (i - 2) * this.pivotStep;
      const xR = this.pivotBaseXRight + (i - 2) * this.pivotStep;
      const hL = new THREE.Mesh(pivotHoleGeo, pivotHoleMat);
      const hR = new THREE.Mesh(pivotHoleGeo, pivotHoleMat);
      hL.position.set(xL, this.pivotY, 0);
      hR.position.set(xR, this.pivotY, 0);
      this.scene.add(hL, hR);
    }

    const railGeo = new THREE.BoxGeometry(0.4, 0.01, wallGap);
    const railMat = new THREE.MeshStandardMaterial({ color: 0x374151 });
    const railLeft = new THREE.Mesh(railGeo, railMat);
    const railRight = new THREE.Mesh(railGeo, railMat);
    railLeft.position.set(this.pivotBaseXLeft, this.pivotY, 0);
    railRight.position.set(this.pivotBaseXRight, this.pivotY, 0);
    this.scene.add(railLeft, railRight);

    const pinGeo = new THREE.CylinderGeometry(0.004, 0.004, wallGap + wallThickness, 12);
    const pinMat = new THREE.MeshStandardMaterial({
      color: 0xf97316,
      metalness: 0.6,
      roughness: 0.2
    });
    const pin = new THREE.Mesh(pinGeo, pinMat);
    pin.rotation.x = Math.PI / 2;
    pin.position.set(0, this.latchY, 0);
    this.scene.add(pin);

    const laneMat = new THREE.LineBasicMaterial({ color: 0x4b5563 });
    for (let i = 0; i < 5; i++) {
      const laneXLeft = this.pivotBaseXLeft + (i - 2) * this.pivotStep;
      const laneXRight = this.pivotBaseXRight + (i - 2) * this.pivotStep;
      const geomL = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(laneXLeft, 0.1, 0),
        new THREE.Vector3(laneXLeft, wallHeight, 0)
      ]);
      const geomR = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(laneXRight, 0.1, 0),
        new THREE.Vector3(laneXRight, wallHeight, 0)
      ]);
      this.scene.add(new THREE.Line(geomL, laneMat));
      this.scene.add(new THREE.Line(geomR, laneMat));
    }

    const hemi = new THREE.HemisphereLight(0xffffff, 0x202030, 0.7);
    this.scene.add(hemi);
    const dir = new THREE.DirectionalLight(0xffffff, 0.6);
    dir.position.set(1, 2, 1);
    this.scene.add(dir);
  }

  createPendulums() {
    this.leftPend = new Pendulum(this.scene, {
      rodColor: 0x22d3ee,
      bobColor: 0x38bdf8
    });
    this.rightPend = new Pendulum(this.scene, {
      rodColor: 0x4ade80,
      bobColor: 0x22c55e
    });

    this.leftPend.setConfig({
      pivotIndex: 3,
      lenIndex: 3,
      pivotBaseX: this.pivotBaseXLeft,
      pivotStep: this.pivotStep,
      pivotY: this.pivotY
    });
    this.rightPend.setConfig({
      pivotIndex: 3,
      lenIndex: 2,
      pivotBaseX: this.pivotBaseXRight,
      pivotStep: this.pivotStep,
      pivotY: this.pivotY
    });
  }

  setupUI() {
    this.lpivot = document.getElementById("lpivot");
    this.llen = document.getElementById("llen");
    this.rpivot = document.getElementById("rpivot");
    this.rlen = document.getElementById("rlen");
    this.lpivotVal = document.getElementById("lpivotVal");
    this.llenVal = document.getElementById("llenVal");
    this.rpivotVal = document.getElementById("rpivotVal");
    this.rlenVal = document.getElementById("rlenVal");

    const refreshLabels = () => {
      this.lpivotVal.textContent = this.lpivot.value;
      this.llenVal.textContent = this.llen.value;
      this.rpivotVal.textContent = this.rpivot.value;
      this.rlenVal.textContent = this.rlen.value;
    };
    this.refreshLabels = refreshLabels;
    refreshLabels();

    this.lpivot.addEventListener("input", () => {
      this.leftPend.setConfig({
        pivotIndex: parseInt(this.lpivot.value, 10),
        lenIndex: parseInt(this.llen.value, 10),
        pivotBaseX: this.pivotBaseXLeft,
        pivotStep: this.pivotStep,
        pivotY: this.pivotY
      });
      refreshLabels();
    });
    this.llen.addEventListener("input", () => {
      this.leftPend.setConfig({
        pivotIndex: parseInt(this.lpivot.value, 10),
        lenIndex: parseInt(this.llen.value, 10),
        pivotBaseX: this.pivotBaseXLeft,
        pivotStep: this.pivotStep,
        pivotY: this.pivotY
      });
      refreshLabels();
    });
    this.rpivot.addEventListener("input", () => {
      this.rightPend.setConfig({
        pivotIndex: parseInt(this.rpivot.value, 10),
        lenIndex: parseInt(this.rlen.value, 10),
        pivotBaseX: this.pivotBaseXRight,
        pivotStep: this.pivotStep,
        pivotY: this.pivotY
      });
      refreshLabels();
    });
    this.rlen.addEventListener("input", () => {
      this.rightPend.setConfig({
        pivotIndex: parseInt(this.rpivot.value, 10),
        lenIndex: parseInt(this.rlen.value, 10),
        pivotBaseX: this.pivotBaseXRight,
        pivotStep: this.pivotStep,
        pivotY: this.pivotY
      });
      refreshLabels();
    });

    document.getElementById("btnReset").addEventListener("click", () => {
      // Start 3s reset: pull back along rope then roll into start angle
      this.resetStartLeft = this.leftPend.theta;
      this.resetStartRight = this.rightPend.theta;
      this.resetT = 0;
      this.resetActive = true;
      this.latched = true;
      this.running = false;
      this.lastTime = null;
    });

    document.getElementById("btnRelease").addEventListener("click", () => {
      this.latched = false;
      this.running = true;
      this.resetActive = false;
      this.lastTime = null;
    });

    document.getElementById("btnPause").addEventListener("click", () => {
      this.running = !this.running;
      if (this.running) this.lastTime = null;
    });

    document.getElementById("btnFront").addEventListener("click", () => {
      this.camera.position.set(0, 0.4, 0.6);
      this.camera.lookAt(0, 0.4, 0);
      this.controls.target.set(0, 0.4, 0);
      this.controls.update();
    });
  }

  easeOutCubic(x) {
    return 1 - Math.pow(1 - x, 3);
  }

  stepReset(dt) {
    if (!this.resetActive) return;

    this.resetT += dt;
    const tNorm = Math.min(this.resetT / this.resetDur, 1);

    // Two phases over the 3 seconds:
    // 0–1.5s: pull back to a high angle (as if you keep pulling the rope)
    // 1.5–3s: let it "roll" down into the fixed start angle.
    const half = 0.5;

    if (tNorm < half) {
      const u = this.easeOutCubic(tNorm / half);
      const pullLeft = -1.2;
      const pullRight = 1.2;
      this.leftPend.setAngle(
        this.resetStartLeft + (pullLeft - this.resetStartLeft) * u
      );
      this.rightPend.setAngle(
        this.resetStartRight + (pullRight - this.resetStartRight) * u
      );
    } else {
      const u = this.easeOutCubic((tNorm - half) / half);
      const pullLeft = -1.2;
      const pullRight = 1.2;
      this.leftPend.setAngle(
        pullLeft + (this.resetTargetLeft - pullLeft) * u
      );
      this.rightPend.setAngle(
        pullRight + (this.resetTargetRight - pullRight) * u
      );
    }

    if (tNorm >= 1) {
      this.resetActive = false;
      this.leftPend.setAngle(this.resetTargetLeft);
      this.rightPend.setAngle(this.resetTargetRight);
    }
  }

  stepPhysics(dt) {
    if (!this.running || this.latched) return;
    this.leftPend.step(dt);
    this.rightPend.step(dt);
  }

  animate(time) {
    requestAnimationFrame(this.animate);
    const t = time * 0.001;
    if (this.lastTime === null) this.lastTime = t;
    const dt = t - this.lastTime;
    this.lastTime = t;

    if (dt > 0 && dt < 0.1) {
      this.stepReset(dt);
      this.stepPhysics(dt);
    }

    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }

  onResize() {
    this.camera.aspect = window.innerWidth / window.innerHeight;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(window.innerWidth, window.innerHeight);
  }
}

// ---------- boot ----------
window.addEventListener("DOMContentLoaded", () => {
  new PendulumScene();
});
