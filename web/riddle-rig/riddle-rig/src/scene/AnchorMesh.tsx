import type { Anchor } from "../core/model";
import type { Object3D } from "three";

type Props = {
  anchor: Anchor;
  selected: boolean;
  onSelect: () => void;
  registerObject: (id: string, obj: Object3D | null) => void;
};

export function AnchorMesh({ anchor, selected, onSelect, registerObject }: Props) {
  return (
    <group ref={(obj) => registerObject(anchor.id, obj)} position={anchor.position}>
      <mesh
        onPointerDown={(e) => {
          e.stopPropagation();
          onSelect();
        }}
      >
        <sphereGeometry args={[0.035, 16, 16]} />
        <meshStandardMaterial
          color="#ffd166"
          roughness={0.4}
          emissive={selected ? "#fbbf24" : "#000000"}
          emissiveIntensity={selected ? 0.35 : 0.0}
        />
      </mesh>
      <mesh position={[0, 0, -0.05]}>
        <cylinderGeometry args={[0.006, 0.006, 0.1, 12]} />
        <meshStandardMaterial color="#ffd166" roughness={0.4} />
      </mesh>
    </group>
  );
}
