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
