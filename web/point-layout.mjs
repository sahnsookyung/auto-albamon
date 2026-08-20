/** Maximum SVG viewBox distance that still reads as one plotted position. */
export const PARETO_SHARED_POSITION_DISTANCE = 4;

/** Group chart points whose accessible hit targets would overlap. */
export function clusterProjectedPoints(points, project, minimumDistance = 26) {
  const centroid = (clusterPoints) => ({
    x: clusterPoints.reduce((sum, point) => sum + project(point).x, 0) / clusterPoints.length,
    y: clusterPoints.reduce((sum, point) => sum + project(point).y, 0) / clusterPoints.length,
  });
  const clusters = (points || []).map((point) => ({ ...project(point), points: [point] }));

  // Keep merging the closest overlapping targets. A single insertion pass can
  // leave two centroids closer than the hit-target diameter after either
  // centroid moves, which makes the later DOM button steal clicks from the
  // earlier one even though both controls have distinct accessible names.
  while (true) {
    let closest = null;
    for (let left = 0; left < clusters.length; left += 1) {
      for (let right = left + 1; right < clusters.length; right += 1) {
        const distance = Math.hypot(
          clusters[left].x - clusters[right].x,
          clusters[left].y - clusters[right].y,
        );
        if (distance < minimumDistance && (!closest || distance < closest.distance)) {
          closest = { left, right, distance };
        }
      }
    }
    if (!closest) break;
    const mergedPoints = [
      ...clusters[closest.left].points,
      ...clusters[closest.right].points,
    ];
    clusters[closest.left] = { ...centroid(mergedPoints), points: mergedPoints };
    clusters.splice(closest.right, 1);
  }
  return clusters;
}

/**
 * Place accessible controls without moving the data they represent.
 * Grouped controls use the cluster centroid; ungrouped controls stay on each
 * point's exact projected coordinate while retaining the shared chooser.
 */
export function interactivePointControls(clusters, project, groupOverlaps = true) {
  return (clusters || []).flatMap((cluster, clusterIndex) => {
    const members = cluster.points || [];
    if (!members.length) return [];
    if (groupOverlaps || members.length === 1) {
      return [{
        x: cluster.x,
        y: cluster.y,
        point: members[0],
        members,
        clusterIndex,
      }];
    }
    return members.map((point) => ({
      ...project(point),
      point,
      members,
      clusterIndex,
    }));
  });
}
