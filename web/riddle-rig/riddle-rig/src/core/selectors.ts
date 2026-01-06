import { Euler, Vector3 } from "three";
import type { Chain, ChainEndpoint, Project, Vec3 } from "./model";

export function vecDistance(a: Vec3, b: Vec3): number {
  const dx = a[0] - b[0];
  const dy = a[1] - b[1];
  const dz = a[2] - b[2];
  return Math.sqrt(dx * dx + dy * dy + dz * dz);
}

export function endpointLabel(ep: ChainEndpoint): string {
  if (ep.type === "ANCHOR") return ep.anchorId;
  return `${ep.diskId}:${ep.holeId}`;
}

export function resolveEndpointWorld(project: Project, ep: ChainEndpoint): Vec3 | null {
  if (ep.type === "ANCHOR") {
    const a = project.anchors[ep.anchorId];
    return a ? a.position : null;
  }

  const d = project.disks[ep.diskId];
  if (!d) return null;
  const h = d.holes[ep.holeId];
  if (!h) return null;

  const local = new Vector3(h.localPosition[0], h.localPosition[1], h.localPosition[2]);
  const rot = new Euler(d.transform.rotation[0], d.transform.rotation[1], d.transform.rotation[2], "XYZ");
  local.applyEuler(rot);

  const world = local.add(new Vector3(d.transform.position[0], d.transform.position[1], d.transform.position[2]));
  return [world.x, world.y, world.z];
}

export function computeChainLinkCount(project: Project, chain: Chain): number | null {
  if (typeof chain.linkCountOverride === "number") return chain.linkCountOverride;

  const a = resolveEndpointWorld(project, chain.a);
  const b = resolveEndpointWorld(project, chain.b);
  if (!a || !b) return null;

  const d = vecDistance(a, b); // meters
  const linkLenMm = project.units.linkLengthMm;
  if (linkLenMm <= 0) return null;

  const links = Math.max(1, Math.round((d * 1000) / linkLenMm));
  return links;
}

export function chainMidpoint(project: Project, chain: Chain): Vec3 | null {
  const a = resolveEndpointWorld(project, chain.a);
  const b = resolveEndpointWorld(project, chain.b);
  if (!a || !b) return null;
  return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2];
}
