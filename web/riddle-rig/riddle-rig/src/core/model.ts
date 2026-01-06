export type Vec3 = [number, number, number];

export type DiskKind = "SQUARE" | "TRIANGLE" | "CIRCLE";

export type DiskId = `S${number}` | `T${number}` | `C${number}`;
export type AnchorId = "A1" | "A2" | "A3" | "A4";
export type ChainId = `CH${number}`;

export type HoleId = "H1" | "H2" | "H3";

export type Transform = {
  position: Vec3;
  rotation: Vec3; // Euler radians (XYZ)
};

export type Hole = {
  localPosition: Vec3; // relative to disk center (meters)
};

export type Disk = {
  id: DiskId;
  kind: DiskKind;
  transform: Transform;
  holes: Record<HoleId, Hole>;
  rotationLocked: boolean;
};

export type Anchor = {
  id: AnchorId;
  position: Vec3;
};

export type ChainEndpoint =
  | { type: "ANCHOR"; anchorId: AnchorId }
  | { type: "HOLE"; diskId: DiskId; holeId: HoleId };

export type Chain = {
  id: ChainId;
  a: ChainEndpoint;
  b: ChainEndpoint;
  linkCountOverride?: number;
};

export type Selection =
  | null
  | { type: "DISK"; diskId: DiskId }
  | { type: "HOLE"; diskId: DiskId; holeId: HoleId }
  | { type: "ANCHOR"; anchorId: AnchorId }
  | { type: "CHAIN"; chainId: ChainId };

export type GizmoMode = "TRANSLATE" | "ROTATE";

export type ConnectState = {
  enabled: boolean;
  pending: ChainEndpoint | null;
};

export type Project = {
  units: {
    worldUnit: "m";
    diskDiameterMm: number;
    diskThicknessMm: number;
    linkLengthMm: number;
  };
  disks: Record<DiskId, Disk>;
  anchors: Record<AnchorId, Anchor>;
  chains: Record<ChainId, Chain>;
  nextChainIndex: number;

  selection: Selection;
  gizmoMode: GizmoMode;
  connect: ConnectState;
};

function holeDefaults(diskRadiusM: number): Record<HoleId, Hole> {
  // Three holes in a triangle pattern. Editable later.
  const r = diskRadiusM * 0.65;
  const a0 = 0;
  const a1 = (2 * Math.PI) / 3;
  const a2 = (4 * Math.PI) / 3;

  return {
    H1: { localPosition: [r * Math.cos(a0), r * Math.sin(a0), 0] },
    H2: { localPosition: [r * Math.cos(a1), r * Math.sin(a1), 0] },
    H3: { localPosition: [r * Math.cos(a2), r * Math.sin(a2), 0] },
  };
}

function makeDisk(id: DiskId, kind: DiskKind, position: Vec3): Disk {
  const diskDiameterMm = 220;
  const radiusM = (diskDiameterMm / 1000) / 2;
  return {
    id,
    kind,
    transform: { position, rotation: [0, 0, 0] },
    holes: holeDefaults(radiusM),
    rotationLocked: false,
  };
}

export function makeDefaultProject(): Project {
  const diskDiameterMm = 220;
  const diskThicknessMm = 30;
  const radiusM = (diskDiameterMm / 1000) / 2;

  const spacing = radiusM * 2.6;
  const startX = -spacing * 4;
  const rowY = { S: 1.2, T: 0.0, C: -1.2 };

  const disks: Record<DiskId, Disk> = {} as Record<DiskId, Disk>;

  for (let i = 1; i <= 9; i++) {
    const x = startX + (i - 1) * spacing;
    disks[`S${i}`] = makeDisk(`S${i}`, "SQUARE", [x, rowY.S, 0.2]);
    disks[`T${i}`] = makeDisk(`T${i}`, "TRIANGLE", [x, rowY.T, 0.2]);
    disks[`C${i}`] = makeDisk(`C${i}`, "CIRCLE", [x, rowY.C, 0.2]);
  }

  const anchors: Record<AnchorId, Anchor> = {
    A1: { id: "A1", position: [-0.8, 0.8, 2.2] },
    A2: { id: "A2", position: [0.8, 0.8, 2.2] },
    A3: { id: "A3", position: [-0.8, -0.8, 2.2] },
    A4: { id: "A4", position: [0.8, -0.8, 2.2] },
  };

  return {
    units: { worldUnit: "m", diskDiameterMm, diskThicknessMm, linkLengthMm: 18 },
    disks,
    anchors,
    chains: {},
    nextChainIndex: 1,
    selection: null,
    gizmoMode: "TRANSLATE",
    connect: { enabled: false, pending: null },
  };
}
