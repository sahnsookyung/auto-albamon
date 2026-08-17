import assert from "node:assert/strict";
import test from "node:test";

import { clusterProjectedPoints } from "../web/point-layout.mjs";

test("overlapping accessible hit targets become one deterministic cluster", () => {
  const points = [{ x: 10, y: 10, id: "a" }, { x: 11, y: 12, id: "b" }, { x: 80, y: 80, id: "c" }];
  const clusters = clusterProjectedPoints(points, (point) => point, 26);

  assert.equal(clusters.length, 2);
  assert.deepEqual(clusters[0].points.map((point) => point.id), ["a", "b"]);
  assert.equal(clusters[0].x, 10.5);
  assert.equal(clusters[0].y, 11);
  assert.deepEqual(clusters[1].points.map((point) => point.id), ["c"]);
});

test("cluster centroids never leave overlapping controls after a merge", () => {
  const points = [
    { id: "a", x: 0, y: 0 },
    { id: "b", x: 20, y: 0 },
    { id: "c", x: 40, y: 0 },
    { id: "d", x: 60, y: 0 },
  ];
  const clusters = clusterProjectedPoints(points, (point) => point, 26);

  for (let left = 0; left < clusters.length; left += 1) {
    for (let right = left + 1; right < clusters.length; right += 1) {
      assert.ok(
        Math.hypot(clusters[left].x - clusters[right].x, clusters[left].y - clusters[right].y) >= 26,
      );
    }
  }
  assert.deepEqual(
    clusters.flatMap((cluster) => cluster.points.map((point) => point.id)).sort(),
    ["a", "b", "c", "d"],
  );
});
