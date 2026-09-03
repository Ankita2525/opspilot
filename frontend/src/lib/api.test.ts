import assert from "node:assert/strict";
import { test } from "node:test";

import { incidentStartPayload } from "./api.ts";

test("includes turnstile token only on the protected start payload", () => {
  assert.deepEqual(incidentStartPayload("checkout-db-pool-regression", "tok_1"), {
    scenario_id: "checkout-db-pool-regression",
    turnstile_token: "tok_1",
  });
  assert.deepEqual(incidentStartPayload("checkout-db-pool-regression", null), {
    scenario_id: "checkout-db-pool-regression",
  });
});
