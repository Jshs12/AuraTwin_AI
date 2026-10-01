import test from "node:test";
import assert from "node:assert/strict";
import { defaultZoneSelection } from "../src/utils/zoneSelection.ts";

const zones = [{ zone_id: "zone-a" }, { zone_id: "zone-b" }];

test("zones view selects the first authorized zone when none is selected", () => {
  assert.equal(defaultZoneSelection(zones, null), "zone-a");
});

test("zones view preserves a selected zone and repairs a removed selection", () => {
  assert.equal(defaultZoneSelection(zones, "zone-b"), "zone-b");
  assert.equal(defaultZoneSelection([zones[1]], "zone-a"), "zone-b");
  assert.equal(defaultZoneSelection([], "zone-a"), null);
});
