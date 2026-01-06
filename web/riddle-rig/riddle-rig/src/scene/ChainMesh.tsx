import { Html, Line } from "@react-three/drei";
import type { Chain, Project } from "../core/model";
import { chainMidpoint, computeChainLinkCount, endpointLabel, resolveEndpointWorld } from "../core/selectors";

type Props = {
  project: Project;
  chain: Chain;
  selected: boolean;
  onSelect: () => void;
};

export function ChainMesh({ project, chain, selected, onSelect }: Props) {
  const a = resolveEndpointWorld(project, chain.a);
  const b = resolveEndpointWorld(project, chain.b);
  if (!a || !b) return null;

  const links = computeChainLinkCount(project, chain);
  const mid = chainMidpoint(project, chain);

  return (
    <group>
      <Line
        points={[a, b]}
        lineWidth={selected ? 2.5 : 1.5}
        dashed={false}
        onPointerDown={(e) => {
          e.stopPropagation();
          onSelect();
        }}
      />
      {mid && (
        <Html position={mid} center style={{ pointerEvents: "none" }}>
          <div
            style={{
              padding: "4px 6px",
              borderRadius: 8,
              border: "1px solid rgba(255,255,255,0.12)",
              background: "rgba(10,14,20,0.78)",
              color: "#e6edf3",
              fontSize: 11,
              whiteSpace: "nowrap",
              fontFamily:
                'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
            }}
          >
            {chain.id} • {links ?? "?"} links • {endpointLabel(chain.a)} → {endpointLabel(chain.b)}
          </div>
        </Html>
      )}
    </group>
  );
}
