import type { Disk, HoleId, Project } from "../core/model";
import type { Object3D } from "three";

type Props = {
  disk: Disk;
  units: Project["units"];
  selected: boolean;
  selectedHoleId: HoleId | null;
  onSelectDisk: () => void;
  onSelectHole: (holeId: HoleId) => void;
  registerObject: (id: string, obj: Object3D | null) => void;
};

function kindToColor(kind: Disk["kind"]): string {
  switch (kind) {
    case "SQUARE":
      return "#6ee7ff";
    case "TRIANGLE":
      return "#a7f3d0";
    case "CIRCLE":
      return "#fbcfe8";
    default: {
      const _exhaustive: never = kind;
      return "#ffffff";
    }
  }
}

export function DiskMesh({
  disk,
  units,
  selected,
  selectedHoleId,
  onSelectDisk,
  onSelectHole,
  registerObject,
}: Props) {
  const radiusM = (units.diskDiameterMm / 1000) / 2;
  const thicknessM = units.diskThicknessMm / 1000;
  const holeIds: HoleId[] = ["H1", "H2", "H3"];

  const baseColor = kindToColor(disk.kind);

  return (
    <group
      ref={(obj) => registerObject(disk.id, obj)}
      position={disk.transform.position}
      rotation={disk.transform.rotation}
    >
      {/* Disk body (click target) */}
      <mesh
        onPointerDown={(e) => {
          e.stopPropagation();
          onSelectDisk();
        }}
      >
        <cylinderGeometry args={[radiusM, radiusM, thicknessM, 48]} />
        <meshStandardMaterial
          color={baseColor}
          roughness={0.75}
          metalness={0.0}
          emissive={selected ? "#2dd4bf" : "#000000"}
          emissiveIntensity={selected ? 0.25 : 0.0}
        />
      </mesh>

      {/* Center marker */}
      <mesh position={[0, 0, thicknessM / 2 + 0.002]}>
        <circleGeometry args={[radiusM * 0.12, 24]} />
        <meshStandardMaterial color="#111827" />
      </mesh>

      {/* Holes */}
      {holeIds.map((hid) => {
        const hp = disk.holes[hid].localPosition;
        const isHoleSelected = selected && selectedHoleId === hid;
        return (
          <mesh
            key={hid}
            position={[hp[0], hp[1], thicknessM / 2 + 0.01]}
            onPointerDown={(e) => {
              e.stopPropagation();
              onSelectHole(hid);
            }}
          >
            <sphereGeometry args={[radiusM * 0.05, 16, 16]} />
            <meshStandardMaterial
              color={isHoleSelected ? "#2dd4bf" : "#0b0f14"}
              roughness={0.3}
              metalness={0.0}
              emissive={isHoleSelected ? "#2dd4bf" : "#000000"}
              emissiveIntensity={isHoleSelected ? 0.25 : 0.0}
            />
          </mesh>
        );
      })}
    </group>
  );
}
