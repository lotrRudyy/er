import { Grid } from "@react-three/drei";

type Props = {
  size: number;
  divisions: number;
};

export function GridPlanes({ size, divisions }: Props) {
  const cellSize = size / divisions;

  return (
    <group>
      {/* XY plane (Z=0) */}
      <group rotation={[Math.PI / 2, 0, 0]}>
        <Grid args={[size, size]} cellSize={cellSize} infiniteGrid={false} />
      </group>

      {/* XZ plane (Y=0) */}
      <Grid args={[size, size]} cellSize={cellSize} infiniteGrid={false} />

      {/* YZ plane (X=0) */}
      <group rotation={[0, Math.PI / 2, 0]}>
        <Grid args={[size, size]} cellSize={cellSize} infiniteGrid={false} />
      </group>
    </group>
  );
}
